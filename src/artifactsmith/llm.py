"""LLM adapter. AM_LLM_API=chat (Chat Completions, default) or "responses". The output is one text string
that the builder parses; this module never executes model output."""

from __future__ import annotations

import logging

import httpx

from .config import CFG

log = logging.getLogger("artifactsmith.llm")

# Cap the raw HTTP response body while streaming so a runaway reply cannot fill memory.
LLM_RESPONSE_CAP_BYTES = 2_000_000


class LLMError(RuntimeError):
    pass


def _scrub_http_error(status: int, body: str) -> LLMError:
    log.warning("LLM HTTP %s body_prefix=%r", status, body[:200])
    return LLMError(f"LLM HTTP {status}")


async def _read_capped(response: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > LLM_RESPONSE_CAP_BYTES:
            await response.aclose()
            raise LLMError(f"model response exceeds {LLM_RESPONSE_CAP_BYTES} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


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
    url = CFG.normalized_llm_base() + "/v1/chat/completions"
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=15)) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as r:
            body = await _read_capped(r)
            if r.status_code != 200:
                raise _scrub_http_error(r.status_code, body.decode("utf-8", errors="replace"))
    data = httpx.Response(200, content=body).json()
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        log.warning("unexpected chat-completions shape: %s", type(data).__name__)
        raise LLMError("unexpected chat-completions response") from e
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
    url = CFG.normalized_llm_base() + "/v1/responses"
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=15)) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as r:
            body = await _read_capped(r)
            if r.status_code != 200:
                raise _scrub_http_error(r.status_code, body.decode("utf-8", errors="replace"))
    data = httpx.Response(200, content=body).json()
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
