"""Decoupe le flux de tokens du LLM en trois canaux.

    [emotion:joie] Bonne question ! <tableau>$f'(x) = 2x$</tableau> Tu vois ?

donne :

    ("emotion", "joie")              -> expression de l'avatar (decision D7)
    ("sentence", "Bonne question !") -> TTS, phrase par phrase
    ("board", "$f'(x) = 2x$")        -> tableau, jamais lu a voix haute
    ("sentence", "Tu vois ?")

Les tokens arrivent coupes n'importe ou, y compris au milieu d'une balise :
le parseur retient ce qui pourrait etre le debut d'une balise tant qu'il ne
peut pas trancher.
"""

from __future__ import annotations

import re

BOARD_OPEN = "<tableau"
BOARD_CLOSE = "</tableau>"
EMOTION_RE = re.compile(r"\[\s*[eé]motion\s*:\s*([a-zA-Zéèêàùç_-]+)\s*\]", re.IGNORECASE)
MAX_TAG_WAIT = 40  # au-dela, un "[" ou un "<" n'est pas une balise

EMOTIONS = {
    "neutre", "joie", "tendresse", "tristesse", "surprise",
    "agacement", "taquin", "pensif", "encourageant",
}

# Fin de phrase : ponctuation forte suivie d'un blanc. Le "..." et le "…" comptent.
SENTENCE_END = re.compile(r"(?<=[.!?…])[\"»)\]]*\s+")
SOFT_LIMIT = 200  # une phrase plus longue est coupee a la virgule, pour le TTFB


def clean_for_speech(text: str) -> str:
    """Retire ce qu'un TTS lirait mal : markdown, LaTeX residuel, balises."""
    text = re.sub(r"</?\w+[^>]*>", " ", text)
    text = EMOTION_RE.sub(" ", text)
    text = re.sub(r"[*_#`$\\|~^]", "", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_emotion(raw: str) -> str:
    e = raw.strip().lower()
    return e if e in EMOTIONS else "neutre"


class ResponseParser:
    def __init__(self) -> None:
        self.pending = ""
        self.in_board = False
        self.board_buf = ""
        self.speech_buf = ""
        self.raw = ""  # reponse complete, pour l'historique

    # -- API ---------------------------------------------------------------

    def feed(self, delta: str) -> list[tuple[str, str]]:
        self.raw += delta
        self.pending += delta
        events: list[tuple[str, str]] = []
        self._drain(events, final=False)
        return events

    def finish(self) -> list[tuple[str, str]]:
        events: list[tuple[str, str]] = []
        self._drain(events, final=True)
        if self.in_board:
            self.board_buf += self.pending
            self.pending = ""
            self._emit_board(events)
            self.in_board = False
        elif self.pending:
            self._speech(self.pending, events)
            self.pending = ""
        self._flush_sentences(events, final=True)
        return events

    def history_text(self) -> str:
        """Ce que l'on garde dans l'historique : sans tags d'emotion."""
        return EMOTION_RE.sub("", self.raw).strip()

    # -- machine a etats -----------------------------------------------------

    def _drain(self, events: list, final: bool) -> None:
        while self.pending:
            if self.in_board:
                idx = self.pending.lower().find(BOARD_CLOSE)
                if idx >= 0:
                    self.board_buf += self.pending[:idx]
                    self.pending = self.pending[idx + len(BOARD_CLOSE):]
                    self._emit_board(events)
                    self.in_board = False
                    continue
                keep = 0 if final else len(BOARD_CLOSE) - 1
                if len(self.pending) > keep:
                    cut = len(self.pending) - keep
                    self.board_buf += self.pending[:cut]
                    self.pending = self.pending[cut:]
                return

            m = re.search(r"[<\[]", self.pending)
            if m is None:
                self._speech(self.pending, events)
                self.pending = ""
                return
            if m.start() > 0:
                self._speech(self.pending[: m.start()], events)
                self.pending = self.pending[m.start():]

            rest = self.pending
            if rest[0] == "<":
                low = rest.lower()
                if low.startswith(BOARD_OPEN):
                    close = rest.find(">")
                    if close >= 0:
                        # Ce qui precede le tableau est dit avant qu'il n'apparaisse.
                        self._flush_sentences(events, final=True)
                        self.in_board = True
                        self.board_buf = ""
                        self.pending = rest[close + 1:]
                        continue
                    if not final and len(rest) < MAX_TAG_WAIT:
                        return
                elif low.startswith(BOARD_CLOSE):
                    self.pending = rest[len(BOARD_CLOSE):]  # fermeture orpheline
                    continue
                elif not final and (BOARD_OPEN.startswith(low) or BOARD_CLOSE.startswith(low)):
                    return  # debut possible de balise, on attend
                self._speech("<", events)
                self.pending = rest[1:]
                continue

            # rest[0] == "["
            em = EMOTION_RE.match(rest)
            if em:
                events.append(("emotion", normalize_emotion(em.group(1))))
                self.pending = rest[em.end():]
                continue
            if not final and "]" not in rest and len(rest) < MAX_TAG_WAIT:
                return
            self._speech("[", events)
            self.pending = rest[1:]

    def _emit_board(self, events: list) -> None:
        content = self.board_buf.strip()
        self.board_buf = ""
        if content:
            events.append(("board", content))

    def _speech(self, text: str, events: list) -> None:
        self.speech_buf += text
        self._flush_sentences(events, final=False)

    def _flush_sentences(self, events: list, final: bool) -> None:
        while True:
            m = SENTENCE_END.search(self.speech_buf)
            para = self.speech_buf.find("\n\n")
            if para >= 0 and (m is None or para < m.start()):
                cut, nxt = para, para + 2
            elif m is not None:
                cut, nxt = m.end(), m.end()
            elif len(self.speech_buf) > SOFT_LIMIT:
                comma = max(self.speech_buf.rfind(", ", 0, SOFT_LIMIT),
                            self.speech_buf.rfind("; ", 0, SOFT_LIMIT))
                if comma < 40:
                    break
                cut, nxt = comma + 1, comma + 2
            else:
                break
            self._emit_sentence(self.speech_buf[:cut], events)
            self.speech_buf = self.speech_buf[nxt:]
        if final and self.speech_buf.strip():
            self._emit_sentence(self.speech_buf, events)
            self.speech_buf = ""

    def _emit_sentence(self, text: str, events: list) -> None:
        clean = clean_for_speech(text)
        if re.search(r"\w", clean):
            events.append(("sentence", clean))
