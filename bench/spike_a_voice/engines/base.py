"""Interface commune aux moteurs TTS compares dans l'essai A.

Cette interface est volontairement la meme que celle qui sera utilisee en
production (`backend/adapters/tts`). Le banc d'essai n'est donc pas du code
jetable : le moteur gagnant se branche derriere la meme abstraction.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

_REGISTRY: dict[str, type["TTSEngine"]] = {}


def register(cls: type["TTSEngine"]) -> type["TTSEngine"]:
    _REGISTRY[cls.name] = cls
    return cls


def available() -> list[str]:
    return sorted(_REGISTRY)


def build(name: str, **kwargs) -> "TTSEngine":
    if name not in _REGISTRY:
        raise KeyError(f"moteur inconnu : {name}. Disponibles : {', '.join(available())}")
    return _REGISTRY[name](**kwargs)


@dataclass
class VoicePrompt:
    """Identite vocale cible, telle que fournie par tools/prepare_dataset.py."""

    reference_wav: Path
    reference_text: str
    # Dataset complet, pour les moteurs qui finetunent au lieu de faire du zero-shot.
    dataset_dir: Path | None = None
    finetuned_model: Path | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class SynthResult:
    audio: np.ndarray  # mono float32 dans [-1, 1]
    sample_rate: int
    first_chunk_s: float  # latence jusqu'au premier echantillon disponible
    total_s: float  # duree totale de la synthese

    @property
    def duration_s(self) -> float:
        return len(self.audio) / self.sample_rate

    @property
    def rtf(self) -> float:
        """Real-time factor. En dessous de 1, le moteur genere plus vite que le temps reel."""
        return self.total_s / self.duration_s if self.duration_s else float("inf")


class TTSEngine(ABC):
    """Un moteur TTS candidat.

    `load` est separe de `__init__` pour que le banc puisse charger les moteurs
    un par un : trois modeles simultanement en VRAM ne tiennent pas sur 24 Go.
    """

    name: str = "abstract"
    #: renseigne a la main d'apres la licence du depot, affiche dans le rapport
    license: str = "inconnue"
    #: le moteur sait-il vraiment prononcer le francais
    supports_french: bool = True

    def __init__(self, device: str = "cuda", **kwargs) -> None:
        self.device = device
        self.options = kwargs
        self._loaded = False

    @abstractmethod
    def _load(self) -> None: ...

    @abstractmethod
    def _synth(self, text: str, prompt: VoicePrompt) -> tuple[np.ndarray, int]: ...

    def load(self) -> None:
        if not self._loaded:
            self._load()
            self._loaded = True

    def unload(self) -> None:
        self._loaded = False
        for attr in ("model", "_model", "pipeline"):
            if hasattr(self, attr):
                setattr(self, attr, None)
        try:
            import gc

            import torch

            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    def prepare(self, prompt: VoicePrompt) -> None:
        """Pre-calcule ce qui peut l'etre pour une voix donnee (embedding, prompt cache)."""

    def synth(self, text: str, prompt: VoicePrompt) -> SynthResult:
        self.load()
        started = time.perf_counter()
        audio, sr = self._synth(text, prompt)
        elapsed = time.perf_counter() - started
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
        if peak > 1.0:
            audio = audio / peak
        return SynthResult(audio=audio, sample_rate=sr, first_chunk_s=elapsed, total_s=elapsed)
