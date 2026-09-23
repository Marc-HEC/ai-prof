"""Serveur de la demo sans GPU.

    navigateur : micro, VAD, avatar VRM, lip-sync, tableau, historique
         |  POST /api/stt   WAV 16 kHz -> texte
         |  POST /api/chat  historique -> SSE : emotion, phrases, tableau
         |  POST /api/tts   phrase -> audio
    serveur    : adaptateurs STT / LLM / TTS, sans etat de conversation

Le serveur ne garde aucune conversation : l'historique vit dans le navigateur
et revient a chaque tour, comme prevu par la decision D2.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

import llm
import material
import stt
import tts
from config import DEMO_DIR, ROOT_DIR, load_settings
from stream_parser import EMOTIONS, ResponseParser, normalize_emotion

log = logging.getLogger("demo")
SETTINGS = load_settings()
PERSONA_DIR = ROOT_DIR / "backend" / "personas"
AVATAR_DIR = ROOT_DIR / "avatars"
STATUS: dict[str, str] = {"stt": "en attente", "tts": "en attente"}

CHANNEL_RULES = f"""
---
Format de sortie, a respecter a chaque reponse :

Commence toujours par un tag d'emotion, par exemple [emotion:joie]. Emotions
possibles : {", ".join(sorted(EMOTIONS))}. Tu peux en remettre un plus loin si
ton ton change. Le tag n'est pas lu, il pilote ton visage et ta voix.

Pour ecrire au tableau, entoure le contenu de <tableau> et </tableau>. Dedans,
du Markdown, avec les formules en LaTeX entre $...$ ou $$...$$. Le tableau
n'est jamais lu a voix haute. Chaque bloc s'ajoute a la suite du tableau.

Hors du tableau, uniquement ce que tu dis a voix haute, sans markdown, sans
LaTeX, sans symboles.

