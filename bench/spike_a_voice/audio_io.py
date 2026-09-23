"""Lecture/ecriture WAV sans dependance externe (stdlib + numpy)."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as fh:
        sr = fh.getframerate()
        width = fh.getsampwidth()
        channels = fh.getnchannels()
        raw = fh.readframes(fh.getnframes())

    dtype = {1: np.uint8, 2: "<i2", 4: "<i4"}.get(width)
    if dtype is None:
        raise ValueError(f"{path.name}: largeur d'echantillon non geree ({width} octets)")

    data = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if width == 1:
        data = (data - 128.0) / 128.0
    else:
        data /= float(2 ** (8 * width - 1))
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, sr


def write_wav(path: Path, audio: np.ndarray, sr: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sr)
        fh.writeframes(pcm.tobytes())
