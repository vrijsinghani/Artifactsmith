"""LLM adapter. AM_LLM_API=chat (Chat Completions, default) or "responses". The output is one text string
that the builder parses; this module never executes model output."""

from __future__ import annotations

import httpx

from .config import CFG


class LLMError(RuntimeError):
    pass


async def call_chat(model: str, system: str, user: str, timeout: float = 300) -> str:
    key = CFG.llm_key()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0,
    }
    url = CFG.llm_base.rstrip("/") + "/v1/chat/completions"
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=15)) as client:
        r = await client.post(url, json=payload, headers=headers)
    if r.status_code != 200:
        raise LLMError(f"LLM HTTP {r.status_code}: {r.text[:400]}")
    data = r.json()
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"unexpected chat-completions response: {str(data)[:400]}") from e
    if not text.strip():
        raise LLMError("empty model output")
    return text


async def call_responses(model: str, system: str, user: str, timeout: float = 300) -> str:
    key = CFG.llm_key()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {
        "model": model,
        "input": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    url = CFG.llm_base.rstrip("/") + "/v1/responses"
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=15)) as client:
        r = await client.post(url, json=payload, headers=headers)
    if r.status_code != 200:
        raise LLMError(f"LLM HTTP {r.status_code}: {r.text[:400]}")
    data = r.json()
    text = "".join(
        c.get("text", "")
        for item in data.get("output", [])
        for c in item.get("content", [])
        if c.get("type") in ("output_text", "text")
    )
    if not text.strip():
        raise LLMError("empty model output")
    return text


async def call(model: str, system: str, user: str, timeout: float = 300) -> str:
    if CFG.llm_api == "responses":
        return await call_responses(model, system, user, timeout)
    return await call_chat(model, system, user, timeout)
