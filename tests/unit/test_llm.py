from __future__ import annotations

import json

import pytest

from artifactsmith import llm


class _StreamResp:
    def __init__(self, status_code: int, payload: dict | bytes):
        self.status_code = status_code
        if isinstance(payload, bytes):
            self._body = payload
        else:
            self._body = json.dumps(payload).encode()

    async def aiter_bytes(self):
        # Yield in small chunks so the byte-cap path is exercised.
        chunk = 64 * 1024
        for i in range(0, len(self._body), chunk):
            yield self._body[i : i + chunk]

    async def aclose(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class _Client:
    def __init__(self, resp: _StreamResp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def stream(self, method, url, json=None, headers=None):  # noqa: A002
        return self._resp


@pytest.mark.asyncio
async def test_call_chat_reads_content(monkeypatch):
    resp = _StreamResp(200, {"choices": [{"message": {"content": "hello"}}]})
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: _Client(resp))
    assert await llm.call_chat("m", "sys", "user") == "hello"


@pytest.mark.asyncio
async def test_call_chat_empty(monkeypatch):
    resp = _StreamResp(200, {"choices": [{"message": {"content": "  "}}]})
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: _Client(resp))
    with pytest.raises(llm.LLMError, match="empty"):
        await llm.call_chat("m", "sys", "user")


@pytest.mark.asyncio
async def test_call_chat_http_and_shape(monkeypatch):
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: _Client(_StreamResp(500, {})))
    with pytest.raises(llm.LLMError, match="HTTP 500"):
        await llm.call_chat("m", "sys", "user")
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: _Client(_StreamResp(200, {"choices": []})))
    with pytest.raises(llm.LLMError, match="unexpected"):
        await llm.call_chat("m", "sys", "user")


@pytest.mark.asyncio
async def test_call_chat_caps_stream_bytes(monkeypatch):
    huge = b"x" * (llm.LLM_RESPONSE_CAP_BYTES + 10)
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: _Client(_StreamResp(200, huge)))
    with pytest.raises(llm.LLMError, match="exceeds"):
        await llm.call_chat("m", "sys", "user")


@pytest.mark.asyncio
async def test_call_responses_and_dispatch(monkeypatch):
    payload = {"output": [{"content": [{"type": "output_text", "text": "hi"}]}]}
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: _Client(_StreamResp(200, payload)))
    assert await llm.call_responses("m", "sys", "user") == "hi"
    monkeypatch.setattr(llm.CFG, "llm_api", "responses")
    assert await llm.call("m", "sys", "user") == "hi"
    monkeypatch.setattr(llm.CFG, "llm_api", "chat")
    monkeypatch.setattr(
        llm.httpx,
        "AsyncClient",
        lambda **kw: _Client(_StreamResp(200, {"choices": [{"message": {"content": "c"}}]})),
    )
    assert await llm.call("m", "sys", "user") == "c"


@pytest.mark.asyncio
async def test_call_responses_empty(monkeypatch):
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: _Client(_StreamResp(200, {"output": []})))
    with pytest.raises(llm.LLMError, match="empty"):
        await llm.call_responses("m", "sys", "user")
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: _Client(_StreamResp(503, {})))
    with pytest.raises(llm.LLMError, match="HTTP 503"):
        await llm.call_responses("m", "sys", "user")
