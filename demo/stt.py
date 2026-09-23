"""Transcription francaise, sans GPU.

- groq  : Whisper large-v3-turbo par l'API gratuite de Groq (le plus rapide).
- local : faster-whisper sur CPU, int8. "small" transcrit une phrase en une a
          deux secondes sur un i7 recent ; "base" est plus rapide, moins precis.
- mock  : renvoie une phrase fixe, pour tester l'interface sans micro ni modele.

Le navigateur envoie du WAV mono 16 kHz (encode cote client), donc aucun
ffmpeg n'est necessaire.
"""

from __future__ import annotations

import asyncio
import io
import os
import threading
import wave

import httpx
import numpy as np

_local_model = None
_local_lock = threading.Lock()


def wav_to_float32(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        width = w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError("WAV 16 bits attendu")
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        audio = audio.reshape(-1, ch).mean(axis=1)
    if sr != 16000:
        n = int(len(audio) * 16000 / sr)
        audio = np.interp(np.linspace(0, len(audio), n, endpoint=False),
                          np.arange(len(audio)), audio).astype(np.float32)
    return audio, 16000


def _load_local(model_name: str):
    global _local_model
    with _local_lock:
        if _local_model is None:
            from faster_whisper import WhisperModel

            threads = int(os.environ.get("STT_THREADS", "0")) or max(1, (os.cpu_count() or 4) // 2)
            _local_model = WhisperModel(model_name, device="cpu", compute_type="int8",
                                        cpu_threads=threads)
    return _local_model


def warmup(engine: str, model_name: str) -> None:
    """Charge le modele local au demarrage plutot qu'au premier tour."""
    if engine == "local":
        model = _load_local(model_name)
        silence = np.zeros(16000, dtype=np.float32)
        list(model.transcribe(silence, language="fr", beam_size=1)[0])


def _transcribe_local(data: bytes, model_name: str) -> str:
    audio, _ = wav_to_float32(data)
    model = _load_local(model_name)
    segments, _info = model.transcribe(
        audio, language="fr", beam_size=1, condition_on_previous_text=False,
        vad_filter=False,
    )
    return " ".join(s.text.strip() for s in segments).strip()


async def _transcribe_groq(data: bytes, model_name: str) -> str:
    key = os.environ.get("STT_API_KEY") or os.environ.get("GROQ_API_KEY", "")
    base = os.environ.get("STT_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{base}/audio/transcriptions",
            headers={"Authorization": f"Bearer {key}"},
            files={"file": ("tour.wav", data, "audio/wav")},
            data={"model": model_name, "language": "fr", "response_format": "json",
                  "temperature": "0"},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"STT HTTP {resp.status_code} : {resp.text[:300]}")
    return str(resp.json().get("text", "")).strip()


# Hallucinations classiques de Whisper sur du silence ou du bruit.
_HALLUCINATIONS = {
    "sous-titrage st' 501", "sous-titres realises para la communaute d'amara.org",
    "merci d'avoir regarde cette video", "sous-titrage societe radio-canada",
    "merci.", "...",
}


async def transcribe(engine: str, model_name: str, data: bytes) -> str:
    if engine == "mock":
        return os.environ.get("STT_MOCK_TEXT", "Tu peux m'expliquer les derivees ?")
    if engine == "groq":
        text = await _transcribe_groq(data, model_name)
    elif engine == "local":
        text = await asyncio.to_thread(_transcribe_local, data, model_name)
    else:
        raise RuntimeError(f"STT_ENGINE={engine!r} inconnu (groq, local, mock)")
    if text.lower().strip(" !.") in {h.strip(" !.") for h in _HALLUCINATIONS}:
        return ""
    return text
