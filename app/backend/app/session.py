"""Une conversation : voix en temps réel, interruption, mémoire et réflexion."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable

from . import services as default_services
from . import teaching
from .audio import SpeechSegmenter, pcm_to_wav
from .config import Config
from .memory import Memory
from .persona import DEFAULT_STATE, Persona, apply_reflection, build_system_prompt
from .text import SentenceBuffer, extract_tags

log = logging.getLogger("compagne")

REFLECT_PROMPT = """Tu es {name}. Relis la conversation ci-dessous entre toi et {user}, puis réponds
UNIQUEMENT avec un objet JSON, sans texte autour, de la forme :
{{
  "souvenir": "ce que tu retiens de ce moment, 1 à 3 phrases, à la première personne",
  "souvenir_flou": "le même souvenir en une phrase vague, comme un souvenir lointain",
  "importance": 0.0 à 1.0 (1 = moment marquant),
  "faits": ["faits durables et nouveaux sur {user}, ex. 'Sa sœur s'appelle Léa'"],
  "humeur": "ton humeur après cet échange, un ou deux mots",
  "affection_delta": -0.05 à 0.05,
  "confiance_delta": -0.05 à 0.05,
  "note_personnalite": "une évolution de ton caractère ou de votre relation, ou chaîne vide"
}}

Conversation :
{transcript}"""

JOURNAL_PROMPT = """Tu es {name}. {user} et toi ne vous êtes pas parlé depuis {hours} heures.
Écris en 3 à 4 phrases, à la première personne et au passé composé, ce que tu as fait et pensé
pendant ce temps, de façon cohérente avec ta vie et ta personnalité. Ajoute une chose dont tu as
envie de lui parler. Pas de liste, pas d'emoji.

