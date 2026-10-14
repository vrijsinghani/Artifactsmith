"""My pages login buckets, paging, and labeled local times."""

from __future__ import annotations

import ipaddress
import sqlite3
from datetime import UTC, datetime

import pytest
from starlette.requests import Request
from starlette.testclient import TestClient
from tests.unit.test_web_pages import TOKEN, _artifact, _client, _csrf, _sign_in, _token, open_pages_service

import artifactsmith.server as server
from artifactsmith.config import ConfigError, _trusted_proxies
from artifactsmith.db import DB
from artifactsmith.service import Service
from artifactsmith.web.client_addr import client_ip

_PROXY = ipaddress.ip_network("192.168.30.160/32")
WHEN = datetime(2026, 1, 15, 18, 0, tzinfo=UTC).timestamp()


def _request(peer: str, headers: dict[str, str]) -> Request:
    raw = [(key.lower().encode(), value.encode()) for key, value in headers.items()]
    scope = {
        "type": "http",
        "client": (peer, 443),
        "headers": raw,
        "method": "POST",
        "path": "/app/login",
        "scheme": "https",
        "query_string": b"",
        "server": ("127.0.0.1", 8780),
    }
    return Request(scope)


def _proxy_client() -> TestClient:
    return TestClient(server.ApiApp(), base_url="https://127.0.0.1:8780", client=("192.168.30.160", 443))


def test_untrusted_peer_ignores_forwarded_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server.CFG, "trusted_proxies", (_PROXY,))
    peer = _request("198.51.100.4", {"x-real-ip": "203.0.113.9", "x-forwarded-for": "203.0.113.8, 203.0.113.9"})
    assert client_ip(peer) == "198.51.100.4"
    trusted = _request("192.168.30.160", {"x-real-ip": "203.0.113.9", "x-forwarded-for": "198.51.100.8"})
    assert client_ip(trusted) == "203.0.113.9"
    forwarded = _request("192.168.30.160", {"x-forwarded-for": "198.51.100.8, 203.0.113.10"})
    assert client_ip(forwarded) == "203.0.113.10"
    missing = _request("192.168.30.160", {"x-real-ip": "not-an-ip"})
    assert client_ip(missing) == "192.168.30.160"


def test_trusted_proxy_setting_rejects_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AM_TRUSTED_PROXIES", "not-an-ip")
    with pytest.raises(ConfigError, match="AM_TRUSTED_PROXIES"):
        _trusted_proxies()


def test_forwarded_headers_do_not_split_an_untrusted_peer(svc: Service, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server.CFG, "login_limit", 2)
    client = _client()
    for n, addr in enumerate(("203.0.113.1", "203.0.113.2", "203.0.113.3")):
        page = client.get("/app/login")
        response = client.post(
            "/app/login",
            data={"csrf": _csrf(page.text), "token": f"miss-{n}-token"},
            headers={"X-Forwarded-For": addr, "X-Real-IP": addr},
            follow_redirects=False,
        )
        assert response.status_code == (429 if n == 2 else 401)


def test_prefix_limit_holds_across_client_addresses(svc: Service, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server.CFG, "login_limit", 8)
    monkeypatch.setattr(server.CFG, "login_prefix_limit", 2)
    monkeypatch.setattr(server.CFG, "trusted_proxies", (_PROXY,))
    client = _proxy_client()
    codes = []
    for n, addr in enumerate(("203.0.113.1", "203.0.113.2", "203.0.113.3")):
        page = client.get("/app/login")
        response = client.post(
            "/app/login",
            data={"csrf": _csrf(page.text), "token": f"sameprefix1-{n}-value"},
            headers={"X-Real-IP": addr},
            follow_redirects=False,
        )
        codes.append(response.status_code)
    assert codes == [401, 401, 429]
    stored = svc.db.all("SELECT ip, kind FROM login_attempts")
    blob = str(stored)
    assert "sameprefix1" not in blob
    assert sum(1 for row in stored if row["kind"] == "prefix") == 2
    other = client.post(
        "/app/login",
        data={"csrf": _csrf(client.get("/app/login").text), "token": "otherprefix9-value"},
        headers={"X-Real-IP": "203.0.113.4"},
        follow_redirects=False,
    )
    assert other.status_code == 401


