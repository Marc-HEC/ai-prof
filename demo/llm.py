"""Client LLM compatible OpenAI (Groq, Gemini, Ollama, OpenAI...) en streaming.

Un seul client pour tous : ils exposent tous /chat/completions avec stream=true.
Le mode "mock" repond sans reseau, pour verifier l'interface sans cle.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

import httpx

from config import Endpoint


class LLMError(RuntimeError):
    pass


async def stream_chat(
    endpoint: Endpoint,
    messages: list[dict],
    extra: dict | None = None,
    temperature: float = 0.6,
) -> AsyncIterator[str]:
    if endpoint.is_mock:
        async for delta in _mock_stream(messages):
            yield delta
        return

    headers = {"Content-Type": "application/json"}
    if endpoint.api_key:
        headers["Authorization"] = f"Bearer {endpoint.api_key}"
    body = {
        "model": endpoint.model,
        "messages": messages,
        "stream": True,
        "temperature": temperature,
        **(extra or {}),
    }
    url = f"{endpoint.base_url}/chat/completions"
    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, headers=headers, json=body) as resp:
            if resp.status_code >= 400:
                detail = (await resp.aread()).decode("utf-8", "replace")[:500]
                raise LLMError(f"{endpoint.provider} HTTP {resp.status_code} : {detail}")
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                for choice in chunk.get("choices") or []:
                    delta = (choice.get("delta") or {}).get("content")
                    if delta:
                        yield delta


async def list_models(endpoint: Endpoint) -> list[str]:
    if endpoint.is_mock:
        return ["mock"]
    headers = {"Authorization": f"Bearer {endpoint.api_key}"} if endpoint.api_key else {}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{endpoint.base_url}/models", headers=headers)
        if resp.status_code >= 400:
            raise LLMError(f"HTTP {resp.status_code} : {resp.text[:300]}")
        data = resp.json().get("data", [])
        return sorted(str(m.get("id", "")).removeprefix("models/") for m in data)


# -- mock ------------------------------------------------------------------

def _last_user_text(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        for part in content or []:
            if part.get("type") == "text":
                return part.get("text", "")
    return ""


def _mock_reply(messages: list[dict]) -> str:
    question = _last_user_text(messages).strip() or "rien"
    has_image = any(
        isinstance(m.get("content"), list)
        and any(p.get("type") == "image_url" for p in m["content"])
        for m in messages
    )
    seen = " Et j'ai bien vu ton dessin au tableau." if has_image else ""
    return (
        f"[emotion:joie] Tres bonne question ! Tu m'as dit : {question[:80]}.{seen} "
        "Je t'ecris l'idee au tableau. "
        "<tableau>\n### La derivee en une ligne\n"
        "$$f'(a) = \\lim_{h \\to 0} \\frac{f(a+h) - f(a)}{h}$$\n\n"
        "**Exemple** : si $f(x) = x^2$ alors $f'(x) = 2x$.\n</tableau> "
        "[emotion:pensif] Tu vois, la derivee c'est la pente de la tangente. "
        "A toi : quelle est la derivee de x au cube ?"
    )


async def _mock_stream(messages: list[dict]) -> AsyncIterator[str]:
    text = _mock_reply(messages)
    step = 7  # des morceaux de taille irreguliere, comme un vrai flux
    for i in range(0, len(text), step):
        await asyncio.sleep(0.02)
        yield text[i : i + step]
