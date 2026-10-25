"""Clients des services distants (RunPod via tunnel SSH), tous au format OpenAI.

- LLM  : KoboldCpp  -> /v1/chat/completions (flux SSE)
- STT  : KoboldCpp  -> /v1/audio/transcriptions (Whisper intégré)
- TTS  : AllTalk v2 -> /v1/audio/speech
"""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from .config import LLMConfig, STTConfig, TTSConfig

_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0))
    return _client


async def stream_chat(messages: list[dict], cfg: LLMConfig) -> AsyncIterator[str]:
    payload = {
        "model": cfg.model,
        "messages": messages,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
        "stream": True,
    }
    async with client().stream("POST", f"{cfg.base_url}/chat/completions", json=payload) as r:
        r.raise_for_status()
        async for line in r.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                delta = json.loads(data)["choices"][0].get("delta", {})
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
            if delta.get("content"):
                yield delta["content"]


async def complete(messages: list[dict], cfg: LLMConfig, max_tokens: int = 500,
                   temperature: float = 0.4) -> str:
    payload = {"model": cfg.model, "messages": messages,
               "temperature": temperature, "max_tokens": max_tokens}
    r = await client().post(f"{cfg.base_url}/chat/completions", json=payload)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


async def transcribe(wav: bytes, cfg: STTConfig) -> str:
    files = {"file": ("audio.wav", wav, "audio/wav")}
    data = {"model": cfg.model, "language": cfg.language}
    r = await client().post(f"{cfg.base_url}/audio/transcriptions", files=files, data=data)
    r.raise_for_status()
    return r.json().get("text", "").strip()


async def synthesize(text: str, cfg: TTSConfig) -> bytes:
    payload = {"model": cfg.model, "input": text, "voice": cfg.voice,
               "response_format": cfg.response_format, "speed": cfg.speed}
    r = await client().post(f"{cfg.base_url}/audio/speech", json=payload)
    r.raise_for_status()
    return r.content
