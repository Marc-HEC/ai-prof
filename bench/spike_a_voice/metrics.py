"""Metriques objectives de l'essai A.

Deux mesures, qui repondent a deux questions distinctes :

- similarite locuteur (ECAPA-TDNN) : "est-ce que ca sonne comme la bonne personne"
- WER francais (faster-whisper) : "est-ce que le francais est correctement prononce"

Un moteur peut exceller sur la premiere et echouer sur la seconde : c'est
exactement le profil attendu d'un modele dont le frontend graphemes-phonemes
ignore le francais mais qui transfere correctement le timbre. Mesurer les deux
separement est ce qui permet de le diagnostiquer.

Calibration : la similarite cosinus brute n'est pas interpretable seule. Deux
enregistrements differents d'une meme personne ne donnent pas 1,0, mais plutot
0,75 a 0,90 selon le materiel et la piece. `enrollment_baseline` mesure ce
plafond sur le dataset reel, et le rapport exprime chaque moteur en pourcentage
de ce plafond plutot que dans l'absolu.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import numpy as np

EMBED_SR = 16_000


def resample(audio: np.ndarray, sr: int, target_sr: int) -> np.ndarray:
    if sr == target_sr:
        return audio.astype(np.float32)
    try:
        import torch
        import torchaudio.functional as F

        tensor = torch.from_numpy(audio.astype(np.float32)).unsqueeze(0)
        return F.resample(tensor, sr, target_sr).squeeze(0).numpy()
    except ImportError:
        from scipy.signal import resample_poly  # type: ignore[import-untyped]
        from math import gcd

        divisor = gcd(sr, target_sr)
        return resample_poly(audio, target_sr // divisor, sr // divisor).astype(np.float32)


class SpeakerSimilarity:
    """Embeddings locuteur ECAPA-TDNN, via SpeechBrain.

        pip install speechbrain
    """

    def __init__(self, device: str = "cuda", source: str = "speechbrain/spkrec-ecapa-voxceleb") -> None:
        self.device = device
        self.source = source
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return
        from speechbrain.inference.speaker import EncoderClassifier

        self._model = EncoderClassifier.from_hparams(
            source=self.source,
            savedir=str(Path.home() / ".cache" / "speechbrain" / "ecapa"),
            run_opts={"device": self.device},
        )

    def embed(self, audio: np.ndarray, sr: int) -> np.ndarray:
        import torch

        self.load()
        wav = resample(audio, sr, EMBED_SR)
        peak = float(np.max(np.abs(wav))) if len(wav) else 0.0
        if peak > 0:
            wav = wav / peak
        with torch.no_grad():
            tensor = torch.from_numpy(wav).unsqueeze(0).to(self.device)
            emb = self._model.encode_batch(tensor).squeeze().cpu().numpy()
        return emb / (np.linalg.norm(emb) + 1e-12)

    @staticmethod
    def cosine(a: np.ndarray, b: np.ndarray) -> float:
        return float(np.dot(a, b) / ((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-12))

    def enrollment(self, clips: list[tuple[np.ndarray, int]]) -> np.ndarray:
        """Embedding moyen de la vraie voix, calcule sur plusieurs segments."""
        embeddings = [self.embed(audio, sr) for audio, sr in clips]
        mean = np.mean(embeddings, axis=0)
        return mean / (np.linalg.norm(mean) + 1e-12)

    def enrollment_baseline(self, clips: list[tuple[np.ndarray, int]]) -> float:
        """Plafond atteignable : similarite de la vraie voix contre elle-meme.

        Aucun moteur ne devrait raisonnablement depasser cette valeur ; s'en
        approcher est le vrai objectif, pas un chiffre absolu comme "90 %".

        Le partage est fait un segment sur deux, pas en coupant la liste en son
        milieu. Les segments arrivent tries par SNR decroissant : une coupe
        mediane comparerait les meilleurs aux moins bons et sous-estimerait le
        plafond, ce qui gonflerait ensuite le "pourcentage du plafond" de tous
        les moteurs. L'alternance repartit la qualite equitablement.
        """
        if len(clips) < 4:
            return float("nan")
        embeddings = [self.embed(audio, sr) for audio, sr in clips]
        left = np.mean(embeddings[0::2], axis=0)
        right = np.mean(embeddings[1::2], axis=0)
        return self.cosine(left, right)


_PUNCT = re.compile(r"[^\w\s]", flags=re.UNICODE)
_SPACES = re.compile(r"\s+")


def normalise_fr(text: str, strip_accents: bool = True) -> str:
    """Normalisation avant WER.

    Les accents sont retires par defaut : la sortie de Whisper sur les accents
    n'est pas un indicateur fiable de la prononciation, et les conserver ajoute
    du bruit de mesure sans rien apprendre sur le TTS.
    """
    text = text.lower().replace("'", " ").replace("\u2019", " ").replace("-", " ")
    if strip_accents:
        text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    text = _PUNCT.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


class FrenchWER:
    """Re-transcription de la synthese puis WER contre le texte source.

        pip install faster-whisper jiwer
    """

    def __init__(self, device: str = "cuda", model_size: str = "large-v3") -> None:
        self.device = device
        self.model_size = model_size
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return
        from faster_whisper import WhisperModel

        compute_type = "float16" if self.device.startswith("cuda") else "int8"
        self._model = WhisperModel(self.model_size, device=self.device.split(":")[0], compute_type=compute_type)

    def transcribe(self, audio: np.ndarray, sr: int) -> str:
        self.load()
        wav = resample(audio, sr, EMBED_SR)
        segments, _ = self._model.transcribe(
            wav,
            language="fr",
            beam_size=5,
            # sans VAD : couper du silence fausserait le compte de mots
            vad_filter=False,
            condition_on_previous_text=False,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()

    @staticmethod
    def wer(reference: str, hypothesis: str) -> float:
        import jiwer

        ref = normalise_fr(reference)
        hyp = normalise_fr(hypothesis)
        if not ref:
            return float("nan")
        return float(jiwer.wer(ref, hyp))


@dataclass
class SentenceScore:
    text: str
    transcript: str
    wer: float
    similarity: float
    rtf: float
    duration_s: float
    synth_s: float


def summarise(scores: list[SentenceScore]) -> dict[str, float]:
    if not scores:
        return {}

    def stat(values: list[float], fn) -> float:
        clean = [v for v in values if not np.isnan(v)]
        return float(fn(clean)) if clean else float("nan")

    wers = [s.wer for s in scores]
    sims = [s.similarity for s in scores]
    return {
        "wer_mean": stat(wers, np.mean),
        "wer_median": stat(wers, np.median),
        "similarity_mean": stat(sims, np.mean),
        "similarity_p10": stat(sims, lambda v: np.percentile(v, 10)),
        "rtf_median": stat([s.rtf for s in scores], np.median),
        "synth_s_median": stat([s.synth_s for s in scores], np.median),
        "sentences": float(len(scores)),
    }
