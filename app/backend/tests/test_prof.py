"""Mode prof : tableau blanc, recherche web, consignes. Sans GPU ni réseau."""
import asyncio
import json
from pathlib import Path

import pytest

from app.audio import SpeechSegmenter
from app.config import Config, MemoryConfig, WebSearchConfig
from app.memory import Memory
from app.persona import Persona, build_system_prompt
from app.session import Deps, Session
from app.teaching import BlockParser, board_event, tools_prompt

ROOT = Path(__file__).resolve().parent.parent


def parse_all(tokens):
    p, out = BlockParser(), []
    for t in tokens:
        out += p.push(t)
    out += p.flush()
    # Recolle les morceaux de texte consécutifs pour comparer facilement.
    merged = []
    for kind, content in out:
        if merged and kind == "text" and merged[-1][0] == "text":
            merged[-1] = ("text", merged[-1][1] + content)
        else:
            merged.append((kind, content))
    return merged


def test_block_parser_handles_tags_split_across_tokens():
    tokens = ["Je t'écris ça. <tab", "leau>\na² + b²", " = c²\n</tab", "leau> Tu vois ?"]
    assert parse_all(tokens) == [
        ("text", "Je t'écris ça. "),
        ("tableau", "a² + b² = c²"),
        ("text", " Tu vois ?"),
    ]


def test_block_parser_leaves_comparisons_alone():
    assert parse_all(["Si x <", " 3 alors", " y > 2."]) == [("text", "Si x < 3 alors y > 2.")]


def test_block_parser_unclosed_block_is_still_delivered():
    assert parse_all(["Regarde. <recherche>prix Nobel 2026"]) == [
        ("text", "Regarde. "), ("recherche", "prix Nobel 2026")]


def test_board_event_detects_mermaid_or_notes():
    assert board_event("flowchart LR\n A --> B")["op"] == "schema"
    notes = board_event("Pythagore\n\na² + b² = c²")
    assert notes == {"type": "board", "op": "notes", "lines": ["Pythagore", "a² + b² = c²"]}
    assert board_event("   ") is None


def test_prof_persona_prompt_mentions_tools_only_when_enabled():
    p = Persona.load(ROOT / "persona" / "prof.yaml")
    assert p.role == "prof" and p.avatar == "2d" and "tableau" in p.outils
    from datetime import datetime
    off = build_system_prompt(p, {}, [], [], None, datetime(2026, 10, 25, 18), None,
                              tools_prompt(p.outils, web_search=False))
    on = tools_prompt(p.outils, web_search=True)
    assert "<tableau>" in off and "<recherche>" not in off
    assert "<recherche>" in on
    assert "2 à 4 par réponse" in off  # règles de cours, pas de conversation téléphonique


class ProfServices:
    """Faux LLM : première réponse avec tableau et recherche, deuxième avec la suite."""

    def __init__(self, replies, sources=None, fail_search=False):
        self.replies, self.sources, self.fail_search = list(replies), sources or [], fail_search
        self.spoken, self.calls, self.searches = [], [], []

    async def stream_chat(self, messages, cfg):
        self.calls.append(messages)
        reply = self.replies.pop(0)
        for i in range(0, len(reply), 7):
            yield reply[i:i + 7]

    async def complete(self, messages, cfg, max_tokens=500, temperature=0.4):
        return json.dumps({})

    async def transcribe(self, wav, cfg):
        return ""

    async def synthesize(self, text, cfg):
        self.spoken.append(text)
        return b"RIFF"

    async def web_search(self, query, cfg):
        self.searches.append(query)
        if self.fail_search:
            raise RuntimeError("hors ligne")
        return self.sources


def make_prof_session(tmp_path, services, search=True):
    cfg = Config(memory=MemoryConfig(reflect_every_turns=100), recherche_web=WebSearchConfig(enabled=search))
    deps = Deps(cfg=cfg, persona=Persona.load(ROOT / "persona" / "prof.yaml"),
                memory=Memory(tmp_path / "m.db", cfg.memory), services=services)
    sent = []

    async def sj(m):
        sent.append(m)

    async def sb(_):
        pass

    return Session(deps, sj, sb, segmenter=SpeechSegmenter(cfg.vad, is_speech=lambda f: False)), sent


