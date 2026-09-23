"""Tracage de la latence par etage.

Le cahier des charges initial demandait une "latence totale < X ms" sans jamais
definir X ni comment le mesurer. Un chiffre global ne sert d'ailleurs a rien pour
optimiser : savoir qu'un tour prend 1,4 s ne dit pas s'il faut changer de modele
STT, quantifier davantage le LLM, ou decouper le texte autrement avant le TTS.

Ce processeur mesure donc chaque etage separement :

    fin de parole utilisateur (VAD)
        -> transcription finale            latence STT
        -> premier token du LLM            TTFT
        -> premier echantillon audio TTS   TTFB du TTS
        -> premier son dans le casque      total, le chiffre qui compte

Identification des trames par nom de classe plutot que par import : Pipecat
reorganise regulierement ses modules, et une sonde qui casse a chaque montee de
version ne serait jamais utilisee.
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, asdict
from pathlib import Path

try:
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
except ImportError:  # permet d'importer le module pour l'analyse hors du pod
    FrameProcessor = object  # type: ignore[assignment,misc]
    FrameDirection = None  # type: ignore[assignment]


# Noms de trames observes. Plusieurs variantes par etage : les versions recentes
# de Pipecat ont renomme certaines trames, on accepte les deux orthographes.
MARKERS: dict[str, tuple[str, ...]] = {
    "user_started": ("UserStartedSpeakingFrame",),
    "user_stopped": ("UserStoppedSpeakingFrame",),
    "stt_final": ("TranscriptionFrame",),
    "stt_interim": ("InterimTranscriptionFrame",),
    "llm_start": ("LLMFullResponseStartFrame",),
    "llm_token": ("LLMTextFrame", "TextFrame"),
    "llm_end": ("LLMFullResponseEndFrame",),
    "tts_start": ("TTSStartedFrame",),
    "tts_audio": ("TTSAudioRawFrame",),
    "tts_end": ("TTSStoppedFrame",),
    "bot_started": ("BotStartedSpeakingFrame",),
    "bot_stopped": ("BotStoppedSpeakingFrame",),
    "interruption": ("InterruptionFrame", "StartInterruptionFrame", "BotInterruptionFrame"),
}

_LOOKUP = {name: stage for stage, names in MARKERS.items() for name in names}


@dataclass
class Turn:
    """Un tour de parole, du silence de l'utilisateur au premier son de l'avatar."""

    index: int
    user_stopped: float
    stt_final: float | None = None
    llm_first_token: float | None = None
    tts_first_audio: float | None = None
    bot_started: float | None = None
    transcript: str = ""
    interrupted_at: float | None = None
    bot_last_audio: float | None = None

    def _delta(self, a: float | None, b: float | None) -> float | None:
        return None if a is None or b is None else (a - b) * 1000.0

    @property
    def stt_ms(self) -> float | None:
        return self._delta(self.stt_final, self.user_stopped)

    @property
    def llm_ttft_ms(self) -> float | None:
        return self._delta(self.llm_first_token, self.stt_final)

    @property
    def tts_ttfb_ms(self) -> float | None:
        return self._delta(self.tts_first_audio, self.llm_first_token)

    @property
    def total_ms(self) -> float | None:
        """Le chiffre qui compte : silence de l'utilisateur au premier son emis."""
        return self._delta(self.bot_started or self.tts_first_audio, self.user_stopped)

    @property
    def barge_in_ms(self) -> float | None:
        """Delai entre le debut de parole de l'utilisateur et l'arret de l'avatar."""
        return self._delta(self.bot_last_audio, self.interrupted_at)

    def complete(self) -> bool:
        return self.total_ms is not None


