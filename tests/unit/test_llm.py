from __future__ import annotations

import json

import httpx
import pytest

from artifactsmith import llm


@pytest.fixture(autouse=True)
def _clear_temperature_memory():
    llm._MODELS_WITHOUT_TEMPERATURE.clear()


_RealAsyncClient = httpx.AsyncClient


def _install_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)

    def _client(**kwargs):
        kwargs["transport"] = transport
        return _RealAsyncClient(**kwargs)

    monkeypatch.setattr(llm.httpx, "AsyncClient", _client)


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


def _ok_chat(text: str = "ok") -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


@pytest.mark.asyncio
async def test_call_chat_retries_once_without_temperature_on_400(monkeypatch):
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append(payload)
        if "temperature" in payload:
            return httpx.Response(
                400,
                json={"error": {"message": "`temperature` is deprecated for this model"}},
            )
        return _ok_chat()

    _install_transport(monkeypatch, handler)
    assert await llm.call_chat("anthropic/claude-haiku-5-5", "sys", "user") == "ok"
    assert len(seen) == 2
    assert seen[0]["temperature"] == 0
    assert "temperature" not in seen[1]


@pytest.mark.asyncio
async def test_call_chat_does_not_retry_unrelated_400(monkeypatch):
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"message": "context length exceeded"}})

    _install_transport(monkeypatch, handler)
    with pytest.raises(llm.LLMError, match="HTTP 400"):
        await llm.call_chat("m", "sys", "user")
    assert len(seen) == 1
    assert seen[0]["temperature"] == 0


@pytest.mark.asyncio
async def test_call_chat_remembers_models_without_temperature(monkeypatch):
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append(payload)
        if payload["model"] == "no-temp" and "temperature" in payload:
            return httpx.Response(
                400,
                json={"error": {"message": "Unsupported parameter: Temperature"}},
            )
        return _ok_chat()

    _install_transport(monkeypatch, handler)
    assert await llm.call_chat("no-temp", "sys", "user") == "ok"
    assert await llm.call_chat("no-temp", "sys", "user") == "ok"
    assert await llm.call_chat("other", "sys", "user") == "ok"
    assert [p.get("model") for p in seen] == ["no-temp", "no-temp", "no-temp", "other"]
    assert "temperature" in seen[0]
    assert "temperature" not in seen[1]
    assert "temperature" not in seen[2]
    assert seen[3]["temperature"] == 0


@pytest.mark.asyncio
async def test_call_chat_omits_temperature_when_env_none(monkeypatch):
    monkeypatch.setenv("AM_LLM_TEMPERATURE", "none")
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _ok_chat()

    _install_transport(monkeypatch, handler)
    assert await llm.call_chat("m", "sys", "user") == "ok"
    assert len(seen) == 1
    assert "temperature" not in seen[0]
