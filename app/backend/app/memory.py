"""Mémoire à long terme : souvenirs, faits, journal, état; avec oubli progressif.

Tout est stocké dans un fichier SQLite sur ton PC. Les embeddings sont calculés
en local (fastembed, ONNX, sur le CPU). Sans fastembed, on retombe sur une
recherche par mots-clés.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from pathlib import Path

from .config import MemoryConfig

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,              -- episode | fact | journal
    text TEXT NOT NULL,              -- version détaillée
    vague TEXT,                      -- version floue, utilisée une fois le souvenir estompé
    stage INTEGER NOT NULL DEFAULT 0,-- 0 détaillé, 1 flou
    importance REAL NOT NULL DEFAULT 0.5,
    created REAL NOT NULL,
    last_access REAL NOT NULL,
    access_count INTEGER NOT NULL DEFAULT 0,
    pinned INTEGER NOT NULL DEFAULT 0,
    embedding TEXT
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

WORD_RE = re.compile(r"\w{3,}", re.UNICODE)
DAY = 86400.0


class Embedder:
    def __init__(self, model: str):
        self.model = None
        try:
            from fastembed import TextEmbedding
            self.model = TextEmbedding(model_name=model)
        except Exception:  # fastembed absent ou modèle non téléchargé
            self.model = None

    def embed(self, text: str) -> list[float] | None:
        if self.model is None:
            return None
        vec = next(iter(self.model.embed([text])))
        return [float(x) for x in vec]


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def _overlap(a: str, b: str) -> float:
    wa, wb = set(WORD_RE.findall(a.lower())), set(WORD_RE.findall(b.lower()))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / math.sqrt(len(wa) * len(wb))


class Memory:
    def __init__(self, path: Path, cfg: MemoryConfig, embedder: Embedder | None = None,
                 clock=time.time):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.cfg = cfg
        self.embedder = embedder
        self.clock = clock

    # --- état clé/valeur -------------------------------------------------
    def get(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set(self, key: str, value) -> None:
        self.db.execute("INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, json.dumps(value, ensure_ascii=False)))
        self.db.commit()

    # --- écriture ----------------------------------------------------------
    def add(self, kind: str, text: str, vague: str | None = None, importance: float = 0.5,
            pinned: bool = False) -> int:
        now = self.clock()
        emb = self.embedder.embed(text) if self.embedder else None
        cur = self.db.execute(
            "INSERT INTO memories(kind, text, vague, importance, created, last_access, pinned, embedding)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (kind, text, vague, max(0.0, min(1.0, importance)), now, now, int(pinned),
             json.dumps(emb) if emb else None))
        self.db.commit()
        return cur.lastrowid

    def add_fact(self, text: str) -> None:
        """Les faits sur toi (prénom, proches, travail) sont protégés de l'oubli; on évite les doublons."""
        for row in self.db.execute("SELECT id, text FROM memories WHERE kind='fact'"):
            if _overlap(row["text"], text) > 0.8:
                self.db.execute("UPDATE memories SET text=?, last_access=? WHERE id=?",
                                (text, self.clock(), row["id"]))
                self.db.commit()
                return
        self.add("fact", text, importance=0.9, pinned=True)

    # --- lecture -----------------------------------------------------------
    def facts(self, limit: int = 30) -> list[str]:
        rows = self.db.execute("SELECT text FROM memories WHERE kind='fact' ORDER BY last_access DESC LIMIT ?",
                               (limit,))
        return [r["text"] for r in rows]

    def last_journal(self) -> str | None:
        row = self.db.execute("SELECT text FROM memories WHERE kind='journal' ORDER BY created DESC LIMIT 1").fetchone()
        return row["text"] if row else None

    def recall(self, query: str, k: int | None = None) -> list[str]:
        k = k or self.cfg.recall_k
        now = self.clock()
        qemb = self.embedder.embed(query) if self.embedder else None
        scored = []
        for r in self.db.execute("SELECT * FROM memories WHERE kind='episode'"):
            if qemb and r["embedding"]:
                sim = _cos(qemb, json.loads(r["embedding"]))
            else:
                sim = _overlap(query, r["text"])
            recency = math.exp(-(now - r["last_access"]) / (self.cfg.half_life_days * DAY))
            score = 0.6 * sim + 0.25 * r["importance"] + 0.15 * recency
            scored.append((score, sim, r))
        scored.sort(key=lambda t: t[0], reverse=True)
        out = []
        for score, sim, r in scored[:k]:
            if sim < 0.15 and score < 0.45:
                continue
            self.db.execute("UPDATE memories SET last_access=?, access_count=access_count+1 WHERE id=?",
                            (now, r["id"]))
            text = r["vague"] if r["stage"] == 1 and r["vague"] else r["text"]
            out.append(text)
        self.db.commit()
        return out

    # --- oubli -------------------------------------------------------------
    def strength(self, r: sqlite3.Row, now: float) -> float:
        age = (now - r["last_access"]) / DAY
        return r["importance"] * math.exp(-age * math.log(2) / self.cfg.half_life_days) \
            + 0.05 * math.log1p(r["access_count"])

    def decay(self) -> dict:
        """Souvenir faible -> version floue -> effacé. Les souvenirs épinglés et les faits restent."""
        now = self.clock()
        blurred = forgotten = 0
        for r in list(self.db.execute("SELECT * FROM memories WHERE pinned=0 AND kind!='fact'")):
            s = self.strength(r, now)
            if s < self.cfg.forget_threshold:
                self.db.execute("DELETE FROM memories WHERE id=?", (r["id"],))
                forgotten += 1
            elif s < self.cfg.vague_threshold and r["stage"] == 0 and r["vague"]:
                self.db.execute("UPDATE memories SET stage=1 WHERE id=?", (r["id"],))
                blurred += 1
        self.db.commit()
        return {"floutes": blurred, "oublies": forgotten}
