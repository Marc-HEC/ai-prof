"""Tests de la demo : parseur de flux et serveur en mode mock.

    python -m pytest demo/tests        ou        python demo/tests/test_demo.py
"""

from __future__ import annotations

import io
import json
import os
import random
import sys
import wave
from pathlib import Path

DEMO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEMO))
for k in ("LLM_PROVIDER", "STT_ENGINE", "TTS_ENGINE"):
    os.environ[k] = "mock"
os.environ["VISION_PROVIDER"] = ""

from stream_parser import ResponseParser  # noqa: E402

SAMPLE = (
    "[emotion:joie] Bonne question ! On va voir ca ensemble. "
    "<tableau>\n## Derivee\n$$f'(x) = 2x$$\n</tableau> "
    "[emotion:pensif] Tu vois pourquoi ? Dis-moi ce que tu en penses."
)


def run(text: str, chunks: list[int] | None = None) -> list[tuple[str, str]]:
    p = ResponseParser()
    out = []
    i = 0
    sizes = iter(chunks or [])
    while i < len(text):
        n = next(sizes, None) or 1
        out += p.feed(text[i : i + n])
        i += n
    out += p.finish()
    return out


EXPECTED = [
    ("emotion", "joie"),
    ("sentence", "Bonne question !"),
    ("sentence", "On va voir ca ensemble."),
    ("board", "## Derivee\n$$f'(x) = 2x$$"),
    ("emotion", "pensif"),
    ("sentence", "Tu vois pourquoi ?"),
    ("sentence", "Dis-moi ce que tu en penses."),
]


def test_parser_whole():
    assert run(SAMPLE, [len(SAMPLE)]) == EXPECTED


def test_parser_any_chunking():
    rng = random.Random(0)
    for _ in range(300):
        chunks = [rng.randint(1, 9) for _ in range(len(SAMPLE))]
        assert run(SAMPLE, chunks) == EXPECTED


def test_parser_char_by_char():
    assert run(SAMPLE) == EXPECTED


def test_parser_edge_cases():
    # Balise tableau jamais fermee : le contenu part quand meme au tableau.
    assert run("Regarde. <tableau>x^2") == [("sentence", "Regarde."), ("board", "x^2")]
    # Crochets et chevrons ordinaires restent du texte.
    assert run("Si a < b alors [a, b] est un intervalle.") == [
        ("sentence", "Si a < b alors [a, b] est un intervalle.")]
    # Emotion inconnue -> neutre ; markdown retire de la parole.
    assert run("[emotion:euphorie] **Super** travail !") == [
        ("emotion", "neutre"), ("sentence", "Super travail !")]
    # Balise avec attribut, casse differente.
    assert run("<Tableau class='x'>A</TABLEAU>Ok.") == [("board", "A"), ("sentence", "Ok.")]
    # Ce qui precede le tableau est dit avant qu'il n'apparaisse.
    assert run("Regarde bien : <tableau>A</tableau> voila") == [
        ("sentence", "Regarde bien :"), ("board", "A"), ("sentence", "voila")]
    # Nombres decimaux : pas de coupure sur le point.
    assert run("Pi vaut 3.14 environ.") == [("sentence", "Pi vaut 3.14 environ.")]


def test_parser_history_strips_emotions():
    p = ResponseParser()
    p.feed(SAMPLE)
    p.finish()
    h = p.history_text()
    assert "[emotion" not in h and "<tableau>" in h


def test_long_sentence_split_at_comma():
    long = ("Alors on prend la fonction, " * 12).strip() + " voila."
    sents = [v for k, v in run(long, [len(long)]) if k == "sentence"]
    assert len(sents) > 1 and all(len(s) <= 210 for s in sents)


# -- serveur ----------------------------------------------------------------

def client():
    from starlette.testclient import TestClient

    import server

    return TestClient(server.app)


def _wav(seconds=0.5, sr=16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x00" * int(sr * seconds))
    return buf.getvalue()


def test_server_endpoints():
    with client() as c:
        cfg = c.get("/api/config").json()
        assert cfg["llm"] == "mock:mock" and "prof" in cfg["personas"]
        assert "api_key" not in json.dumps(cfg).lower()

        assert c.post("/api/stt", content=_wav(), headers={"Content-Type": "audio/wav"}).json()["text"]

        r = c.post("/api/tts", json={"text": "Bonjour a toi.", "emotion": "joie"})
        assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
        with wave.open(io.BytesIO(r.content)) as w:
            assert w.getnframes() > 1000

        body = {"persona": "prof", "materials": [{"name": "cours.md", "text": "Chapitre derivees"}],
                "messages": [{"role": "user", "content": "Explique les derivees"}]}
        events = []
        with c.stream("POST", "/api/chat", json=body) as resp:
            for line in resp.iter_lines():
                if line.startswith("data:"):
                    events.append(json.loads(line[5:]))
        kinds = [e["type"] for e in events]
        assert kinds[0] == "timing" and kinds[-1] == "done"
        assert "emotion" in kinds and "board" in kinds and kinds.count("sentence") >= 3
        assert "Explique les derivees" in " ".join(e.get("value", "") for e in events)

        up = c.post("/api/material", files={"file": ("cours.md", b"# Suites\nu(n+1) = 2u(n)")}).json()
        assert up["kind"] == "text" and "Suites" in up["text"]
        assert c.post("/api/material", files={"file": ("x.exe", b"MZ")}).status_code == 400

        assert c.get("/").status_code == 200 and "AI Prof" in c.get("/").text


def test_prompt_contains_persona_rules_and_material():
    import server

    msgs = server.build_messages(
        {"persona": "prof", "materials": [{"name": "c.md", "text": "NOTATION_SPECIALE"}],
         "messages": [{"role": "user", "content": [
             {"type": "text", "text": "regarde"},
             {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}]},
        allow_image=False)
    assert "Camille" in msgs[0]["content"] and "<tableau>" in msgs[0]["content"]
    assert "NOTATION_SPECIALE" in msgs[0]["content"]
    assert msgs[1]["content"] == "regarde"  # image retiree sans modele vision


if __name__ == "__main__":
    import inspect

    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and inspect.isfunction(fn):
            try:
                fn()
                print("ok  ", name)
            except Exception as exc:  # noqa: BLE001
                fails += 1
                print("FAIL", name, repr(exc))
    raise SystemExit(1 if fails else 0)
