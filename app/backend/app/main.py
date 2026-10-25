"""Serveur local : sert l'interface web et relie le navigateur aux services RunPod."""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .config import BASE_DIR, load_config, resolve
from .memory import Embedder, Memory
from .persona import Persona
from .session import Deps, Session

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
FRONTEND = BASE_DIR.parent / "frontend"


def create_app(deps: Deps | None = None, persona_path: str | None = None) -> FastAPI:
    if deps is None:
        cfg = load_config()
        persona = Persona.load(resolve(persona_path or cfg.persona))
        # Le prof ne doit pas hériter des souvenirs intimes de la compagne, et inversement.
        db_path = resolve(persona.memoire or cfg.memory.db_path)
        deps = Deps(cfg=cfg, persona=persona,
                    memory=Memory(db_path, cfg.memory, Embedder(cfg.memory.embed_model)))
    app = FastAPI(title="Compagne", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/api/info")
    async def info():
        # Lu par le navigateur avant de créer l'avatar : VRM ou 2D, tableau ou non.
        return {**deps.persona.public(),
                "recherche_web": "recherche_web" in deps.persona.outils and deps.cfg.recherche_web.enabled}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        session = Session(deps, ws.send_json, ws.send_bytes)
        await session.start()
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    await session.on_audio(msg["bytes"])
                    continue
                data = json.loads(msg.get("text") or "{}")
                kind = data.get("type")
                if kind == "text" and data.get("text", "").strip():
                    await session.user_message(data["text"].strip(), images=data.get("images"))
                elif kind == "image":
                    await session.user_message(data.get("text") or "Regarde cette image.",
                                               images=data.get("images"))
                elif kind == "voice_note":
                    # message vocal déjà converti en PCM16 16 kHz par le navigateur
                    session._spawn(session.on_speech(base64.b64decode(data["pcm"])))
                elif kind == "interrupt":
                    await session.interrupt()
        except WebSocketDisconnect:
            pass
        finally:
            await session.close()

    if FRONTEND.exists():
        app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
    return app


def run() -> None:
    import argparse

    import uvicorn
    parser = argparse.ArgumentParser(description="Compagne ou prof, selon la fiche choisie.")
    parser.add_argument("--persona", help="fiche à utiliser à la place de celle de config.yaml, "
                                          "par exemple persona/prof.yaml")
    args = parser.parse_args()
    cfg = load_config()
    if cfg.server.host not in ("127.0.0.1", "localhost"):
        logging.warning("Le serveur écoute sur %s : il est visible sur ton réseau.", cfg.server.host)
    uvicorn.run(create_app(persona_path=args.persona), host=cfg.server.host, port=cfg.server.port, log_level="warning")


if __name__ == "__main__":
    run()