Si l'eleve te montre une image (un dessin fait au tableau, une photo de cours
ou d'exercice), decris brievement ce que tu y vois avant d'y repondre.
""".strip()


def list_personas() -> list[str]:
    return sorted(p.stem for p in PERSONA_DIR.glob("*.md"))


def load_persona(name: str) -> str:
    if name not in list_personas():
        name = SETTINGS.persona if SETTINGS.persona in list_personas() else "default"
    text = (PERSONA_DIR / f"{name}.md").read_text(encoding="utf-8")
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()


def avatar_url() -> str | None:
    for rel in ("demo/model.vrm", "model.vrm"):
        if (AVATAR_DIR / rel).exists():
            return f"/avatars/{rel}"
    return None


# -- construction du contexte ----------------------------------------------

def _clean_content(content, allow_image: bool):
    if isinstance(content, str):
        return content[:8000]
    parts = []
    for part in content or []:
        if part.get("type") == "text":
            parts.append({"type": "text", "text": str(part.get("text", ""))[:8000]})
        elif part.get("type") == "image_url" and allow_image:
            url = (part.get("image_url") or {}).get("url", "")
            if url.startswith("data:image/"):
                parts.append({"type": "image_url", "image_url": {"url": url}})
    if not any(p["type"] == "image_url" for p in parts):
        return " ".join(p["text"] for p in parts)
    return parts


def build_messages(body: dict, allow_image: bool) -> list[dict]:
    system = load_persona(str(body.get("persona", SETTINGS.persona))) + "\n\n" + CHANNEL_RULES
    materials = body.get("materials") or []
    if materials:
        budget = SETTINGS.material_max_chars
        chunks = []
        for m in materials:
            text = str(m.get("text", ""))[:budget]
            budget -= len(text)
            chunks.append(f"### {m.get('name', 'document')}\n{text}")
            if budget <= 0:
                break
        system += ("\n\n---\nSupport de cours fourni par l'eleve. Appuie-toi dessus, "
                   "avec ses notations :\n\n" + "\n\n".join(chunks))
    history = [
        m for m in (body.get("messages") or [])
        if m.get("role") in ("user", "assistant") and m.get("content")
    ][-SETTINGS.max_history:]
    msgs = [{"role": "system", "content": system}]
    for i, m in enumerate(history):
        last = i == len(history) - 1
        msgs.append({"role": m["role"], "content": _clean_content(m["content"], allow_image and last)})
    return msgs


def _has_image(messages: list[dict]) -> bool:
    return any(isinstance(m["content"], list) for m in messages)


# -- routes ----------------------------------------------------------------

async def api_config(request: Request) -> JSONResponse:
    return JSONResponse({
        **SETTINGS.public(),
        "personas": list_personas(),
        "avatarUrl": avatar_url(),
        "status": STATUS,
    })


async def api_stt(request: Request) -> JSONResponse:
    data = await request.body()
    if not data or len(data) > 15 * 1024 * 1024:
        return JSONResponse({"error": "audio vide ou trop long"}, status_code=400)
    t0 = time.perf_counter()
    try:
        text = await stt.transcribe(SETTINGS.stt_engine, SETTINGS.stt_model, data)
    except Exception as exc:  # noqa: BLE001 - on renvoie l'erreur a l'interface
        log.exception("STT")
        return JSONResponse({"error": f"STT : {exc}"}, status_code=502)
    return JSONResponse({"text": text, "ms": round((time.perf_counter() - t0) * 1000)})


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


async def api_chat(request: Request) -> StreamingResponse:
    body = await request.json()
    wants_image = any(isinstance(m.get("content"), list) for m in body.get("messages") or [])
    endpoint = SETTINGS.vision if (wants_image and SETTINGS.vision) else SETTINGS.llm
    messages = build_messages(body, allow_image=endpoint is SETTINGS.vision)

    async def events():
        t0 = time.perf_counter()
        first = None
        parser = ResponseParser()
        if wants_image and not _has_image(messages):
            yield _sse({"type": "warning", "message":
                        "Aucun modele vision configure : l'image n'a pas ete transmise. "
                        "Ajoutez GEMINI_API_KEY dans demo/.env."})
        try:
            extra = SETTINGS.llm_extra if endpoint is SETTINGS.llm else {}
            async for delta in llm.stream_chat(endpoint, messages, extra=extra):
                if first is None:
                    first = time.perf_counter()
                    yield _sse({"type": "timing", "llm_first_token_ms": round((first - t0) * 1000)})
                for kind, value in parser.feed(delta):
                    yield _sse({"type": kind, "value": value})
            for kind, value in parser.finish():
                yield _sse({"type": kind, "value": value})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("LLM")
            yield _sse({"type": "error", "message": str(exc)})
        yield _sse({"type": "done", "history": parser.history_text(),
                    "model": f"{endpoint.provider}:{endpoint.model}",
                    "llm_total_ms": round((time.perf_counter() - t0) * 1000)})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def api_tts(request: Request) -> Response:
    body = await request.json()
    text = str(body.get("text", "")).strip()[:600]
    if not text:
        return JSONResponse({"error": "texte vide"}, status_code=400)
    emotion = normalize_emotion(str(body.get("emotion", "neutre")))
    try:
        audio, mime = await tts.synthesize(
            SETTINGS.tts_engine, text, emotion,
            piper_voice=SETTINGS.piper_voice, edge_voice=SETTINGS.edge_voice,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("TTS")
        return JSONResponse({"error": f"TTS : {exc}"}, status_code=502)
    return Response(audio, media_type=mime, headers={"Cache-Control": "no-store"})


async def api_material(request: Request) -> JSONResponse:
    form = await request.form(max_part_size=20 * 1024 * 1024)
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        return JSONResponse({"error": "fichier manquant"}, status_code=400)
    data = await upload.read()
    try:
        result = material.extract(upload.filename or "document", data)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(result)


# -- demarrage -------------------------------------------------------------

def _warm(name: str, fn, *args) -> None:
    STATUS[name] = "chargement"
    try:
        fn(*args)
        STATUS[name] = "pret"
    except Exception as exc:  # noqa: BLE001
        STATUS[name] = f"erreur : {exc}"
        log.error("Prechauffage %s : %s", name, exc)


@asynccontextmanager
async def lifespan(app):
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _warm, "stt", stt.warmup, SETTINGS.stt_engine, SETTINGS.stt_model)
    loop.run_in_executor(None, _warm, "tts", tts.warmup, SETTINGS.tts_engine, SETTINGS.piper_voice)
    yield


routes = [
    Route("/api/config", api_config),
    Route("/api/stt", api_stt, methods=["POST"]),
    Route("/api/chat", api_chat, methods=["POST"]),
    Route("/api/tts", api_tts, methods=["POST"]),
    Route("/api/material", api_material, methods=["POST"]),
    Mount("/avatars", StaticFiles(directory=AVATAR_DIR, check_dir=False), name="avatars"),
    Mount("/", StaticFiles(directory=DEMO_DIR / "static", html=True), name="static"),
]
app = Starlette(routes=routes, lifespan=lifespan)
