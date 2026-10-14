"""My pages: paste-token session, workspace isolation, CSRF, and search."""

from __future__ import annotations

import hashlib
import json
import re
import time

import pytest
from starlette.testclient import TestClient
from tests.unit.test_server import FakeStore

import artifactsmith.server as server
from artifactsmith.service import Service

TOKEN = "asmb_test_token_value_ok"
OTHER = "asmb_other_token_value_ok"


def open_pages_service(monkeypatch, tmp_path):
    monkeypatch.setattr("artifactsmith.service.Store", FakeStore)
    monkeypatch.setattr(server.CFG, "data_dir", tmp_path)
    monkeypatch.setattr(server.CFG, "secrets_dir", tmp_path / "secrets")
    monkeypatch.setattr(server.CFG, "allowed_hosts", ["127.0.0.1:8780", "localhost:8780"])
    monkeypatch.setattr(server.CFG, "cookie_secure", True)
    monkeypatch.setattr(server.CFG, "share_url", "http://127.0.0.1:8781")
    monkeypatch.setattr(server.CFG, "login_limit", 8)
    monkeypatch.setattr(server.CFG, "login_prefix_limit", 8)
    monkeypatch.setattr(server.CFG, "login_window_s", 900)
    monkeypatch.setattr(server.CFG, "trusted_proxies", ())
    (tmp_path / "secrets").mkdir(parents=True, exist_ok=True)
    service = Service()
    server.SVC = service
    return service


@pytest.fixture
def svc(monkeypatch, tmp_path):
    service = open_pages_service(monkeypatch, tmp_path)
    yield service
    server.SVC = None


def _client() -> TestClient:
    # https so a Secure cookie is stored and sent. The app is not doing TLS; the client is.
    return TestClient(server.ApiApp(), base_url="https://127.0.0.1:8780")


def _token(svc: Service, raw: str, *, name: str, workspace: str, perms: list[str] | None = None) -> None:
    svc.db.exec(
        "INSERT INTO tokens (id,token_hash,name,workspace,perms,created_at,revoked_at) VALUES (?,?,?,?,?,?,NULL)",
        "tok_" + name,
        hashlib.sha256(raw.encode()).hexdigest(),
        name,
        workspace,
        json.dumps(perms or ["read"]),
        time.time(),
    )


def _artifact(
    svc: Service,
    *,
    aid: str,
    workspace: str,
    slug: str,
    title: str,
    status: str,
    summary: str = "",
    error: str = "",
    started: float | None = None,
    finished: float | None = None,
    updated: float | None = None,
) -> None:
    now = time.time() if updated is None else updated
    svc.db.exec(
        "INSERT INTO artifacts (id,workspace,slug,display_name,kind,format,created_by,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        aid,
        workspace,
        slug,
        title,
        "web_static",
        "html",
        "agent",
        now,
        now,
    )
    job = "job_" + aid
    svc.db.exec(
        "INSERT INTO jobs (id,artifact_id,version,client,status,progress,error,created_at,started_at,finished_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        job,
        aid,
        1,
        "tok_agent",
        status,
        "build note",
        error or None,
        now,
        started,
        finished,
    )
    svc.db.exec(
        "INSERT INTO versions (artifact_id,version,base_version,verbatim_request,status,job_id,summary,style,format,"
        "created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        aid,
        1,
        None,
        "make a page",
        status,
        job,
        summary,
        "house",
        "html",
        "agent",
        now,
    )


