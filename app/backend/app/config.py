"""Chargement de la configuration (config.yaml)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass
class LLMConfig:
    base_url: str = "http://127.0.0.1:5001/v1"
    model: str = "koboldcpp"
    temperature: float = 0.9
    max_tokens: int = 300
    history_turns: int = 20


@dataclass
class STTConfig:
    base_url: str = "http://127.0.0.1:5001/v1"
    model: str = "whisper-1"
    language: str = "fr"


@dataclass
class TTSConfig:
    base_url: str = "http://127.0.0.1:7851/v1"
    model: str = "tts-1"
    voice: str = "voix.wav"
    response_format: str = "wav"
    speed: float = 1.0


@dataclass
class VADConfig:
    aggressiveness: int = 2          # 0 (tolérant) à 3 (strict)
    end_silence_ms: int = 500        # silence qui marque la fin de ta phrase
    min_speech_ms: int = 250         # en dessous, c'est du bruit
    barge_in_ms: int = 300           # parole continue nécessaire pour l'interrompre


@dataclass
class MemoryConfig:
    db_path: str = "data/memoire.db"
    embed_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    recall_k: int = 6
    reflect_every_turns: int = 12
    half_life_days: float = 30.0
    vague_threshold: float = 0.25
    forget_threshold: float = 0.08
    journal_after_hours: float = 6.0


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    stt: STTConfig = field(default_factory=STTConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    vad: VADConfig = field(default_factory=VADConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    persona: str = "persona/exemple.yaml"


def _section(cls, data: dict | None):
    data = data or {}
    known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
    return cls(**known)


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path) if path else BASE_DIR / "config.yaml"
    if not path.exists():
        return Config()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config(
        llm=_section(LLMConfig, raw.get("llm")),
        stt=_section(STTConfig, raw.get("stt")),
        tts=_section(TTSConfig, raw.get("tts")),
        vad=_section(VADConfig, raw.get("vad")),
        memory=_section(MemoryConfig, raw.get("memory")),
        server=_section(ServerConfig, raw.get("server")),
        persona=raw.get("persona", "persona/exemple.yaml"),
    )


def resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else BASE_DIR / p
