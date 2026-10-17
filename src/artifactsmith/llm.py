"""LLM adapter. AM_LLM_API=chat (Chat Completions, default) or "responses". The output is one text string
that the builder parses; this module never executes model output."""

from __future__ import annotations

import contextvars
import json
import logging
import re
from urllib.parse import urlparse

import httpx

from . import stdio_mode
from .config import CFG

log = logging.getLogger("artifactsmith.llm")

# Cap the raw HTTP response body while streaming so a runaway reply cannot fill memory.
LLM_RESPONSE_CAP_BYTES = 2_000_000

# Models that rejected `temperature` during this process. Later calls omit the field.
_MODELS_WITHOUT_TEMPERATURE: set[str] = set()

# Set by the worker for a trial build. None for every other call.
_output_cap: contextvars.ContextVar[int | None] = contextvars.ContextVar("trial_output_cap", default=None)
# (input_tokens, output_tokens, known). known is false when the provider omitted usage.
_usage: contextvars.ContextVar[list[tuple[int, int, bool]] | None] = contextvars.ContextVar("trial_usage", default=None)


def set_output_cap(limit: int | None) -> contextvars.Token[int | None]:
    return _output_cap.set(limit)


def reset_output_cap(token: contextvars.Token[int | None]) -> None:
    _output_cap.reset(token)


def begin_usage() -> contextvars.Token[list[tuple[int, int, bool]] | None]:
    return _usage.set([])


def end_usage(token: contextvars.Token[list[tuple[int, int, bool]] | None]) -> list[tuple[int, int, bool]]:
    rows = list(_usage.get() or [])
    _usage.reset(token)
    return rows


def _apply_output_cap(payload: dict[str, object]) -> None:
    cap = _output_cap.get()
    if not cap:
        return
    from .trial import MAX_INPUT_TOKENS

    if "messages" in payload:
        payload["max_tokens"] = cap
        parts = payload["messages"]
    else:
        payload["max_output_tokens"] = cap
        parts = payload.get("input", [])
    text = ""
    if isinstance(parts, list):
        text = "".join(str(item.get("content") or "") for item in parts if isinstance(item, dict))
    if len(text) > MAX_INPUT_TOKENS * 4:
        raise LLMError("this input is too large")


def _mark_unknown() -> None:
    """A request left this process and the provider did not report usage. Keep the hold."""
    sink = _usage.get()
    if sink is not None:
        sink.append((0, 0, False))


def _record_body(body: bytes, *, chat: bool, unknown: bool) -> None:
    """Record provider counts from a response body, including a failed call.

    ``unknown`` is false for a probe this process is about to retry, so a body
    with no usage is ignored. The final body always records something: measured
    tokens when they are present, otherwise an unknown row that keeps the hold.
    """
    sink = _usage.get()
    if sink is None:
        return
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        if unknown:
            sink.append((0, 0, False))
        return
    if not isinstance(data, dict):
        if unknown:
            sink.append((0, 0, False))
        return
    usage = data.get("usage")
    if not isinstance(usage, dict):
        if unknown:
            sink.append((0, 0, False))
        return
    key_in, key_out = ("prompt_tokens", "completion_tokens") if chat else ("input_tokens", "output_tokens")
    incoming, outgoing = usage.get(key_in), usage.get(key_out)
    if not isinstance(incoming, int) or not isinstance(outgoing, int):
        if unknown:
            sink.append((0, 0, False))
        return
    sink.append((incoming, outgoing, True))


class LLMError(RuntimeError):
    pass


class MissingModelKey(LLMError):
    """Empty model credential. The text is fixed and carries no key or host."""

    message = "OPENAI_API_KEY / AM_LLM_KEY is empty"

    def __init__(self) -> None:
        super().__init__(self.message)


def openai_key_missing(base: str, key: str) -> bool:
    """True when the configured host is api.openai.com and no key is set."""
    host = (urlparse(base).hostname or "").lower()
    return (not key) and host == "api.openai.com"


def _after_transport_error(exc: Exception, *, recorded: bool) -> Exception:
    """Keep a transport failure from carrying a host into the client error."""
    if not recorded:
        _mark_unknown()
    if isinstance(exc, httpx.HTTPError) and openai_key_missing(CFG.normalized_llm_base(), CFG.llm_key()):
        log.warning("model transport failed with an empty key: %s", type(exc).__name__)
        return MissingModelKey()
    return exc


