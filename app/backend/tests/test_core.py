import asyncio
import json
import time
from pathlib import Path

import pytest

from app.audio import FRAME_BYTES, SpeechSegmenter
from app.config import Config, MemoryConfig, VADConfig
from app.memory import DAY, Memory
from app.persona import Persona, apply_reflection, build_system_prompt
from app.session import Deps, Session, parse_json
from app.text import SentenceBuffer, extract_tags

ROOT = Path(__file__).resolve().parent.parent


def test_extract_tags_removes_tags_and_asterisks():
    clean, tags = extract_tags("[emotion:joie] [action:salut] Coucou *sourit* toi !")
    assert clean == "Coucou toi !"
    assert tags == [{"type": "emotion", "value": "joie"}, {"type": "action", "value": "salut"}]


def test_sentence_buffer_streams_sentences():
    buf, out = SentenceBuffer(), []
    for tok in ["Salut", " toi", ". Comment", " ça va", " aujourd'hui ?", " Moi bien"]:
        out += buf.push(tok)
    out += buf.flush()
    assert out == ["Salut toi.", "Comment ça va aujourd'hui ?", "Moi bien"]


def test_sentence_buffer_early_comma_cut_for_latency():
    buf = SentenceBuffer()
    out = buf.push("Alors écoute, je crois que j'ai une idée géniale pour ce soir")
    assert out == ["Alors écoute,"]


def test_sentence_buffer_merges_short_sentences():
    buf, out = SentenceBuffer(), []
    for tok in ["Bonjour.", " Oui.", " Bien sûr que je viens ce soir.", " Super"]:
        out += buf.push(tok)
    assert out == ["Bonjour.", "Oui. Bien sûr que je viens ce soir."]


def test_sentence_buffer_ignores_punctuation_inside_tag():
    buf = SentenceBuffer()
    assert buf.push("[action:se_lever") == []


def test_persona_rejects_minor(tmp_path):
    f = tmp_path / "p.yaml"
    f.write_text("name: X\nage: 17\ndescription: a\npersonality: b\nspeaking_style: c\n", encoding="utf-8")
    with pytest.raises(ValueError):
        Persona.load(f)


def test_example_persona_and_prompt():
    p = Persona.load(ROOT / "persona" / "exemple.yaml")
    from datetime import datetime, timedelta
    now = datetime(2026, 10, 25, 21, 0)
    prompt = build_system_prompt(p, {}, ["On a parlé de Lyon"], ["Il aime le café"], "J'ai dessiné.",
                                 now, now - timedelta(days=2))
    assert "Léna, 26 ans" in prompt and "2 jours" in prompt and "Il aime le café" in prompt


def test_reflection_changes_are_capped():
    s = apply_reflection({"affection": 0.5}, {"affection_delta": 0.9, "humeur": "ravie",
                                              "note_personnalite": "plus tendre"})
    assert s["affection"] == 0.55 and s["humeur"] == "ravie" and s["notes"] == ["plus tendre"]


def test_memory_recall_blur_and_forget(tmp_path):
    now = [1_000_000.0]
    mem = Memory(tmp_path / "m.db", MemoryConfig(half_life_days=10), clock=lambda: now[0])
    mem.add("episode", "Marc m'a parlé de son entretien chez Airbus, il était stressé",
            vague="Il avait un entretien quelque part", importance=0.4)
    mem.add("episode", "On a ri de mon chat Pixel qui a renversé le café", importance=0.9, pinned=True)
    mem.add_fact("Il s'appelle Marc")
    mem.add_fact("Il s'appelle Marc")
    assert mem.facts() == ["Il s'appelle Marc"]
    assert any("Airbus" in m for m in mem.recall("comment s'est passé ton entretien chez Airbus ?"))

    now[0] += 25 * DAY  # le souvenir faible devient flou
    assert mem.decay()["floutes"] == 1
    assert "Il avait un entretien quelque part" in mem.recall("entretien")

    now[0] += 120 * DAY  # puis il disparaît; l'épinglé et le fait restent
    assert mem.decay()["oublies"] == 1
    assert mem.recall("entretien Airbus") == []
    assert mem.facts() == ["Il s'appelle Marc"]


def test_vad_segmenter_events():
    cfg = VADConfig(end_silence_ms=150, min_speech_ms=100, barge_in_ms=120)
    seg = SpeechSegmenter(cfg, is_speech=lambda f: f[0] == 1)
    speech, silence = bytes([1]) * FRAME_BYTES, bytes([0]) * FRAME_BYTES
    events = seg.feed(speech * 10 + silence * 6)
    kinds = [e for e, _ in events]
    assert kinds == ["start", "barge_in", "end"]
    assert len(events[-1][1]) >= FRAME_BYTES * 10


def test_parse_json_tolerates_text_around():
    assert parse_json('Voici : {"a": 1} merci') == {"a": 1}


class FakeServices:
    def __init__(self, reply="[emotion:joie] Coucou toi. Ça va ?", delay=0.0):
        self.reply, self.delay, self.spoken = reply, delay, []

    async def stream_chat(self, messages, cfg):
        self.last_messages = messages
        for word in self.reply.split(" "):
            await asyncio.sleep(self.delay)
            yield word + " "

    async def complete(self, messages, cfg, max_tokens=500, temperature=0.4):
        return json.dumps({"souvenir": "Il m'a dit bonjour", "souvenir_flou": "Un bonjour",
                           "importance": 0.6, "faits": ["Il aime le chocolat"], "humeur": "contente",
                           "affection_delta": 0.02})

    async def transcribe(self, wav, cfg):
        return "bonjour"

    async def synthesize(self, text, cfg):
        self.spoken.append(text)
        return b"RIFFaudio"


def make_session(tmp_path, services):
    cfg = Config(memory=MemoryConfig(reflect_every_turns=100))
    deps = Deps(cfg=cfg, persona=Persona.load(ROOT / "persona" / "exemple.yaml"),
                memory=Memory(tmp_path / "m.db", cfg.memory), services=services)
    sent, audio = [], []

    async def sj(m):
        sent.append(m)

    async def sb(b):
        audio.append(b)

    return Session(deps, sj, sb, segmenter=SpeechSegmenter(cfg.vad, is_speech=lambda f: False)), sent, audio


@pytest.mark.asyncio
async def test_session_turn_streams_text_tags_and_audio(tmp_path):
    svc = FakeServices()
    s, sent, audio = make_session(tmp_path, svc)
    await s.start()
    await s.user_message("Salut", images=["data:image/png;base64,AAA"])
    await s.turn
    types = [m["type"] for m in sent]
    assert any(m["type"] == "tag" and m["value"] == "joie" for m in sent)
    assert svc.spoken == ["Coucou toi.", "Ça va ?"]
    assert len(audio) == 2 and types[-1] == "assistant_end"
    assert svc.last_messages[-1]["content"][1]["type"] == "image_url"

    await s.close()  # réflexion de fin de session
    assert s.d.memory.facts() == ["Il aime le chocolat"]
    assert s.state()["humeur"] == "contente"


@pytest.mark.asyncio
async def test_session_barge_in_cancels_response(tmp_path):
    svc = FakeServices(reply=" ".join(["mot."] * 50), delay=0.01)
    s, sent, _ = make_session(tmp_path, svc)
    await s.start()
    await s.user_message("Raconte-moi une histoire")
    await asyncio.sleep(0.05)
    await s.interrupt()
    assert any(m["type"] == "stop" for m in sent)
    assert s.history[-1]["content"].endswith("…")
    assert len(svc.spoken) < 50
