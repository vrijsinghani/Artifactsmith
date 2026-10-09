from __future__ import annotations

import pytest

from artifactsmith import llm


class _Resp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self.text = "err"
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, resp: _Resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, json, headers):
        return self._resp


@pytest.mark.asyncio
async def test_call_chat_reads_content(monkeypatch):
    resp = _Resp(200, {"choices": [{"message": {"content": "hello"}}]})
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: _Client(resp))
    assert await llm.call_chat("m", "sys", "user") == "hello"


@pytest.mark.asyncio
async def test_call_chat_empty(monkeypatch):
    resp = _Resp(200, {"choices": [{"message": {"content": "  "}}]})
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: _Client(resp))
    with pytest.raises(llm.LLMError, match="empty"):
        await llm.call_chat("m", "sys", "user")


@pytest.mark.asyncio
async def test_call_chat_http_and_shape(monkeypatch):
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: _Client(_Resp(500, {})))
    with pytest.raises(llm.LLMError, match="HTTP 500"):
        await llm.call_chat("m", "sys", "user")
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: _Client(_Resp(200, {"choices": []})))
    with pytest.raises(llm.LLMError, match="unexpected"):
        await llm.call_chat("m", "sys", "user")


@pytest.mark.asyncio
async def test_call_responses_and_dispatch(monkeypatch):
    payload = {"output": [{"content": [{"type": "output_text", "text": "hi"}]}]}
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: _Client(_Resp(200, payload)))
    assert await llm.call_responses("m", "sys", "user") == "hi"
    monkeypatch.setattr(llm.CFG, "llm_api", "responses")
    assert await llm.call("m", "sys", "user") == "hi"
    monkeypatch.setattr(llm.CFG, "llm_api", "chat")
    monkeypatch.setattr(
        llm.httpx, "AsyncClient", lambda **kw: _Client(_Resp(200, {"choices": [{"message": {"content": "c"}}]}))
    )
    assert await llm.call("m", "sys", "user") == "c"


@pytest.mark.asyncio
async def test_call_responses_empty(monkeypatch):
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: _Client(_Resp(200, {"output": []})))
    with pytest.raises(llm.LLMError, match="empty"):
        await llm.call_responses("m", "sys", "user")
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: _Client(_Resp(503, {})))
    with pytest.raises(llm.LLMError, match="HTTP 503"):
        await llm.call_responses("m", "sys", "user")
