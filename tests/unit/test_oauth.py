"""OAuth authorization server, demo review account, and privacy draft."""

from __future__ import annotations

import base64
import hashlib
import re
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient
from tests.unit.test_service import FakeStore

import artifactsmith.server as server
from artifactsmith.config import CFG, ConfigError, _demo_pair, _oauth_static_client
from artifactsmith.oauth_demo import DEMO_ARTIFACT, DEMO_TOKEN_ID, ensure
from artifactsmith.oauth_grants import resolve_bearer
from artifactsmith.oauth_origin import redirect_allowed
from artifactsmith.service import AMError, Service
from artifactsmith.trial import email_hash, mint_trial, normalize_email

CLAUDE = "https://claude.ai/api/mcp/auth_callback"
CHATGPT = "https://chatgpt.com/connector_platform_oauth_redirect"


def test_demo_settings_are_a_pair(monkeypatch):
    monkeypatch.setenv("AM_OAUTH_DEMO_EMAIL", "review@example.com")
    monkeypatch.delenv("AM_OAUTH_DEMO_PASSWORD", raising=False)
    with pytest.raises(ConfigError):
        _demo_pair()
    monkeypatch.setenv("AM_OAUTH_DEMO_PASSWORD", "review-secret")
    assert _demo_pair() == ("review@example.com", "review-secret")


def test_static_client_id_rejects_spaces(monkeypatch):
    monkeypatch.setenv("AM_OAUTH_STATIC_CLIENT_ID", "bad id")
    with pytest.raises(ConfigError):
        _oauth_static_client()


def test_redirect_allowlist_matches_the_published_callbacks():
    assert redirect_allowed(CLAUDE)
    assert redirect_allowed(CHATGPT)
    assert redirect_allowed("https://chatgpt.com/connector/oauth/cb_123")
    assert redirect_allowed("http://127.0.0.1:3456/callback")
    assert redirect_allowed("http://localhost:9/callback")
    assert not redirect_allowed("http://127.0.0.1:3456/steal")
    assert not redirect_allowed("https://evil.example/callback")
    assert not redirect_allowed("http://[::1]:3456/callback")


@pytest.fixture
def oauth_http(svc, monkeypatch):
    monkeypatch.setattr(CFG, "trial_enabled", True)
    monkeypatch.setattr(CFG, "trial_email_verify", False)
    monkeypatch.setattr(CFG, "cookie_secure", True)
    monkeypatch.setattr(CFG, "allowed_hosts", ["127.0.0.1:8780", "localhost:8780"])
    monkeypatch.setattr(CFG, "api_url", "https://trial.example")
    monkeypatch.setattr(CFG, "smtp_host", "smtp.example")
    monkeypatch.setattr(CFG, "smtp_from", "trial@example.com")
    monkeypatch.setattr(CFG, "trial_build_limit", 20)
    monkeypatch.setattr(CFG, "trial_builds_per_hour", 10)
    box: dict[str, str] = {}

    def fake_send(to: str, url: str, *, intro: str | None = None) -> None:
        box["to"] = to
        box["url"] = url
        box["intro"] = intro or ""

    monkeypatch.setattr("artifactsmith.trial.send_magic_link", fake_send)
    server.SVC = svc
    client = TestClient(server.ApiApp(), base_url="https://127.0.0.1:8780")
    yield client, box
    server.SVC = None


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setattr("artifactsmith.service.Store", FakeStore)
    return Service()


def _pkce() -> tuple[str, str]:
    verifier = "a" * 50
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _start(client: TestClient, redirect: str = CLAUDE, client_id: str = "artifactsmith") -> tuple[str, str]:
    verifier, challenge = _pkce()
    page = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect,
            "state": "state-1",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    assert page.status_code == 200, page.text
    found = re.search(r'name=csrf value="([^"]+)"', page.text)
    assert found
    return verifier, found.group(1)


def _code_from(response) -> str:
    assert response.status_code == 303, response.text
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["iss"] == ["https://trial.example"]
    assert query["state"] == ["state-1"]
    return query["code"][0]


