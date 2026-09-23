"""Qwen3-TTS Base (Alibaba).

Candidat principal : licence Apache, francais officiellement supporte, clonage
a partir d'environ trois secondes de reference accompagnee de sa transcription.

Le prompt de clonage est calcule une fois via `create_voice_clone_prompt` puis
reutilise : sans cela le banc paierait l'extraction de features a chaque phrase
et la mesure de latence serait fausse.

    pip install qwen-tts
"""

from __future__ import annotations

import numpy as np

from .base import TTSEngine, VoicePrompt, register

# L'API attend le nom de langue en toutes lettres, pas un code ISO.
LANGUAGE = "French"


@register
class Qwen3TTSEngine(TTSEngine):
    name = "qwen3-tts-base"
    license = "Apache-2.0"
    supports_french = True

    def _load(self) -> None:
        import torch
        from qwen_tts import Qwen3TTSModel

        self._clone_prompt = None
        self._prompt_key: str | None = None
        self.model = Qwen3TTSModel.from_pretrained(
            self.options.get("model_id", "Qwen/Qwen3-TTS-12Hz-1.7B-Base"),
            device_map=self.device,
            dtype=torch.bfloat16,
            # `sdpa` par defaut plutot que `flash_attention_2` : ce dernier exige
            # nvcc pour se compiler, absent des images CUDA `runtime`. Le gain est
            # marginal sur des enonces courts, et un banc qui ne demarre pas parce
            # qu'une optimisation optionnelle manque ne sert a rien.
            attn_implementation=self.options.get("attn_implementation", "sdpa"),
        )

    def prepare(self, prompt: VoicePrompt) -> None:
        self.load()
        key = str(prompt.reference_wav)
        if self._prompt_key == key:
            return
        self._clone_prompt = self.model.create_voice_clone_prompt(
            ref_audio=str(prompt.reference_wav),
            ref_text=prompt.reference_text,
            x_vector_only_mode=False,
        )
        self._prompt_key = key

    def _synth(self, text: str, prompt: VoicePrompt) -> tuple[np.ndarray, int]:
        self.prepare(prompt)
        wavs, sr = self.model.generate_voice_clone(
            text=text,
            language=LANGUAGE,
            voice_clone_prompt=self._clone_prompt,
        )
        return np.asarray(wavs[0]).reshape(-1), int(sr)
