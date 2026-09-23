"""Service TTS Pipecat adosse aux moteurs de l'essai A.

Le banc de l'essai A n'est pas du code jetable : le moteur qui y gagne se branche
ici sans etre reecrit, derriere la meme interface `TTSEngine`. C'est ce qui rend
la brique vocale reellement interchangeable, exigence centrale du cahier des
charges.

Trois points determinent la latence percue et meritent qu'on s'y arrete.

La synthese tourne dans un thread separe. Un appel de modele bloquant executé
directement dans la boucle asyncio gelerait aussi la reception du micro et la
detection d'interruption : l'avatar deviendrait sourd pendant qu'il parle.

L'audio est decoupe en trames courtes avant d'etre emis. Rendre un enonce entier
d'un bloc empeche Pipecat de jeter ce qui n'a pas encore ete joue quand
l'utilisateur coupe la parole, et le barge-in devient inoperant.

Le streaming s'arrete au niveau de la phrase. Pipecat decoupe la reponse du LLM
en phrases et appelle `run_tts` sur chacune, mais a l'interieur d'une phrase la
synthese est complete avant la premiere trame emise. Le TTFB mesure par l'essai B
correspond donc a la synthese d'une phrase entiere, pas au premier echantillon
d'un decodage incremental : c'est un plancher, pas une valeur a optimiser par un
reglage de chunk. Descendre en dessous supposerait un moteur capable de decodage
incremental reel, et le decoupage en phrases plus courtes est le seul levier
disponible avec les moteurs candidats.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncGenerator
from pathlib import Path

import numpy as np

from pipecat.frames.frames import ErrorFrame, Frame, TTSAudioRawFrame, TTSStartedFrame, TTSStoppedFrame
from pipecat.services.tts_service import TTSService

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "spike_a_voice"))

import engines as engine_registry  # noqa: E402
from metrics import resample  # noqa: E402

FRAME_MS = 20


class LocalCloneTTSService(TTSService):
    """Enveloppe Pipecat autour d'un `TTSEngine` de l'essai A."""

    def __init__(
        self,
        engine_name: str,
        prompt: engine_registry.VoicePrompt,
        *,
        sample_rate: int = 24_000,
        device: str = "cuda",
        **kwargs,
    ) -> None:
        super().__init__(sample_rate=sample_rate, **kwargs)
        self._engine = engine_registry.build(engine_name, device=device)
        self._prompt = prompt
        self._engine_name = engine_name
        self._warm = False

    def can_generate_metrics(self) -> bool:
        return True

    async def start(self, frame) -> None:
        await super().start(frame)
        await self._warmup()

    async def _warmup(self) -> None:
        """Premiere synthese a vide, hors mesure.

        Sans cela le premier tour de chaque session porte le cout d'allocation
        CUDA et de compilation des noyaux, ce qui gonfle la mesure de plusieurs
        secondes et n'a rien a voir avec la latence en regime etabli.
        """
        if self._warm:
            return
        await asyncio.to_thread(self._engine.load)
        await asyncio.to_thread(self._engine.prepare, self._prompt)
        await asyncio.to_thread(self._engine.synth, "Bonjour.", self._prompt)
        self._warm = True

    async def run_tts(self, text: str) -> AsyncGenerator[Frame, None]:
        text = text.strip()
        if not text:
            return

        await self.start_ttfb_metrics()
        yield TTSStartedFrame()

        try:
            result = await asyncio.to_thread(self._engine.synth, text, self._prompt)
        except Exception as exc:  # noqa: BLE001 - une phrase ratee ne doit pas tuer l'appel
            yield ErrorFrame(f"{self._engine_name}: {exc}")
            yield TTSStoppedFrame()
            return

        audio = resample(result.audio, result.sample_rate, self.sample_rate)
        pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()

        await self.stop_ttfb_metrics()

        stride = int(self.sample_rate * FRAME_MS / 1000) * 2  # 2 octets par echantillon
        for offset in range(0, len(pcm), stride):
            yield TTSAudioRawFrame(
                audio=pcm[offset : offset + stride],
                sample_rate=self.sample_rate,
                num_channels=1,
            )

        yield TTSStoppedFrame()