def _exchange(client: TestClient, code: str, verifier: str, redirect: str = CLAUDE, client_id: str = "artifactsmith"):
    return client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": client_id,
            "redirect_uri": redirect,
        },
    )


def test_metadata_stays_404_on_a_private_origin(svc, monkeypatch):
    monkeypatch.setattr(CFG, "api_url", "http://127.0.0.1:8780")
    monkeypatch.setattr(CFG, "allowed_hosts", ["127.0.0.1:8780"])
    server.SVC = svc
    client = TestClient(server.ApiApp(), base_url="https://127.0.0.1:8780")
    page = client.get("/.well-known/oauth-authorization-server")
    assert page.status_code == 404
    assert "127.0.0.1" not in page.text
    denied = client.post("/mcp", headers={"Accept": "application/json, text/event-stream"})
    assert denied.headers["www-authenticate"] == "Bearer"
    server.SVC = None


def test_metadata_advertises_dcr_not_cimd(oauth_http):
    client, _box = oauth_http
    page = client.get("/.well-known/oauth-authorization-server")
    body = page.json()
    assert page.status_code == 200
    assert body["registration_endpoint"] == "https://trial.example/oauth/register"
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert body["token_endpoint_auth_methods_supported"] == ["none"]
    assert body["authorization_response_iss_parameter_supported"] is True
    assert "client_id_metadata_document_supported" not in body
    assert "openid" not in body["scopes_supported"]
    resource = client.get("/.well-known/oauth-protected-resource")
    suffixed = client.get("/.well-known/oauth-protected-resource/mcp")
    assert resource.json() == suffixed.json()
    assert resource.json()["resource"] == "https://trial.example/mcp"
    denied = client.post("/mcp", headers={"Accept": "application/json, text/event-stream"})
    assert 'resource_metadata="https://trial.example/.well-known/oauth-protected-resource"' in denied.headers[
        "www-authenticate"
    ]


def test_new_email_gets_a_code_and_can_call_mcp(oauth_http, svc):
    client, _box = oauth_http
    verifier, csrf = _start(client)
    jumped = client.post(
        "/oauth/authorize",
        data={"csrf": csrf, "email": "new-person@example.com", "password": ""},
        follow_redirects=False,
    )
    assert "asmb_" not in jumped.text
    code = _code_from(jumped)
    token = _exchange(client, code, verifier)
    assert token.status_code == 200, token.text
    access = token.json()["access_token"]
    principal = resolve_bearer(svc.db, access)
    assert principal is not None and principal["trial"] is True
    created = svc.create(principal, slug="one", display_name="One", verbatim_request="show the page")
    assert created["builds_remaining"] == 19
    denied = client.post(
        "/mcp",
        headers={"Authorization": "Bearer asmo_not-a-token", "Accept": "application/json, text/event-stream"},
    )
    assert denied.status_code == 401


def test_existing_email_needs_the_link_in_the_same_browser(oauth_http, svc, monkeypatch):
    monkeypatch.setattr(CFG, "trial_email_verify", False)
    digest = email_hash(normalize_email("already@example.com"))
    assert mint_trial(svc.db, digest, verified=False) is not None
    client, box = oauth_http
    _verifier, csrf = _start(client)
    waited = client.post(
        "/oauth/authorize",
        data={"csrf": csrf, "email": "already@example.com"},
        follow_redirects=False,
    )
    assert waited.status_code == 200
    assert "Check your email" in waited.text
    assert "code=" not in waited.headers.get("location", "")
    other = TestClient(server.ApiApp(), base_url="https://127.0.0.1:8780")
    peeked = other.get(box["url"].split("https://trial.example")[1])
    assert peeked.status_code == 400
    assert "browser where you started" in peeked.text
    assert svc.db.one("SELECT used_at FROM oauth_links")["used_at"] is None
    confirmed = client.get(box["url"].split("https://trial.example")[1])
    assert confirmed.status_code == 200
    csrf = re.search(r'name=csrf value="([^"]+)"', confirmed.text).group(1)
    code_field = re.search(r'name=code value="([^"]+)"', confirmed.text).group(1)
    finished = client.post("/oauth/confirm", data={"csrf": csrf, "code": code_field}, follow_redirects=False)
    code = _code_from(finished)
    assert code
    same = svc.db.one("SELECT id FROM tokens WHERE email_hash=?", digest)
    issued = _exchange(client, code, _verifier)
    assert resolve_bearer(svc.db, issued.json()["access_token"])["id"] == same["id"]


