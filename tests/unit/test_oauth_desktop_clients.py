"""Desktop MCP clients: the callbacks they register and the sign-in page they see."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from tests.unit.test_oauth import _code_from, _exchange, _start, oauth_http, svc  # noqa: F401

from artifactsmith.oauth_origin import client_redirect_ok, form_source, redirect_allowed
from artifactsmith.web.pages import security_headers

CURSOR_WEB = "https://www.cursor.com/agents/mcp/oauth/callback"
CURSOR_APP = "cursor://anysphere.cursor-mcp/oauth/callback"


def test_known_desktop_callbacks_are_allowed():
    assert redirect_allowed("http://127.0.0.1:65060/callback")  # Codex CLI
    assert redirect_allowed("http://localhost:8787/callback")  # Cursor CLI, Claude Code
    assert redirect_allowed("http://localhost:3334/oauth/callback")  # mcp-remote
    assert redirect_allowed("http://127.0.0.1:33418/")  # VS Code
    assert redirect_allowed("http://127.0.0.1:33418")
    assert redirect_allowed(CURSOR_WEB)
    assert redirect_allowed(CURSOR_APP)
    assert redirect_allowed("https://vscode.dev/redirect")


def test_lookalike_callbacks_stay_denied():
    for uri in (
        "http://127.0.0.1:1/steal",
        "http://127.0.0.1:1/oauth/callback/x",
        "http://127.0.0.1:1/?x=1",
        "http://127.0.0.1.evil.com:1/oauth/callback",
        "http://localhost@evil.com/oauth/callback",
        "https://127.0.0.1:1/callback",
        "cursor://anysphere.cursor-mcp/oauth/callback/x",
        "cursor://evil/oauth/callback",
        "https://www.cursor.com.evil.com/agents/mcp/oauth/callback",
        "https://www.cursor.com/agents/mcp/oauth/callback?x=1",
        "javascript://anysphere.cursor-mcp/oauth/callback",
    ):
        assert not redirect_allowed(uri), uri


def test_loopback_port_may_change_but_not_the_path():
    assert client_redirect_ok(["http://127.0.0.1:1/oauth/callback"], "http://127.0.0.1:2/oauth/callback")
    assert not client_redirect_ok(["http://127.0.0.1:1/oauth/callback"], "http://127.0.0.1:2/callback")


def test_cursor_app_scheme_reaches_the_csp_only_when_allowed():
    assert form_source(CURSOR_APP) == "cursor:"
    assert form_source("cursor://evil/x") == ""
    assert "form-action 'self' cursor:;" in security_headers("n", "cursor:")["Content-Security-Policy"]
    assert "evil:" not in security_headers("n", "evil:")["Content-Security-Policy"]


def test_cursor_registration_keeps_allowed_callbacks_and_drops_the_rest(oauth_http):  # noqa: F811
    client, _box = oauth_http
    made = client.post(
        "/oauth/register",
        json={
            "redirect_uris": [CURSOR_WEB, "http://localhost:8787/callback", "https://evil.example/cb"],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "client_name": "Cursor",
        },
    )
    assert made.status_code == 201, made.text
    assert made.json()["redirect_uris"] == [CURSOR_WEB, "http://localhost:8787/callback"]
    none_ok = client.post(
        "/oauth/register",
        json={"redirect_uris": ["https://evil.example/cb"], "token_endpoint_auth_method": "none"},
    )
    assert none_ok.status_code == 400
    assert none_ok.json()["error"] == "invalid_redirect_uri"
    bad_type = client.post(
        "/oauth/register",
        json={"redirect_uris": [CURSOR_WEB, 7], "token_endpoint_auth_method": "none"},
    )
    assert bad_type.status_code == 400


def test_cursor_loopback_sign_in_end_to_end(oauth_http):  # noqa: F811
    client, _box = oauth_http
    made = client.post(
        "/oauth/register",
        json={"redirect_uris": [CURSOR_WEB, "http://localhost:8787/callback"], "token_endpoint_auth_method": "none"},
    )
    cid = made.json()["client_id"]
    redirect = "http://localhost:8787/callback"
    verifier, csrf = _start(client, redirect=redirect, client_id=cid)
    done = client.post("/oauth/authorize", data={"csrf": csrf, "email": "cursor@example.com"}, follow_redirects=False)
    assert urlparse(done.headers["location"]).path == "/callback"
    code = _code_from(done)
    assert _exchange(client, code, verifier, client_id=cid, redirect=redirect).status_code == 200


def test_sign_in_page_says_what_it_is_and_hides_reviewer_fields(oauth_http):  # noqa: F811
    client, _box = oauth_http
    made = client.post(
        "/oauth/register",
        json={"redirect_uris": ["http://127.0.0.1:65060/callback"], "token_endpoint_auth_method": "none"},
    )
    page = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": made.json()["client_id"],
            "redirect_uri": "http://127.0.0.1:65060/callback",
            "state": "s",
            "code_challenge": "a" * 43,
            "code_challenge_method": "S256",
        },
    )
    text = page.text
    assert "Sign in to ArtifactSmith" in text
    assert "creates your account" in text
    assert "No password needed" in text
    assert "Claude Code, Codex or Cursor" in text
    assert re.search(r"<details><summary>Directory reviewer</summary>.*name=password", text)
    assert re.search(r"<details><summary>Already have an ArtifactSmith token\?</summary>.*name=hosted_token", text)
    assert text.index("name=email") < text.index("<details>")