@pytest.mark.asyncio
async def test_board_block_is_drawn_and_never_spoken(tmp_path):
    svc = ProfServices(["[emotion:joie] Je te l'écris au tableau. <tableau>\na² + b² = c²\n</tableau> "
                        "C'est la somme des carrés. [tableau:effacer]"])
    s, sent = make_prof_session(tmp_path, svc)
    await s.start()
    assert sent[0]["persona"]["role"] == "prof"
    await s.user_message("Explique Pythagore")
    await s.turn
    boards = [m for m in sent if m["type"] == "board"]
    assert boards == [{"type": "board", "op": "notes", "lines": ["a² + b² = c²"]}]
    assert any(m["type"] == "tag" and m["kind"] == "tableau" and m["value"] == "effacer" for m in sent)
    assert all("a²" not in t and "<" not in t for t in svc.spoken)
    assert svc.spoken == ["Je te l'écris au tableau.", "C'est la somme des carrés."]


@pytest.mark.asyncio
async def test_search_pauses_answer_then_continues_with_sources(tmp_path):
    svc = ProfServices(
        ["Bonne question, je regarde ça. <recherche>taux BCE actuel</recherche> ignoré",
         "D'après la BCE, le taux est de 2 %. Ça te va ?"],
        sources=[{"titre": "BCE", "url": "https://ecb.europa.eu", "extrait": "taux de dépôt 2 %"}])
    s, sent = make_prof_session(tmp_path, svc)
    await s.start()
    await s.user_message("C'est quoi le taux de la BCE ?")
    await s.turn
    assert svc.searches == ["taux BCE actuel"]
    assert " ".join(svc.spoken) == "Bonne question, je regarde ça. D'après la BCE, le taux est de 2 %. Ça te va ?"
    followup = svc.calls[1][-1]["content"]
    assert "taux de dépôt 2 %" in followup and "ecb.europa.eu" in followup
    assert any(m["type"] == "sources" and m["items"][0]["url"] == "https://ecb.europa.eu" for m in sent)


@pytest.mark.asyncio
async def test_search_disabled_never_leaves_the_pc(tmp_path):
    svc = ProfServices(["Je regarde ça. <recherche>météo</recherche> Je ne peux pas chercher."])
    s, _ = make_prof_session(tmp_path, svc, search=False)
    await s.start()
    await s.user_message("Quel temps fait-il ?")
    await s.turn
    assert svc.searches == [] and len(svc.calls) == 1
    assert "<recherche>" not in svc.calls[0][0]["content"]


@pytest.mark.asyncio
async def test_failed_search_still_ends_the_answer(tmp_path):
    svc = ProfServices(["Je regarde. <recherche>actu</recherche>", "Je n'ai pas pu vérifier, désolée."],
                       fail_search=True)
    s, sent = make_prof_session(tmp_path, svc)
    await s.start()
    await s.user_message("Quoi de neuf ?")
    await asyncio.wait_for(s.turn, 2)
    assert " ".join(svc.spoken) == "Je regarde. Je n'ai pas pu vérifier, désolée."
    assert "rien donné" in svc.calls[1][-1]["content"]
    assert sent[-1]["type"] == "assistant_end"


@pytest.mark.asyncio
async def test_web_search_reads_pages_and_falls_back_to_snippet(monkeypatch):
    import httpx

    from app import teaching

    monkeypatch.setattr(teaching, "_ddgs_search", lambda q, region, n: [
        {"title": "Page lisible", "href": "https://a.example/ok", "body": "résumé A"},
        {"title": "Page en panne", "href": "https://b.example/ko", "body": "résumé B"},
    ])
    html = "<html><body><article><p>" + "Le taux de dépôt est fixé à 2 %. " * 20 + "</p></article></body></html>"

    def handler(request):
        return httpx.Response(200, text=html) if request.url.host == "a.example" else httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sources = await teaching.web_search("taux", WebSearchConfig(enabled=True), client)
    assert [s["url"] for s in sources] == ["https://a.example/ok", "https://b.example/ko"]
    assert "taux de dépôt" in sources[0]["extrait"]
    assert sources[1]["extrait"] == "résumé B"


def test_info_endpoint_tells_the_browser_which_avatar_and_tools(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    cfg = Config(memory=MemoryConfig(), recherche_web=WebSearchConfig(enabled=False))
    deps = Deps(cfg=cfg, persona=Persona.load(ROOT / "persona" / "prof.yaml"),
                memory=Memory(tmp_path / "m.db", cfg.memory))
    info = TestClient(create_app(deps)).get("/api/info").json()
    assert info == {"name": "Sora", "role": "prof", "outils": ["tableau", "recherche_web"],
                    "avatar": "2d", "recherche_web": False}
