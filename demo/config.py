"""Configuration de la demo, lue depuis demo/.env puis l'environnement.

Un seul principe : chaque brique est choisie par une variable, avec un preset
qui remplit l'URL et le modele par defaut. Tout reste surchargeable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent
ROOT_DIR = DEMO_DIR.parent

# Presets des fournisseurs compatibles OpenAI. Les identifiants de modeles
# changent souvent : ils sont surchargeables par LLM_MODEL / VISION_MODEL, et
# `python demo/start.py --check` liste ceux que la cle donne reellement.
LLM_PRESETS = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "openai/gpt-oss-120b",
        "key_env": "GROQ_API_KEY",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-flash-latest",
        "key_env": "GEMINI_API_KEY",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5:3b",
        "key_env": "",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "key_env": "OPENAI_API_KEY",
    },
    "mock": {"base_url": "", "model": "mock", "key_env": ""},
}


def load_dotenv(path: Path) -> None:
    """Parseur .env minimal : KEY=VALUE, commentaires, guillemets. Sans dependance."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        os.environ.setdefault(key, value)


@dataclass
class Endpoint:
    provider: str
    base_url: str
    model: str
    api_key: str

    @property
    def is_mock(self) -> bool:
        return self.provider == "mock"


def _endpoint(prefix: str, provider: str) -> Endpoint:
    preset = LLM_PRESETS.get(provider)
    if preset is None:
        raise SystemExit(
            f"{prefix}_PROVIDER={provider!r} inconnu. Choix : {', '.join(LLM_PRESETS)}"
        )
    key = os.environ.get(f"{prefix}_API_KEY", "")
    if not key and preset["key_env"]:
        key = os.environ.get(preset["key_env"], "")
    return Endpoint(
        provider=provider,
        base_url=os.environ.get(f"{prefix}_BASE_URL", preset["base_url"]).rstrip("/"),
        model=os.environ.get(f"{prefix}_MODEL", preset["model"]),
        api_key=key,
    )


def _auto_provider() -> str:
    """Choisit un fournisseur si rien n'est precise : la premiere cle trouvee."""
    if os.environ.get("GROQ_API_KEY"):
        return "groq"
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "mock"


@dataclass
class Settings:
    llm: Endpoint
    vision: Endpoint | None
    stt_engine: str  # groq | local | mock
    stt_model: str
    tts_engine: str  # piper | edge | mock
    piper_voice: str
    edge_voice: str
    persona: str
    host: str
    port: int
    max_history: int
    material_max_chars: int
    llm_extra: dict = field(default_factory=dict)

    def public(self) -> dict:
        """Ce que le navigateur a le droit de savoir. Jamais les cles."""
        return {
            "llm": f"{self.llm.provider}:{self.llm.model}",
            "vision": f"{self.vision.provider}:{self.vision.model}" if self.vision else None,
            "stt": f"{self.stt_engine}:{self.stt_model}",
            "tts": self.tts_engine,
            "persona": self.persona,
        }


def load_settings() -> Settings:
    load_dotenv(DEMO_DIR / ".env")

    llm_provider = os.environ.get("LLM_PROVIDER", "").strip() or _auto_provider()
    llm = _endpoint("LLM", llm_provider)

    # Vision : explicite, sinon le LLM principal s'il sait voir, sinon Gemini si
    # une cle existe. Groq n'a plus de modele vision dans son offre gratuite.
    vision_provider = os.environ.get("VISION_PROVIDER", "").strip()
    vision: Endpoint | None
    if vision_provider == "none":
        vision = None
    elif vision_provider:
        vision = _endpoint("VISION", vision_provider)
    elif llm_provider in ("gemini", "openai", "mock"):
        vision = llm
    elif os.environ.get("GEMINI_API_KEY"):
        vision = _endpoint("VISION", "gemini")
    else:
        vision = None

    stt_engine = os.environ.get("STT_ENGINE", "").strip()
    if not stt_engine:
        if llm_provider == "mock":
            stt_engine = "mock"
        elif os.environ.get("GROQ_API_KEY"):
            stt_engine = "groq"
        else:
            stt_engine = "local"
    stt_model = os.environ.get(
        "STT_MODEL", "whisper-large-v3-turbo" if stt_engine == "groq" else "small"
    )

    tts_engine = os.environ.get("TTS_ENGINE", "").strip() or (
        "mock" if llm_provider == "mock" else "piper"
    )

    extra: dict = {}
    if "gpt-oss" in llm.model:
        # Modele a raisonnement : on limite la reflexion pour garder un premier
        # token rapide. Pour un cours de maths exigeant, passer a "medium".
        extra["reasoning_effort"] = os.environ.get("LLM_REASONING_EFFORT", "low")

    return Settings(
        llm=llm,
        vision=vision,
        stt_engine=stt_engine,
        stt_model=stt_model,
        tts_engine=tts_engine,
        piper_voice=os.environ.get("PIPER_VOICE", "fr_FR-siwis-medium"),
        edge_voice=os.environ.get("EDGE_VOICE", "fr-FR-DeniseNeural"),
        persona=os.environ.get("PERSONA", "prof"),
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        max_history=int(os.environ.get("MAX_HISTORY", "24")),
        material_max_chars=int(os.environ.get("MATERIAL_MAX_CHARS", "15000")),
        llm_extra=extra,
    )