def test_list_pages_past_fifty_and_rejects_a_bad_page(svc: Service) -> None:
    _token(svc, TOKEN, name="agent", workspace="alpha")
    for n in range(51):
        _artifact(
            svc,
            aid=f"art_{n:02d}",
            workspace="alpha",
            slug=f"item-{n:02d}",
            title=f"Item {n:02d}",
            status="done",
            updated=1_000 + n,
        )
    client = _client()
    assert _sign_in(client).status_code == 303
    first = client.get("/app/", params={"q": "Item", "status": "done"})
    assert "Item 50" in first.text
    assert "Item 00" not in first.text
    assert "Next" in first.text
    assert "q=Item" in first.text
    assert "status=done" in first.text
    assert "page=2" in first.text
    second = client.get("/app/", params={"q": "Item", "status": "done", "page": "2"})
    assert "Item 00" in second.text
    assert "Item 50" not in second.text
    assert "Previous" in second.text
    assert "Next" not in second.text
    bad = client.get("/app/", params={"page": "0"})
    assert bad.status_code == 400
    huge = client.get("/app/", params={"page": "201"})
    assert huge.status_code == 400
    word = client.get("/app/", params={"page": "two"})
    assert word.status_code == 400


def test_times_default_to_new_york_and_honor_a_chosen_zone(svc: Service) -> None:
    _token(svc, TOKEN, name="agent", workspace="alpha")
    _artifact(
        svc,
        aid="art_when",
        workspace="alpha",
        slug="when",
        title="When page",
        status="done",
        updated=WHEN,
    )
    client = _client()
    assert _sign_in(client).status_code == 303
    home = client.get("/app/")
    assert "Times shown in America/New_York" in home.text
    assert "Jan 15, 2026, 1:00 PM" in home.text
    assert "+00:00" not in home.text
    west = client.get("/app/", params={"tz": "America/Los_Angeles"})
    assert "Times shown in America/Los_Angeles" in west.text
    assert "Jan 15, 2026, 10:00 AM" in west.text
    cookie = " ".join(west.headers.get_list("set-cookie")).lower()
    assert 'am_tz="america/los_angeles"' in cookie
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    kept = client.get("/app/")
    assert "Times shown in America/Los_Angeles" in kept.text
    bad = client.get("/app/a/art_when", params={"tz": "Not/AZone"})
    assert "Times shown in America/Los_Angeles" in bad.text
    assert "Jan 15, 2026, 10:00 AM" in bad.text
    fresh = _client()
    assert _sign_in(fresh).status_code == 303
    fallback = fresh.get("/app/", params={"tz": "Not/AZone"})
    assert "Times shown in America/New_York" in fallback.text
    assert "am_tz=" not in " ".join(fallback.headers.get_list("set-cookie")).lower()


def test_login_attempt_kind_migrates(tmp_path) -> None:
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE login_attempts (ip TEXT NOT NULL, created_at REAL NOT NULL)")
    conn.execute("INSERT INTO login_attempts (ip, created_at) VALUES ('203.0.113.9', 10)")
    conn.commit()
    conn.close()
    db = DB(path)
    cols = {str(row[1]) for row in db.conn.execute("PRAGMA table_info(login_attempts)").fetchall()}
    assert "kind" in cols
    row = db.one("SELECT kind FROM login_attempts WHERE ip=?", "203.0.113.9")
    assert row is not None
    assert row["kind"] == "ip"


@pytest.fixture
def svc(monkeypatch, tmp_path):
    service = open_pages_service(monkeypatch, tmp_path)
    yield service
    server.SVC = None
