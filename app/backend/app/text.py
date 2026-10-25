"""Découpage du flux du LLM en phrases et extraction des balises d'action."""
from __future__ import annotations

import re

TAG_RE = re.compile(r"\[(action|emotion|camera)\s*:\s*([\w\-éèàç]+)\]", re.IGNORECASE)
ASTERISK_RE = re.compile(r"\*[^*]{0,200}\*")
SENTENCE_END = re.compile(r"([.!?…]+)(\s+|$)")


def extract_tags(text: str) -> tuple[str, list[dict]]:
    """Retire les balises [type:valeur] et le texte entre astérisques (non lu à voix haute)."""
    tags = [{"type": m.group(1).lower(), "value": m.group(2).lower()} for m in TAG_RE.finditer(text)]
    clean = TAG_RE.sub("", text)
    clean = ASTERISK_RE.sub("", clean)
    clean = re.sub(r"\s{2,}", " ", clean).strip()
    return clean, tags


class SentenceBuffer:
    """Accumule les jetons et rend les phrases complètes dès qu'elles sont prêtes.

    La toute première phrase peut être coupée sur une virgule pour que la voix
    démarre plus tôt (gain de latence).
    """

    def __init__(self, first_chunk_min: int = 25, min_len: int = 12):
        self.buf = ""
        self.first = True
        self.first_chunk_min = first_chunk_min
        self.min_len = min_len

    def push(self, token: str) -> list[str]:
        self.buf += token
        out: list[str] = []
        while True:
            m = next((m for m in SENTENCE_END.finditer(self.buf)
                      if m.group(2) and not self._inside_tag(m.end(1))
                      and (self.first or m.end(1) >= self.min_len)), None)
            if m:
                out.append(self.buf[:m.end(1)].strip())
                self.buf = self.buf[m.end():]
                self.first = False
                continue
            if self.first and len(self.buf) >= self.first_chunk_min:
                idx = self.buf.find(", ", 8)
                if idx != -1:
                    out.append(self.buf[: idx + 1].strip())
                    self.buf = self.buf[idx + 2:]
                    self.first = False
                    continue
            break
        return [s for s in out if s]

    def _inside_tag(self, pos: int) -> bool:
        return self.buf.rfind("[", 0, pos) > self.buf.rfind("]", 0, pos)

    def flush(self) -> list[str]:
        rest, self.buf = self.buf.strip(), ""
        return [rest] if rest else []
