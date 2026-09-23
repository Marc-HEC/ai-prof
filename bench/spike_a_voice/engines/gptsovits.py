"""GPT-SoVITS v4, via l'API HTTP locale `api_v2.py`.

Ce moteur est present comme TEMOIN, pas comme candidat.

Le prompt d'origine du projet faisait de GPT-SoVITS le coeur du clonage vocal
francais. Or son frontend texte ne connait que `zh`, `en`, `ja`, `ko`, `yue` :
il n'existe aucun module de conversion graphemes-phonemes francais, et les
modeles pre-entraines n'ont jamais vu de phoneme francais. Le timbre se
transfere depuis un audio de reference francais, mais le texte francais est
phonetise par un frontend d'une autre langue.

Le banc mesure donc l'ecart au lieu de le postuler : on s'attend a une
similarite locuteur correcte (le timbre passe) et a un WER francais degrade
(la prononciation ne passe pas). Si la mesure contredit cette attente, c'est
la mesure qui gagne.

Lancer le serveur en amont sur le pod :

    python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
"""

from __future__ import annotations

import io
import os
import wave

import numpy as np

from .base import TTSEngine, VoicePrompt, register

# Aucun code francais n'existe. `en` est le frontend en alphabet latin le plus
# proche ; c'est exactement la substitution que le banc met a l'epreuve.
TEXT_LANG_FALLBACK = "en"

# En conteneur, le serveur est un service voisin et non la boucle locale :
# 127.0.0.1 depuis le conteneur du banc designe le banc lui-meme. L'URL est donc
# fournie par l'environnement, et docker-compose la pointe sur `gptsovits`.
DEFAULT_URL = os.environ.get("GPTSOVITS_URL", "http://127.0.0.1:9880")


@register
class GPTSoVITSEngine(TTSEngine):
    name = "gpt-sovits-v4"
    license = "MIT (code) / voir depot pour les poids"
    supports_french = False

    def _load(self) -> None:
        import requests

        self._session = requests.Session()
        self.base_url = self.options.get("base_url", DEFAULT_URL).rstrip("/")
        try:
            self._session.get(f"{self.base_url}/", timeout=5)
        except Exception as exc:  # noqa: BLE001 - message actionnable plutot que trace
            raise RuntimeError(
                f"serveur GPT-SoVITS injoignable sur {self.base_url}. "
                "En conteneur, definir GPTSOVITS_URL=http://gptsovits:9880 et demarrer "
                "le profil spike-a-control. Hors conteneur, lancer api_v2.py. "
                "Sinon, retirer ce moteur de --engines."
            ) from exc

    def _synth(self, text: str, prompt: VoicePrompt) -> tuple[np.ndarray, int]:
        payload = {
            "text": text,
            "text_lang": self.options.get("text_lang", TEXT_LANG_FALLBACK),
            "ref_audio_path": str(prompt.reference_wav),
            "prompt_text": prompt.reference_text,
            "prompt_lang": self.options.get("prompt_lang", TEXT_LANG_FALLBACK),
            "text_split_method": "cut5",
            "media_type": "wav",
            "streaming_mode": 0,
            "parallel_infer": True,
            "speed_factor": 1.0,
        }
        response = self._session.post(f"{self.base_url}/tts", json=payload, timeout=180)
        if response.status_code != 200:
            raise RuntimeError(f"GPT-SoVITS a repondu {response.status_code}: {response.text[:300]}")

        with wave.open(io.BytesIO(response.content), "rb") as fh:
            sr = fh.getframerate()
            raw = fh.readframes(fh.getnframes())
            width = fh.getsampwidth()
        if width != 2:
            raise RuntimeError(f"largeur d'echantillon inattendue : {width} octets")
        audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        return audio, sr
