"""Moteurs TTS candidats de l'essai A.

Chaque module s'enregistre au chargement. Les imports lourds (torch, modeles)
sont differes dans `_load`, ce qui permet de lister les moteurs disponibles sur
une machine sans GPU et de n'installer que les dependances du moteur teste.
"""

from __future__ import annotations

import importlib
import logging

from .base import TTSEngine, VoicePrompt, SynthResult, available, build, register

log = logging.getLogger(__name__)

for _module in ("chatterbox_mtl", "qwen3_tts", "gptsovits"):
    try:
        importlib.import_module(f"{__name__}.{_module}")
    except Exception as exc:  # noqa: BLE001 - un moteur absent ne doit pas casser le banc
        log.debug("moteur %s non enregistre : %s", _module, exc)

__all__ = ["TTSEngine", "VoicePrompt", "SynthResult", "available", "build", "register"]