class LatencyProbe(FrameProcessor):  # type: ignore[misc]
    """Sonde passive : observe et horodate, ne modifie ni ne retient aucune trame.

    Classe ordinaire et non dataclass : `FrameProcessor` doit etre initialise
    avant tout le reste, ce qu'un `__post_init__` ne garantit pas.
    """

    def __init__(self, label: str = "probe", verbose: bool = True, **kwargs) -> None:
        super().__init__(**kwargs)
        self.label = label
        self.verbose = verbose
        self.turns: list[Turn] = []
        self._current: Turn | None = None
        self._bot_speaking = False
        self._index = 0

    # -- collecte ------------------------------------------------------------
    def observe(self, frame_name: str, frame=None) -> None:
        stage = _LOOKUP.get(frame_name)
        if stage is None:
            return
        now = time.perf_counter()

        if stage == "user_started":
            # Parole pendant que l'avatar parle : c'est une interruption.
            if self._bot_speaking and self._current is not None:
                self._current.interrupted_at = now
        elif stage == "user_stopped":
            self._index += 1
            self._current = Turn(index=self._index, user_stopped=now)
            self.turns.append(self._current)
        elif self._current is None:
            return
        elif stage == "stt_final" and self._current.stt_final is None:
            self._current.stt_final = now
            self._current.transcript = getattr(frame, "text", "") or ""
        elif stage == "llm_token" and self._current.llm_first_token is None:
            self._current.llm_first_token = now
        elif stage == "tts_audio":
            if self._current.tts_first_audio is None:
                self._current.tts_first_audio = now
            self._current.bot_last_audio = now
        elif stage == "bot_started":
            self._bot_speaking = True
            if self._current.bot_started is None:
                self._current.bot_started = now
                if self.verbose:
                    self._print(self._current)
        elif stage == "bot_stopped":
            self._bot_speaking = False

    def _print(self, turn: Turn) -> None:
        def fmt(value: float | None) -> str:
            return f"{value:6.0f}" if value is not None else "     -"

        print(
            f"  tour {turn.index:2d}  STT {fmt(turn.stt_ms)}  LLM {fmt(turn.llm_ttft_ms)}  "
            f"TTS {fmt(turn.tts_ttfb_ms)}  TOTAL {fmt(turn.total_ms)} ms   "
            f"\"{turn.transcript[:42]}\""
        )

    async def process_frame(self, frame, direction) -> None:  # type: ignore[override]
        await super().process_frame(frame, direction)
        self.observe(type(frame).__name__, frame)
        await self.push_frame(frame, direction)

    # -- restitution ---------------------------------------------------------
    def summary(self) -> dict:
        done = [t for t in self.turns if t.complete()]

        def quantile(ordered: list[float], q: float) -> float:
            """Interpolation lineaire entre rangs.

            Une simple indexation par `int(q * n)` renvoie le maximum des que
            l'echantillon est petit : le p90 et le max deviennent le meme chiffre
            affiche dans deux colonnes, ce qui donne une fausse impression de
            mesure.
            """
            if len(ordered) == 1:
                return ordered[0]
            position = q * (len(ordered) - 1)
            low = int(position)
            high = min(low + 1, len(ordered) - 1)
            weight = position - low
            return ordered[low] * (1 - weight) + ordered[high] * weight

        def percentiles(values: list[float]) -> dict[str, float | int | bool]:
            if not values:
                return {}
            ordered = sorted(values)
            return {
                "p50": round(statistics.median(ordered), 1),
                "p90": round(quantile(ordered, 0.9), 1),
                "max": round(ordered[-1], 1),
                "n": len(ordered),
                # En dessous de dix mesures, le p90 repose sur deux points et
                # n'a pas de valeur statistique. Le rapport le signale.
                "p90_reliable": len(ordered) >= 10,
            }

        def collect(attr: str) -> list[float]:
            return [v for t in done if (v := getattr(t, attr)) is not None]

        barge = [v for t in self.turns if (v := t.barge_in_ms) is not None and v >= 0]
        return {
            "label": self.label,
            "turns": len(self.turns),
            "complete_turns": len(done),
            "stages_ms": {
                "stt": percentiles(collect("stt_ms")),
                "llm_ttft": percentiles(collect("llm_ttft_ms")),
                "tts_ttfb": percentiles(collect("tts_ttfb_ms")),
                "total": percentiles(collect("total_ms")),
            },
            "barge_in_ms": percentiles(barge),
        }

    def save(self, path: Path, context: dict | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "context": context or {},
            "summary": self.summary(),
            # Les transcriptions sont retirees : une trace de latence n'a aucune
            # raison de conserver le contenu des conversations.
            "turns": [{k: v for k, v in asdict(t).items() if k != "transcript"} for t in self.turns],
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