def test_bad_verifier_does_not_burn_the_code_and_replay_revokes_the_family(oauth_http):
    client, _box = oauth_http
    verifier, csrf = _start(client)
    code = _code_from(
        client.post(
            "/oauth/authorize",
            data={"csrf": csrf, "email": "replay@example.com"},
            follow_redirects=False,
        )
    )
    bad = _exchange(client, code, "b" * 50)
    assert bad.status_code == 400
    assert bad.json()["error"] == "invalid_grant"
    good = _exchange(client, code, verifier)
    assert good.status_code == 200
    replay = _exchange(client, code, verifier)
    assert replay.json()["error"] == "invalid_grant"
    blocked = client.post(
        "/mcp",
        headers={
            "Authorization": f"Bearer {good.json()['access_token']}",
            "Accept": "application/json, text/event-stream",
        },
    )
    assert blocked.status_code == 401


def test_refresh_rotation_and_revoke(oauth_http):
    client, _box = oauth_http
    verifier, challenge = _pkce()
    page = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": "artifactsmith",
            "redirect_uri": CHATGPT,
            "state": "state-1",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    assert "ChatGPT will receive the sign-in." in page.text
    csrf = re.search(r'name=csrf value="([^"]+)"', page.text).group(1)
    code = _code_from(
        client.post("/oauth/authorize", data={"csrf": csrf, "email": "refresh@example.com"}, follow_redirects=False)
    )
    first = _exchange(client, code, verifier, CHATGPT)
    body = first.json()
    rotated = client.post(
        "/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": body["refresh_token"], "client_id": "artifactsmith"},
    )
    assert rotated.status_code == 200
    stale = client.post(
        "/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": body["refresh_token"], "client_id": "artifactsmith"},
    )
    assert stale.json()["error"] == "invalid_grant"
    gone = client.post(
        "/mcp",
        headers={
            "Authorization": f"Bearer {rotated.json()['access_token']}",
            "Accept": "application/json, text/event-stream",
        },
    )
    assert gone.status_code == 401
    fresh_verifier, fresh_csrf = _start(client)
    fresh_code = _code_from(
        client.post("/oauth/authorize", data={"csrf": fresh_csrf, "email": "revoke@example.com"}, follow_redirects=False)
    )
    minted = _exchange(client, fresh_code, fresh_verifier).json()
    revoked = client.post("/oauth/revoke", data={"token": minted["access_token"]})
    assert revoked.status_code == 200
    unknown = client.post("/oauth/revoke", data={"token": "missing"})
    assert unknown.status_code == 200
    after = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {minted['refresh_token']}", "Accept": "application/json, text/event-stream"},
    )
    assert after.status_code == 401


def test_missing_state_and_bad_redirect_do_not_leave(oauth_http):
    client, _box = oauth_http
    _verifier, challenge = _pkce()
    missing = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": "artifactsmith",
            "redirect_uri": CLAUDE,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    assert missing.status_code == 400
    assert "location" not in missing.headers
    bad = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": "artifactsmith",
            "redirect_uri": "https://evil.example/callback",
            "state": "state-1",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    assert bad.status_code == 400
    assert "evil.example" not in bad.headers.get("location", "")


def test_dcr_rejects_a_secret_and_registers_a_public_client(oauth_http):
    client, _box = oauth_http
    rejected = client.post(
        "/oauth/register",
        json={"redirect_uris": [CLAUDE], "token_endpoint_auth_method": "client_secret_basic"},
    )
    assert rejected.status_code == 400
    made = client.post(
        "/oauth/register",
        json={"redirect_uris": [CLAUDE], "token_endpoint_auth_method": "none"},
    )
    assert made.status_code == 201
    assert made.json()["token_endpoint_auth_method"] == "none"
    verifier, csrf = _start(client, client_id=made.json()["client_id"])
    code = _code_from(
        client.post("/oauth/authorize", data={"csrf": csrf, "email": "dcr@example.com"}, follow_redirects=False)
    )
    issued = _exchange(client, code, verifier, client_id=made.json()["client_id"])
    assert issued.status_code == 200


