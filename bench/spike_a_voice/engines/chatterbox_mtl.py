"""Chatterbox Multilingual v3 (Resemble AI).

Candidat principal : licence MIT, 0,5 B parametres, francais officiellement
supporte via `language_id="fr"`, clonage zero-shot depuis quelques secondes
de reference.

Reserve a documenter dans le rapport : les sorties portent un tatouage
numerique PerTh active par defaut, que l'editeur annonce comme resistant au
reencodage. Sans consequence pour un usage prive, mais c'est un fait.

    pip install chatterbox-tts
"""

from __future__ import annotations

import numpy as np

from .base import TTSEngine, VoicePrompt, register


@register
class ChatterboxEngine(TTSEngine):
    name = "chatterbox-multilingual-v3"
    license = "MIT"
    supports_french = True

    def _load(self) -> None:
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS

        self.model = ChatterboxMultilingualTTS.from_pretrained(device=self.device)

    def _synth(self, text: str, prompt: VoicePrompt) -> tuple[np.ndarray, int]:
        wav = self.model.generate(
            text,
            language_id="fr",
            audio_prompt_path=str(prompt.reference_wav),
            # exageration et cfg_weight pilotent l'expressivite ; les valeurs par
            # defaut du depot sont conservees pour que la comparaison reste juste
            **{k: v for k, v in self.options.items() if k in {"exaggeration", "cfg_weight", "temperature"}},
        )
        audio = wav.detach().cpu().numpy() if hasattr(wav, "detach") else np.asarray(wav)
        return audio.reshape(-1), int(self.model.sr)
