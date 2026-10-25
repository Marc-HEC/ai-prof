"""Détection de parole (WebRTC VAD) sur le flux micro PCM16 16 kHz envoyé par le navigateur."""
from __future__ import annotations

import io
import wave
from collections import deque

from .config import VADConfig

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_BYTES = SAMPLE_RATE * FRAME_MS // 1000 * 2  # 960 octets


def pcm_to_wav(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return bio.getvalue()


class SpeechSegmenter:
    """Rend des événements : ("start", None), ("barge_in", None), ("end", pcm)."""

    def __init__(self, cfg: VADConfig, is_speech=None):
        if is_speech is None:
            import webrtcvad
            vad = webrtcvad.Vad(cfg.aggressiveness)
            is_speech = lambda frame: vad.is_speech(frame, SAMPLE_RATE)  # noqa: E731
        self.is_speech = is_speech
        self.cfg = cfg
        self.pending = b""
        self.pre_roll: deque[bytes] = deque(maxlen=10)  # 300 ms avant le début
        self.speaking = False
        self.frames: list[bytes] = []
        self.voiced_run = 0
        self.silence_run = 0
        self.barge_sent = False

    def feed(self, chunk: bytes) -> list[tuple[str, bytes | None]]:
        events: list[tuple[str, bytes | None]] = []
        self.pending += chunk
        while len(self.pending) >= FRAME_BYTES:
            frame, self.pending = self.pending[:FRAME_BYTES], self.pending[FRAME_BYTES:]
            events.extend(self._frame(frame))
        return events

    def _frame(self, frame: bytes):
        voiced = self.is_speech(frame)
        if not self.speaking:
            self.pre_roll.append(frame)
            self.voiced_run = self.voiced_run + 1 if voiced else 0
            if self.voiced_run * FRAME_MS >= 90:
                self.speaking = True
                self.frames = list(self.pre_roll)
                self.silence_run = 0
                self.barge_sent = False
                return [("start", None)]
            return []

        self.frames.append(frame)
        if voiced:
            self.voiced_run += 1
            self.silence_run = 0
        else:
            self.silence_run += 1
        out = []
        if not self.barge_sent and self.voiced_run * FRAME_MS >= self.cfg.barge_in_ms:
            self.barge_sent = True
            out.append(("barge_in", None))
        if self.silence_run * FRAME_MS >= self.cfg.end_silence_ms:
            pcm = b"".join(self.frames)
            speech_ms = (len(self.frames) - self.silence_run) * FRAME_MS
            self._reset()
            if speech_ms >= self.cfg.min_speech_ms:
                out.append(("end", pcm))
            else:
                out.append(("noise", None))
        return out

    def _reset(self):
        self.speaking = False
        self.frames = []
        self.voiced_run = 0
        self.silence_run = 0
        self.pre_roll.clear()