# Reasoning effort is sent only when the model name is an o-series or gpt-5 model.
# Other models omit the field. There is no probe request.
_EFFORT_MODEL = re.compile(r"(?:^|[^A-Za-z0-9])(?:o[134]|gpt-5)(?:[^A-Za-z0-9]|$)")


def supports_effort(model: str) -> bool:
    """True when this model name accepts a reasoning-effort field."""
    return bool(_EFFORT_MODEL.search(model))


def apply_effort(payload: dict[str, object], model: str, effort: str | None) -> dict[str, object]:
    """Copy ``payload`` with reasoning effort when ``model`` supports it."""
    if not effort or not supports_effort(model):
        return payload
    updated = dict(payload)
    if "messages" in updated:
        updated["reasoning_effort"] = effort
    else:
        updated["reasoning"] = {"effort": effort}
    return updated


def _scrub_http_error(status: int, body: str) -> LLMError:
    if status == 401 and not CFG.llm_key():
        log.warning("LLM HTTP 401 with an empty model key")
        return MissingModelKey()
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


async def _post_chat(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, object],
    headers: dict[str, str],
) -> tuple[int, bytes]:
    async with client.stream("POST", url, json=payload, headers=headers) as r:
        body = await _read_capped(r)
        return r.status_code, body


def _temperature_rejected(status: int, body: bytes, payload: dict[str, object]) -> bool:
    if status != 400 or "temperature" not in payload:
        return False
    return "temperature" in body.decode("utf-8", errors="replace").lower()


async def call_chat(model: str, system: str, user: str, timeout: float = 300, *, effort: str | None = None) -> str:
    stdio_mode.refuse()
    key = CFG.llm_key()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload: dict[str, object] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if CFG.llm_send_temperature() and model not in _MODELS_WITHOUT_TEMPERATURE:
        payload["temperature"] = 0
    payload = apply_effort(payload, model, effort)
    _apply_output_cap(payload)
    url = CFG.normalized_llm_base() + "/v1/chat/completions"
    recorded = False
    status = 0
    body = b""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=15)) as client:
            status, body = await _post_chat(client, url, payload, headers)
            if _temperature_rejected(status, body, payload):
                _record_body(body, chat=True, unknown=False)
                _MODELS_WITHOUT_TEMPERATURE.add(model)
                payload.pop("temperature", None)
                status, body = await _post_chat(client, url, payload, headers)
            _record_body(body, chat=True, unknown=True)
            recorded = True
    except LLMError:
        if not recorded:
            _mark_unknown()
        raise
    except Exception as exc:
        mapped = _after_transport_error(exc, recorded=recorded)
        if mapped is exc:
            raise
        raise mapped from None
    if status != 200:
        raise _scrub_http_error(status, body.decode("utf-8", errors="replace"))
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
        log.warning("unexpected chat-completions shape: %s", type(e).__name__)
        raise LLMError("unexpected chat-completions response") from e
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        log.warning("unexpected chat-completions shape: %s", type(data).__name__)
        raise LLMError("unexpected chat-completions response") from e
    if not text.strip():
        raise LLMError("empty model output")
    return text


async def call_responses(model: str, system: str, user: str, timeout: float = 300, *, effort: str | None = None) -> str:
    stdio_mode.refuse()
    key = CFG.llm_key()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = apply_effort(
        {
            "model": model,
            "input": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        },
        model,
        effort,
    )
    _apply_output_cap(payload)
    url = CFG.normalized_llm_base() + "/v1/responses"
    recorded = False
    status = 0
    body = b""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=15)) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                status = response.status_code
                body = await _read_capped(response)
            _record_body(body, chat=False, unknown=True)
            recorded = True
    except LLMError:
        if not recorded:
            _mark_unknown()
        raise
    except Exception as exc:
        mapped = _after_transport_error(exc, recorded=recorded)
        if mapped is exc:
            raise
        raise mapped from None
    if status != 200:
        raise _scrub_http_error(status, body.decode("utf-8", errors="replace"))
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
        raise LLMError("unexpected responses shape") from e
    if not isinstance(data, dict):
        raise LLMError("unexpected responses shape")
    text = "".join(
        c.get("text", "")
        for item in data.get("output", [])
        for c in item.get("content", [])
        if isinstance(c, dict) and c.get("type") in ("output_text", "text")
    )
    if not text.strip():
        raise LLMError("empty model output")
    return text


async def call(model: str, system: str, user: str, timeout: float = 300, *, effort: str | None = None) -> str:
    stdio_mode.refuse()
    if CFG.llm_api == "responses":
        return await call_responses(model, system, user, timeout, effort=effort)
    return await call_chat(model, system, user, timeout, effort=effort)
