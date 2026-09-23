"""Synthese vocale francaise, sans GPU.

- piper : open source (GPL), tourne sur CPU plus vite que le temps reel.
          Voix par defaut fr_FR-siwis-medium. Pas de clonage.
- edge  : voix neuronales Microsoft via edge-tts. Bien plus naturelles,
          gratuites, mais service non officiel et en ligne.
- mock  : un babillage synthetique, pour tester le lip-sync sans modele.

Le clone vocal de l'essai A remplacera ce module sans toucher au reste :
l'interface est la meme, une phrase en entree, un fichier audio en sortie.
"""

from __future__ import annotations

import asyncio
import io
import threading
import wave
from pathlib import Path

import numpy as np

from config import ROOT_DIR

PIPER_DIR = ROOT_DIR / "models" / "piper"
_piper_voice = None
_piper_lock = threading.Lock()

# Emotion -> vitesse de parole Piper (length_scale : > 1 plus lent).
_PIPER_PACE = {"tristesse": 1.12, "tendresse": 1.08, "pensif": 1.08, "joie": 0.95,
               "surprise": 0.95, "taquin": 0.97}
# Emotion -> debit et hauteur edge-tts.
_EDGE_PROSODY = {"tristesse": ("-8%", "-4Hz"), "tendresse": ("-5%", "-2Hz"),
                 "pensif": ("-6%", "-2Hz"), "joie": ("+6%", "+6Hz"),
                 "surprise": ("+4%", "+8Hz"), "taquin": ("+3%", "+3Hz")}


def piper_paths(voice: str) -> tuple[Path, Path]:
    model = PIPER_DIR / f"{voice}.onnx"
    return model, Path(str(model) + ".json")


def _load_piper(voice: str):
    global _piper_voice
    with _piper_lock:
        if _piper_voice is None:
            from piper import PiperVoice

            model, cfg = piper_paths(voice)
            if not model.exists():
                raise RuntimeError(
                    f"Voix Piper absente : {model}. Lancez `python demo/start.py`, "
                    "qui la telecharge."
                )
            _piper_voice = PiperVoice.load(str(model), config_path=str(cfg))
    return _piper_voice


def _synth_piper(text: str, voice: str, emotion: str) -> bytes:
    pv = _load_piper(voice)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        if hasattr(pv, "synthesize_wav"):  # piper-tts >= 1.3
            try:
                from piper import SynthesisConfig

                cfg = SynthesisConfig(length_scale=_PIPER_PACE.get(emotion))
            except ImportError:
                cfg = None
            pv.synthesize_wav(text, wav, syn_config=cfg)
        else:  # piper-tts 1.2
            pv.synthesize(text, wav, length_scale=_PIPER_PACE.get(emotion))
    return buf.getvalue()


async def _synth_edge(text: str, voice: str, emotion: str) -> bytes:
    import edge_tts

    rate, pitch = _EDGE_PROSODY.get(emotion, ("+0%", "+0Hz"))
    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    out = bytearray()
    async for chunk in comm.stream():
        if chunk.get("type") == "audio":
            out.extend(chunk["data"])
    return bytes(out)


def _synth_mock(text: str) -> bytes:
    """Babillage : une voyelle par syllabe, formants qui bougent, pour le lip-sync."""
    sr = 22050
    syllables = max(2, len(text) // 3)
    parts = []
    rng = np.random.default_rng(len(text))
    formants = [(730, 1090), (270, 2290), (300, 870), (570, 840), (530, 1840)]
    for _ in range(syllables):
        dur = rng.uniform(0.11, 0.2)
        t = np.arange(int(sr * dur)) / sr
        f1, f2 = formants[rng.integers(len(formants))]
        f0 = 190 + 25 * np.sin(2 * np.pi * 0.7 * t)
        phase = 2 * np.pi * np.cumsum(f0) / sr
        sig = sum(np.sin(k * phase) * (np.exp(-((k * 200 - f1) / 250) ** 2)
                                         + 0.6 * np.exp(-((k * 200 - f2) / 350) ** 2))
                  for k in range(1, 20))
        env = np.sin(np.pi * t / dur) ** 0.6
        parts.append(sig * env)
        parts.append(np.zeros(int(sr * rng.uniform(0.01, 0.05))))
    audio = np.concatenate(parts)
    audio = (audio / (np.abs(audio).max() + 1e-9) * 0.5 * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(audio.tobytes())
    return buf.getvalue()


async def synthesize(engine: str, text: str, emotion: str, *, piper_voice: str,
                     edge_voice: str) -> tuple[bytes, str]:
    """Renvoie (audio, type MIME)."""
    if engine == "piper":
        return await asyncio.to_thread(_synth_piper, text, piper_voice, emotion), "audio/wav"
    if engine == "edge":
        return await _synth_edge(text, edge_voice, emotion), "audio/mpeg"
    if engine == "mock":
        return _synth_mock(text), "audio/wav"
    raise RuntimeError(f"TTS_ENGINE={engine!r} inconnu (piper, edge, mock)")


def warmup(engine: str, piper_voice: str) -> None:
    if engine == "piper":
        _synth_piper("Bonjour.", piper_voice, "neutre")