def test_demo_password_uses_the_sample_token_and_its_own_cap(oauth_http, svc, monkeypatch):
    monkeypatch.setattr(CFG, "oauth_demo_email", "review@example.com")
    monkeypatch.setattr(CFG, "oauth_demo_password", "review-secret")
    monkeypatch.setattr(CFG, "oauth_demo_build_limit", 1)
    monkeypatch.setattr(CFG, "trial_builds_per_hour", 10)
    ensure(svc.db, svc.store)
    assert svc.db.one("SELECT kind FROM tokens WHERE id=?", DEMO_TOKEN_ID)["kind"] == "demo"
    assert svc.db.one("SELECT 1 AS n FROM trial_signups WHERE token_id=?", DEMO_TOKEN_ID) is None
    assert svc.db.one("SELECT display_name FROM artifacts WHERE id=?", DEMO_ARTIFACT)["display_name"] == "Sample page"
    client, box = oauth_http
    _verifier, csrf = _start(client)
    wrong = client.post(
        "/oauth/authorize",
        data={"csrf": csrf, "email": "review@example.com", "password": "nope"},
        follow_redirects=False,
    )
    assert wrong.status_code == 400
    assert "location" not in wrong.headers
    assert "url" not in box
    csrf = re.search(r'name=csrf value="([^"]+)"', wrong.text).group(1)
    right = client.post(
        "/oauth/authorize",
        data={"csrf": csrf, "email": "review@example.com", "password": "review-secret"},
        follow_redirects=False,
    )
    code = _code_from(right)
    access = _exchange(client, code, _verifier).json()["access_token"]
    principal = resolve_bearer(svc.db, access)
    assert principal["demo"] is True
    assert principal["id"] == DEMO_TOKEN_ID
    first = svc.create(principal, slug="review", display_name="Review", verbatim_request="show the page")
    assert first["builds_remaining"] == 0
    spent = svc.db.one("SELECT COALESCE(SUM(actual_micros), 0) AS n FROM trial_reservations")
    assert int(spent["n"]) == 0
    with pytest.raises(AMError, match="you've used the 1 review builds"):
        svc.create(principal, slug="review-2", display_name="Review 2", verbatim_request="show the page")


def test_revoked_hosted_token_rejects_the_oauth_access_token(oauth_http, svc):
    client, _box = oauth_http
    verifier, csrf = _start(client)
    code = _code_from(
        client.post("/oauth/authorize", data={"csrf": csrf, "email": "gone@example.com"}, follow_redirects=False)
    )
    access = _exchange(client, code, verifier).json()["access_token"]
    svc.db.exec("UPDATE tokens SET revoked_at=? WHERE email_hash=?", 1, email_hash(normalize_email("gone@example.com")))
    denied = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {access}", "Accept": "application/json, text/event-stream"},
    )
    assert denied.status_code == 401


def test_static_bearer_still_passes_the_gate(oauth_http, svc):
    raw = "asmb_staticbearer"
    svc.db.exec(
        "INSERT INTO tokens (id, token_hash, name, workspace, perms, created_at, revoked_at) "
        "VALUES (?,?,?,?,?,?,NULL)",
        "tok_static",
        hashlib.sha256(raw.encode()).hexdigest(),
        "agent",
        "alpha",
        '["create","read"]',
        1,
    )
    client, _box = oauth_http
    assert resolve_bearer(svc.db, raw)["id"] == "tok_static"
    denied = client.post(
        "/mcp",
        headers={"Authorization": "Bearer asmb_other", "Accept": "application/json, text/event-stream"},
    )
    assert denied.status_code == 401


def test_privacy_page_is_a_draft(oauth_http):
    client, _box = oauth_http
    page = client.get("/privacy")
    assert page.status_code == 200
    assert "Draft for owner approval." in page.text
    assert "SHA-256" in page.text
    assert "Claude Haiku 5.5" in page.text
    assert "trial" not in page.text.lower()
