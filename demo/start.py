"""Lance la demo sans GPU, en une commande.

    python demo/start.py            installe ce qui manque, telecharge les
                                    assets, demarre le serveur, ouvre le navigateur
    python demo/start.py --check    verifie la config et la cle, sans demarrer
    python demo/start.py --mock     force le mode sans cle ni modele

Les telechargements (avatar VRM d'exemple, voix Piper) ne se font qu'une fois.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent
ROOT_DIR = DEMO_DIR.parent
sys.path.insert(0, str(DEMO_DIR))

# Avatar d'exemple de pixiv, licence VRM 1.0 : redistribution et modification
# autorisees, usage commercial autorise. Il expose les visemes aa/ih/ou/ee/oh et
# les expressions happy/sad/angry/relaxed/surprised dont la demo a besoin.
VRM_URL = ("https://raw.githubusercontent.com/pixiv/three-vrm/dev/packages/three-vrm/"
           "examples/models/VRM1_Constraint_Twist_Sample.vrm")
VRM_PATH = ROOT_DIR / "avatars" / "demo" / "model.vrm"
PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

CORE = {"starlette": "starlette", "uvicorn": "uvicorn", "httpx": "httpx",
        "multipart": "python-multipart", "numpy": "numpy"}


def missing_modules(settings) -> list[str]:
    need = dict(CORE)
    if settings.stt_engine == "local":
        need["faster_whisper"] = "faster-whisper"
    if settings.tts_engine == "piper":
        need["piper"] = "piper-tts"
    if settings.tts_engine == "edge":
        need["edge_tts"] = "edge-tts"
    need["pypdfium2"] = "pypdfium2"
    out = []
    for mod, pkg in need.items():
        try:
            importlib.import_module(mod)
        except ImportError:
            out.append(pkg)
    return out


def download(url: str, dest: Path, label: str) -> bool:
    if dest.exists() and dest.stat().st_size > 0:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"  telechargement : {label}")
    try:
        with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as f:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while chunk := resp.read(1 << 16):
                f.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r    {done * 100 // total:3d} %", end="", flush=True)
        print()
        tmp.replace(dest)
        return True
    except Exception as exc:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        print(f"\n  echec ({exc}). URL : {url}")
        return False


def piper_url(voice: str) -> str:
    # fr_FR-siwis-medium -> fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx
    locale, name, quality = voice.split("-", 2)
    return f"{PIPER_BASE}/{locale.split('_')[0]}/{locale}/{name}/{quality}/{voice}.onnx"


def fetch_assets(settings) -> None:
    print("Assets")
    if not any((ROOT_DIR / "avatars").glob("**/*.vrm")):
        if not download(VRM_URL, VRM_PATH, "avatar VRM d'exemple (pixiv, ~10 Mo)"):
            print("  -> sans VRM, l'interface affiche un visage 2D de secours.")
    else:
        print("  avatar VRM : present")
    if settings.tts_engine == "piper":
        from tts import piper_paths

        model, cfg = piper_paths(settings.piper_voice)
        url = piper_url(settings.piper_voice)
        download(url, model, f"voix Piper {settings.piper_voice} (~60 Mo)")
        download(url + ".json", cfg, "configuration de la voix")


async def check(settings) -> int:
    import llm

    print("Configuration")
    for k, v in settings.public().items():
        print(f"  {k:8s} {v}")
    if settings.llm.is_mock:
        print("\nMode mock : aucune cle. Ajoutez GROQ_API_KEY ou GEMINI_API_KEY dans demo/.env.")
        return 0
    status = 0
    for label, ep in (("LLM", settings.llm), ("Vision", settings.vision)):
        if ep is None or (label == "Vision" and ep is settings.llm):
            continue
        try:
            models = await llm.list_models(ep)
        except Exception as exc:  # noqa: BLE001
            print(f"\n{label} : echec de connexion a {ep.base_url} : {exc}")
            status = 1
            continue
        ok = ep.model in models
        print(f"\n{label} {ep.provider} : {len(models)} modeles, "
              f"{ep.model!r} {'disponible' if ok else 'ABSENT'}")
        if not ok:
            status = 1
            print("  Modeles disponibles :", ", ".join(models[:40]))
            print(f"  -> choisissez-en un avec {label.upper() if label == 'LLM' else 'VISION'}_MODEL=... dans demo/.env")
    return status


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="verifie la config et quitte")
    ap.add_argument("--mock", action="store_true", help="aucune cle, aucun modele")
    ap.add_argument("--no-install", action="store_true", help="ne pas lancer pip")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    if args.mock:
        for k in ("LLM_PROVIDER", "STT_ENGINE", "TTS_ENGINE"):
            os.environ[k] = "mock"

    from config import load_settings

    settings = load_settings()
    if not (DEMO_DIR / ".env").exists() and not args.mock:
        print("Astuce : copiez demo/.env.example en demo/.env et mettez-y une cle gratuite.\n")

    missing = missing_modules(settings)
    if missing:
        if args.no_install:
            print("Modules manquants :", " ".join(missing))
            return 1
        print("Installation :", " ".join(missing))
        subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])
        importlib.invalidate_caches()

    if args.check:
        return asyncio.run(check(settings))

    fetch_assets(settings)

    import uvicorn

    url = f"http://{'localhost' if settings.host in ('127.0.0.1', '0.0.0.0') else settings.host}:{settings.port}"
    print(f"\nDemo : {url}   (Ctrl+C pour arreter)")
    print("  LLM", settings.public()["llm"], "| STT", settings.public()["stt"],
          "| TTS", settings.tts_engine)
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(url)), daemon=True).start()
    uvicorn.run("server:app", app_dir=str(DEMO_DIR), host=settings.host, port=settings.port,
                log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