def _csrf(html: str) -> str:
    match = re.search(r'name=csrf value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def _sign_in(client: TestClient, raw: str = TOKEN):
    page = client.get("/app/login")
    assert page.status_code == 200
    return client.post("/app/login", data={"csrf": _csrf(page.text), "token": raw}, follow_redirects=False)


def test_login_sets_a_strict_cookie_and_hides_the_token(svc: Service) -> None:
    _token(svc, TOKEN, name="agent", workspace="alpha")
    client = _client()
    leaked = client.get("/app/login", params={"token": TOKEN})
    assert TOKEN not in leaked.text
    response = _sign_in(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/app/"
    assert TOKEN not in response.headers["location"]
    cookie = " ".join(response.headers.get_list("set-cookie")).lower()
    assert "am_session=" in cookie
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=strict" in cookie
    assert "path=/app" in cookie
    assert TOKEN not in cookie
    stored = svc.db.all("SELECT id_hash, token_id, csrf FROM web_sessions")
    assert stored and stored[0]["token_id"] == "tok_agent"
    assert TOKEN not in json.dumps(stored)
    assert "am_session=" not in json.dumps(stored)
    home = client.get("/app/")
    assert home.status_code == 200
    assert TOKEN not in home.text
    assert "script-src 'none'" in home.headers["content-security-policy"]
    assert "<script" not in home.text.lower()
    assert "cdn" not in home.text.lower()
    nonce = re.search(r"style-src 'nonce-([^']+)'", home.headers["content-security-policy"])
    assert nonce is not None
    assert f'nonce="{nonce.group(1)}"' in home.text


def test_login_rejects_a_bad_csrf_and_rate_limits(svc: Service, monkeypatch: pytest.MonkeyPatch) -> None:
    _token(svc, TOKEN, name="agent", workspace="alpha")
    monkeypatch.setattr(server.CFG, "login_limit", 2)
    client = _client()
    page = client.get("/app/login")
    missing = client.post("/app/login", data={"csrf": "nope", "token": TOKEN}, follow_redirects=False)
    assert missing.status_code == 403
    forged = client.post(
        "/app/login",
        data={"csrf": _csrf(page.text) + "x", "token": TOKEN},
        headers={"Origin": "https://evil.example"},
        follow_redirects=False,
    )
    assert forged.status_code == 403
    first = _sign_in(client, "asmb_wrong_token_value_01")
    second = _sign_in(client, "asmb_wrong_token_value_02")
    third = _sign_in(client, "asmb_wrong_token_value_03")
    assert first.status_code == 401
    assert second.status_code == 401
    assert third.status_code == 429
    assert third.headers["retry-after"]


def test_revoked_token_and_sign_out_lose_access(svc: Service) -> None:
    _token(svc, TOKEN, name="agent", workspace="alpha")
    client = _client()
    assert _sign_in(client).status_code == 303
    svc.db.exec("UPDATE tokens SET revoked_at=? WHERE id=?", time.time(), "tok_agent")
    blocked = client.get("/app/", follow_redirects=False)
    assert blocked.status_code == 303
    assert blocked.headers["location"] == "/app/login"
    svc.db.exec("UPDATE tokens SET revoked_at=NULL WHERE id=?", "tok_agent")
    assert _sign_in(client).status_code == 303
    home = client.get("/app/")
    gone = client.post("/app/logout", data={"csrf": _csrf(home.text)}, follow_redirects=False)
    assert gone.status_code == 303
    again = client.get("/app/", follow_redirects=False)
    assert again.status_code == 303
    assert again.headers["location"] == "/app/login"


def test_other_workspace_is_404_and_search_filters(svc: Service) -> None:
    _token(svc, TOKEN, name="agent", workspace="alpha")
    _token(svc, OTHER, name="beta", workspace="beta")
    now = time.time()
    _artifact(
        svc,
        aid="art_harbor",
        workspace="alpha",
        slug="harbor-brief",
        title="Harbor brief",
        status="done",
        summary="Ship note",
        error="builder output failed checks twice: script tag present in sanitized output",
        started=now - 12,
        finished=now,
    )
    _artifact(svc, aid="art_warehouse", workspace="alpha", slug="warehouse", title="Warehouse", status="failed")
    _artifact(svc, aid="art_beta", workspace="beta", slug="beta-page", title="BETA-TITLE", status="done")
    svc.db.exec(
        "INSERT INTO shares (id,artifact_id,version,sha256,created_by,created_at,expires_at,revoked_at,pinned) "
        "VALUES (?,?,?,?,?,?,?,NULL,0)",
        "sidharbor",
        "art_harbor",
        1,
        "abc",
        "agent",
        now,
        now + 86400,
    )
    client = _client()
    assert _sign_in(client).status_code == 303
    home = client.get("/app/")
    assert "Harbor brief" in home.text
    assert "BETA-TITLE" not in home.text
    assert "not shared" in home.text
    assert "/s/sidharbor/" in home.text
    by_title = client.get("/app/", params={"q": "Harbor"})
    assert "Harbor brief" in by_title.text
    assert "Warehouse" not in by_title.text
    by_slug = client.get("/app/", params={"q": "warehouse"})
    assert "Warehouse" in by_slug.text
    assert "Harbor brief" not in by_slug.text
    wildcard = client.get("/app/", params={"q": "%"})
    assert "No pages match." in wildcard.text
    failed = client.get("/app/", params={"status": "failed"})
    assert "Warehouse" in failed.text
    assert "Harbor brief" not in failed.text
    missing = client.get("/app/a/art_beta")
    assert missing.status_code == 404
    assert "BETA-TITLE" not in missing.text
    detail = client.get("/app/a/art_harbor")
    assert detail.status_code == 200
    assert "12s" in detail.text
    assert "script tag present in sanitized output" in detail.text
    assert "Ship note" in detail.text
    assert "BETA-TITLE" not in detail.text


def test_token_without_read_is_rejected(svc: Service) -> None:
    _token(svc, TOKEN, name="agent", workspace="alpha", perms=["create"])
    client = _client()
    response = _sign_in(client)
    assert response.status_code == 401
    assert "cannot open My pages" in response.text


def test_preview_origin_has_no_pages_and_mcp_still_requires_a_bearer(svc: Service) -> None:
    preview = TestClient(server.preview_app, base_url="http://127.0.0.1:8781")
    assert preview.get("/app/login").status_code == 404
    api = _client()
    assert api.get("/app/login").status_code == 200
    denied = api.get("/mcp")
    assert denied.status_code == 401
    bad = TestClient(server.ApiApp(), base_url="http://evil.example")
    assert bad.get("/app/login").status_code == 400


def test_logout_without_csrf_stays_signed_in(svc: Service) -> None:
    _token(svc, TOKEN, name="agent", workspace="alpha")
    client = _client()
    assert _sign_in(client).status_code == 303
    denied = client.post("/app/logout", data={"csrf": "nope"}, follow_redirects=False)
    assert denied.status_code == 403
    assert client.get("/app/").status_code == 200