Qui tu es : {description}
Ta personnalité : {personality}
Ce que tu as fait la dernière fois : {last}"""


def parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


@dataclass
class Deps:
    cfg: Config
    persona: Persona
    memory: Memory
    services: object = default_services


class Session:
    def __init__(self, deps: Deps, send_json: Callable[[dict], Awaitable[None]],
                 send_bytes: Callable[[bytes], Awaitable[None]], segmenter: SpeechSegmenter | None = None):
        self.d = deps
        self.svc = deps.services
        self.send_json = send_json
        self.send_bytes = send_bytes
        self.segmenter = segmenter or SpeechSegmenter(deps.cfg.vad)
        self.history: list[dict] = []
        self.transcript: list[str] = []
        self.turn: asyncio.Task | None = None
        self.background: set[asyncio.Task] = set()
        self.turns_since_reflect = 0
        self.prev_seen: datetime | None = None
        self.journal: str | None = None

    # --- cycle de vie --------------------------------------------------------
    async def start(self) -> None:
        mem = self.d.memory
        stats = mem.decay()
        last = mem.get("last_seen")
        self.prev_seen = datetime.fromtimestamp(last) if last else None
        if last and (time.time() - last) / 3600 >= self.d.cfg.memory.journal_after_hours:
            await self._write_journal((time.time() - last) / 3600)
        mem.set("last_seen", time.time())
        await self.send_json({"type": "state", "state": self.state(), "memoire": stats,
                              "name": self.d.persona.name, "persona": self.d.persona.public()})

    async def close(self) -> None:
        await self.interrupt(notify=False)
        if self.transcript:
            await self.reflect()
        self.d.memory.set("last_seen", time.time())
        for t in list(self.background):
            t.cancel()

    def state(self) -> dict:
        return {**DEFAULT_STATE, **self.d.memory.get("state", {})}

    # --- entrées ---------------------------------------------------------------
    async def on_audio(self, chunk: bytes) -> None:
        for event, pcm in self.segmenter.feed(chunk):
            if event == "start":
                await self.send_json({"type": "vad", "state": "start"})
            elif event == "barge_in":
                # tu parles par-dessus elle : on coupe la génération et la lecture côté navigateur
                if self.responding:
                    await self.interrupt()
                else:
                    await self.send_json({"type": "stop"})
            elif event == "noise":
                await self.send_json({"type": "vad", "state": "idle"})
            elif event == "end":
                await self.send_json({"type": "vad", "state": "thinking"})
                self._spawn(self.on_speech(pcm))

    async def on_speech(self, pcm: bytes) -> None:
        try:
            text = await self.svc.transcribe(pcm_to_wav(pcm), self.d.cfg.stt)
        except Exception as e:  # service injoignable
            await self.send_json({"type": "error", "message": f"Transcription impossible : {e}"})
            return
        if text and len(text) > 1:
            await self.user_message(text, source="voix")
        else:
            await self.send_json({"type": "vad", "state": "idle"})

    async def user_message(self, text: str, images: list[str] | None = None, source: str = "chat") -> None:
        await self.interrupt()
        await self.send_json({"type": "user", "text": text, "source": source, "images": len(images or [])})
        self.d.memory.set("last_seen", time.time())
        self.transcript.append(f"{self.d.persona.user_name}: {text}" + (" [envoie une image]" if images else ""))
        self.turn = asyncio.create_task(self._respond(text, images or []))

    @property
    def search_enabled(self) -> bool:
        return "recherche_web" in self.d.persona.outils and self.d.cfg.recherche_web.enabled

    @property
    def responding(self) -> bool:
        return self.turn is not None and not self.turn.done()

    async def interrupt(self, notify: bool = True) -> None:
        if self.responding:
            self.turn.cancel()
            try:
                await self.turn
            except asyncio.CancelledError:
                pass
            if notify:
                await self.send_json({"type": "stop"})

    # --- réponse ---------------------------------------------------------------
    def _messages(self, text: str, images: list[str]) -> list[dict]:
        mem, p = self.d.memory, self.d.persona
        last_reply = next((m["content"] for m in reversed(self.history) if m["role"] == "assistant"), "")
        system = build_system_prompt(
            p, self.state(), mem.recall(f"{text} {last_reply}"), mem.facts(),
            self.journal, datetime.now(), self.prev_seen,
            teaching.tools_prompt(p.outils, self.search_enabled))
        content: str | list = text
        if images:
            content = [{"type": "text", "text": text}] + [
                {"type": "image_url", "image_url": {"url": url}} for url in images]
        keep = self.d.cfg.llm.history_turns * 2
        return [{"role": "system", "content": system}, *self.history[-keep:], {"role": "user", "content": content}]

    async def _respond(self, text: str, images: list[str]) -> None:
        messages = self._messages(text, images)
        self.history.append({"role": "user", "content": text + (" [image envoyée]" if images else "")})
        raw, spoken = "", []
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        tts = asyncio.create_task(self._speak(queue))
        try:
            # Deux passes au plus : la réponse, puis sa suite si le prof a demandé
            # une recherche web au milieu.
            for attempt in range(2):
                buf, blocks, query = SentenceBuffer(), teaching.BlockParser(), None
                async with aclosing(self.svc.stream_chat(messages, self.d.cfg.llm)) as stream:
                    async for token in stream:
                        raw += token
                        query = await self._feed(blocks.push(token), buf, queue, spoken, attempt)
                        if query:
                            break
                if query is None:
                    query = await self._feed(blocks.flush(), buf, queue, spoken, attempt)
                for sentence in buf.flush():
                    await self._emit(sentence, queue, spoken)
                if query is None:
                    break
                messages = [*messages, {"role": "assistant", "content": raw},
                            {"role": "user", "content": await self._research(query)}]
            await queue.put(None)
            await tts
        except asyncio.CancelledError:
            tts.cancel()
            raw = " ".join(spoken) + "…"
            raise
        except Exception as e:
            tts.cancel()
            await self.send_json({"type": "error", "message": f"Réponse impossible : {e}"})
        finally:
            clean = " ".join(spoken)
            if clean:
                self.history.append({"role": "assistant", "content": raw})
                self.transcript.append(f"{self.d.persona.name}: {clean}")
            await self.send_json({"type": "assistant_end"})
            self.turns_since_reflect += 1
            if self.turns_since_reflect >= self.d.cfg.memory.reflect_every_turns:
                self._spawn(self.reflect())

    async def _feed(self, segments: list[tuple[str, str]], buf: SentenceBuffer, queue: asyncio.Queue,
                    spoken: list[str], attempt: int) -> str | None:
        """Route les morceaux du flux : texte vers la voix, blocs vers le tableau ou la recherche.

        Retourne la question à chercher quand le prof en a écrit une.
        """
        for kind, content in segments:
            if kind == "text":
                for sentence in buf.push(content):
                    await self._emit(sentence, queue, spoken)
            elif kind == "tableau" and "tableau" in self.d.persona.outils:
                event = teaching.board_event(content)
                if event:
                    await self.send_json(event)
                    self.transcript.append(f"{self.d.persona.name} écrit au tableau : {content[:200]}")
            elif kind == "recherche" and self.search_enabled and attempt == 0 and content.strip():
                return content.strip()[:200]
        return None

    async def _research(self, query: str) -> str:
        await self.send_json({"type": "search", "query": query})
        try:
            sources = await self.svc.web_search(query, self.d.cfg.recherche_web)
        except Exception as e:  # réseau, moteur qui bloque : le prof continue sans
            log.warning("recherche impossible: %s", e)
            return teaching.RESEARCH_UNAVAILABLE.format(query=query, reason="recherche indisponible")
        if not sources:
            return teaching.RESEARCH_UNAVAILABLE.format(query=query, reason="aucun résultat")
        await self.send_json({"type": "sources", "query": query,
                              "items": [{"titre": s["titre"], "url": s["url"]} for s in sources]})
        return teaching.RESEARCH_FOLLOWUP.format(query=query, sources=teaching.format_sources(sources))

    async def _emit(self, sentence: str, queue: asyncio.Queue, spoken: list[str]) -> None:
        clean, tags = extract_tags(sentence)
        for tag in tags:
            await self.send_json({"type": "tag", "kind": tag["type"], "value": tag["value"]})
        if clean:
            spoken.append(clean)
            await self.send_json({"type": "assistant_delta", "text": clean})
            await queue.put(clean)

    async def _speak(self, queue: asyncio.Queue) -> None:
        while (sentence := await queue.get()) is not None:
            try:
                audio = await self.svc.synthesize(sentence, self.d.cfg.tts)
            except Exception as e:
                await self.send_json({"type": "error", "message": f"Voix indisponible : {e}"})
                continue
            await self.send_bytes(audio)

    # --- mémoire ---------------------------------------------------------------
    async def reflect(self) -> None:
        if not self.transcript:
            return
        lines, self.transcript = self.transcript, []
        self.turns_since_reflect = 0
        p = self.d.persona
        prompt = REFLECT_PROMPT.format(name=p.name, user=p.user_name, transcript="\n".join(lines[-60:]))
        try:
            out = await self.svc.complete([{"role": "user", "content": prompt}], self.d.cfg.llm)
        except Exception as e:
            log.warning("réflexion impossible: %s", e)
            self.transcript = lines + self.transcript
            return
        refl = parse_json(out)
        if not refl:
            return
        mem = self.d.memory
        if refl.get("souvenir"):
            try:
                importance = float(refl.get("importance", 0.5))
            except (TypeError, ValueError):
                importance = 0.5
            mem.add("episode", refl["souvenir"], vague=refl.get("souvenir_flou"), importance=importance)
        for fact in refl.get("faits") or []:
            if isinstance(fact, str) and fact.strip():
                mem.add_fact(fact.strip())
        new_state = apply_reflection(self.state(), refl)
        mem.set("state", new_state)
        await self.send_json({"type": "state", "state": new_state})

    async def _write_journal(self, hours: float) -> None:
        p = self.d.persona
        prompt = JOURNAL_PROMPT.format(name=p.name, user=p.user_name, hours=int(hours),
                                       description=p.description, personality=p.personality,
                                       last=self.d.memory.last_journal() or "rien de particulier")
        try:
            self.journal = (await self.svc.complete([{"role": "user", "content": prompt}],
                                                    self.d.cfg.llm, temperature=0.9)).strip()
        except Exception as e:
            log.warning("journal impossible: %s", e)
            return
        self.d.memory.add("journal", self.journal, importance=0.3)

    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self.background.add(t)
        t.add_done_callback(self.background.discard)
