"""OpenAI Apps domain check: plain text when set, 404 when unset, no auth."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import artifactsmith.server as server

_PATH = "/.well-known/openai-apps-challenge"


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AM_OPENAI_APPS_CHALLENGE", raising=False)
    monkeypatch.delenv("AM_OPENAI_APPS_CHALLENGE_FILE", raising=False)


def _clients() -> list[TestClient]:
    app = server.BearerAuth(server.mcp.streamable_http_app())
    return [TestClient(app), TestClient(server.ApiApp())]


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-token"}])
def test_challenge_404_when_unset_or_empty(monkeypatch: pytest.MonkeyPatch, headers: dict[str, str]) -> None:
    _clear(monkeypatch)
    for client in _clients():
        missing = client.get(_PATH, headers=headers)
        assert missing.status_code == 404
        assert missing.headers["content-type"].startswith("text/plain")
        assert "www-authenticate" not in missing.headers
        assert missing.content == b"not found"
    monkeypatch.setenv("AM_OPENAI_APPS_CHALLENGE", "")
    empty = _clients()[0].get(_PATH)
    assert empty.status_code == 404
    assert empty.content == b"not found"
    monkeypatch.setenv("AM_OPENAI_APPS_CHALLENGE", " \n")
    blank = _clients()[0].get(_PATH)
    assert blank.status_code == 404
    assert blank.content == b"not found"


def test_challenge_returns_token_without_trailing_newline(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("AM_OPENAI_APPS_CHALLENGE", "directory-challenge\n")
    for client in _clients():
        page = client.get(_PATH)
        assert page.status_code == 200
        assert page.headers["content-type"].startswith("text/plain")
        assert "www-authenticate" not in page.headers
        assert page.content == b"directory-challenge"
        assert not page.content.endswith(b"\n")
    preview = TestClient(server.preview_app)
    assert preview.get(_PATH).status_code == 404
