"""PR #67 regressions. Each probe asserts the fixed behavior."""

# Fixture names are imported and then used as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import re
import time
from urllib.parse import parse_qs, urlparse

from tests.unit.test_oauth import CLAUDE, _code_from, _exchange, _start, oauth_http, svc  # noqa: F401

from artifactsmith.config import CFG
from artifactsmith.oauth_demo import ensure, password_matches
from artifactsmith.oauth_grants import resolve_bearer
from artifactsmith.oauth_origin import redirect_allowed
from artifactsmith.trial import CLOSED, TrialMailError, email_hash, mint_trial, normalize_email

H = {"origin": "https://127.0.0.1:8780"}


def _post(client, csrf, email, password=""):
    return client.post(
        "/oauth/authorize",
        data={"csrf": csrf, "email": email, "password": password},
        headers=H,
        follow_redirects=False,
    )


def _who(client, svc, box, verifier, link):
    path = urlparse(link)
    page = client.get(path.path + "?" + path.query)
    tok = re.search(r'name=csrf value="([^"]+)"', page.text)
    assert tok
    done = client.post(
        "/oauth/confirm",
        data={"csrf": tok.group(1), "code": parse_qs(path.query)["code"][0]},
        headers=H,
        follow_redirects=False,
    )
    issued = _exchange(client, _code_from(done), verifier).json()
    return resolve_bearer(svc.db, issued["access_token"])["id"]


def _pair(svc):
    victim = email_hash(normalize_email("victim@example.com"))
    attacker = email_hash(normalize_email("attacker@example.com"))
    mint_trial(svc.db, victim, verified=True)
    mint_trial(svc.db, attacker, verified=True)
    victim_id = svc.db.one("SELECT token_id FROM trial_signups WHERE email_hash=?", victim)["token_id"]
    attacker_id = svc.db.one("SELECT token_id FROM trial_signups WHERE email_hash=?", attacker)["token_id"]
    return victim_id, attacker_id


def test_t1_own_link_stays_bound_after_a_second_email(oauth_http, svc, monkeypatch):
    client, box = oauth_http
    monkeypatch.setattr(CFG, "trial_email_verify", True)
    victim_id, attacker_id = _pair(svc)
    verifier, csrf = _start(client)
    first = _post(client, csrf, "attacker@example.com")
    assert "Check your email" in first.text
    link = box["url"]
    second = _post(client, csrf, "victim@example.com")
    assert second.status_code == 403
    assert "Start the sign-in again." in second.text
    assert _who(client, svc, box, verifier, link) == attacker_id
    assert attacker_id != victim_id


def test_t1_mail_failure_cannot_retarget_the_link(oauth_http, svc, monkeypatch):
    client, box = oauth_http
    monkeypatch.setattr(CFG, "trial_email_verify", True)
    victim_id, attacker_id = _pair(svc)

    def send(to: str, url: str, *, intro: str | None = None) -> None:
        box["url"] = url
        if to == "victim@example.com":
            raise TrialMailError("sign-up email is not available")

    monkeypatch.setattr("artifactsmith.trial.send_magic_link", send)
    verifier, csrf = _start(client)
    assert "Check your email" in _post(client, csrf, "attacker@example.com").text
    link = box["url"]
    second = _post(client, csrf, "victim@example.com")
    assert second.status_code == 403
    assert _who(client, svc, box, verifier, link) == attacker_id
    assert attacker_id != victim_id


def test_t1_rate_limited_mail_cannot_retarget_the_link(oauth_http, svc, monkeypatch):
    client, box = oauth_http
    monkeypatch.setattr(CFG, "trial_email_verify", True)
    victim_id, attacker_id = _pair(svc)
    verifier, csrf = _start(client)
    assert "Check your email" in _post(client, csrf, "attacker@example.com").text
    link = box["url"]
    for _ in range(8):
        svc.db.exec(
            "INSERT INTO login_attempts (ip, kind, created_at) VALUES (?,?,?)",
            "testclient",
            "oauth_mail",
            time.time(),
        )
    second = _post(client, csrf, "victim@example.com")
    assert second.status_code == 403
    assert _who(client, svc, box, verifier, link) == attacker_id
    assert attacker_id != victim_id


def test_t2_dcr_success_is_rate_limited(oauth_http, svc):
    client, _box = oauth_http
    ok = 0
    for _ in range(60):
        made = client.post("/oauth/register", json={"redirect_uris": [CLAUDE], "token_endpoint_auth_method": "none"})
        ok += made.status_code == 201
    assert ok == 10


def test_t3_authorize_get_keeps_at_most_twenty_rows(oauth_http, svc):
    client, _box = oauth_http
    for _ in range(50):
        _start(client)
    n = svc.db.one("SELECT COUNT(*) AS n FROM oauth_pending")["n"]
    assert n == 20


def test_t4_demo_password_locks_and_rejects_the_right_password(oauth_http, svc, monkeypatch):
    client, _box = oauth_http
    monkeypatch.setattr(CFG, "oauth_demo_email", "review@example.com")
    monkeypatch.setattr(CFG, "oauth_demo_password", "right-password")
    seen = {"n": 0}

    def spy(password: str) -> bool:
        seen["n"] += 1
        return password_matches(password)

    monkeypatch.setattr("artifactsmith.web.oauth_login.password_matches", spy)
    _verifier, csrf = _start(client)
    codes = []
    for i in range(8):
        wrong = _post(client, csrf, "review@example.com", f"guess-{i}")
        codes.append(wrong.status_code)
        found = re.search(r'name=csrf value="([^"]+)"', wrong.text)
        assert found
        csrf = found.group(1)
    locked = _post(client, csrf, "review@example.com", "guess-9")
    right = _post(client, csrf, "review@example.com", "right-password")
    assert codes == [400] * 8
    assert locked.status_code == 429
    assert right.status_code == 429
    assert seen["n"] == 8
    assert "location" not in right.headers


def test_t4_demo_global_ceiling_is_twenty(oauth_http, svc, monkeypatch):
    client, _box = oauth_http
    monkeypatch.setattr(CFG, "login_limit", 100)
    monkeypatch.setattr(CFG, "oauth_demo_email", "review@example.com")
    monkeypatch.setattr(CFG, "oauth_demo_password", "right-password")
    _verifier, csrf = _start(client)
    for i in range(20):
        wrong = _post(client, csrf, "review@example.com", f"guess-{i}")
        assert wrong.status_code == 400
        found = re.search(r'name=csrf value="([^"]+)"', wrong.text)
        assert found
        csrf = found.group(1)
    blocked = _post(client, csrf, "review@example.com", "right-password")
    assert blocked.status_code == 429
    assert "location" not in blocked.headers


def test_t5_demo_revocation_survives_authorize_get(oauth_http, svc, monkeypatch):
    client, _box = oauth_http
    monkeypatch.setattr(CFG, "oauth_demo_email", "review@example.com")
    monkeypatch.setattr(CFG, "oauth_demo_password", "right-password")
    ensure(svc.db, svc.store)
    svc.db.exec("UPDATE tokens SET revoked_at=1 WHERE id='tok_oauth_demo'")
    _start(client)
    ensure(svc.db, svc.store)
    row = svc.db.one("SELECT revoked_at FROM tokens WHERE id='tok_oauth_demo'")
    assert row["revoked_at"] is not None


def test_t6_redirects_outside_the_two_callbacks_are_denied():
    probes = [
        "https://chatgpt.com/aip/g-x#/oauth/callback",
        "https://chatgpt.com/aip/g-x?a=b/oauth/callback",
        "https://chatgpt.com/connector/oauth/x?next=https:",
        "https://chatgpt.com/connector/oauth/%2e%2e",
        "https://claude.ai/api/mcp/auth_callback/",
        "https://claude.ai/api/mcp/auth_callback?x=1",
        "https://claude.ai.evil.com/api/mcp/auth_callback",
        "http://claude.ai/api/mcp/auth_callback",
        "https://CLAUDE.ai/api/mcp/auth_callback",
        "http://127.0.0.1:1/callback?x=1",
        "http://127.0.0.1.evil.com:1/callback",
        "http://localhost@evil.com/callback",
        "https://chatgpt.com/connector/oauth/cb_123",
        "https://chatgpt.com/aip/g-abc/oauth/callback",
        "https://chat.openai.com/aip/g-abc/oauth/callback",
    ]
    assert not any(redirect_allowed(item) for item in probes)


def test_t7_demo_obeys_the_kill_switch(oauth_http, svc, monkeypatch):
    client, _box = oauth_http
    monkeypatch.setattr(CFG, "oauth_demo_email", "review@example.com")
    monkeypatch.setattr(CFG, "oauth_demo_password", "right-password")
    ensure(svc.db, svc.store)
    _verifier, csrf = _start(client)
    monkeypatch.setattr(CFG, "trial_enabled", False)
    denied = _post(client, csrf, "review@example.com", "right-password")
    assert denied.status_code != 303
    assert "location" not in denied.headers
    assert "closed" in denied.text.lower()
    principal = {"id": "tok_oauth_demo", "workspace": "oauth-demo", "perms": set(), "demo": True}
    with svc.db.tx() as conn:
        try:
            svc._check_quota(conn, principal)
            blocked = False
        except Exception:
            blocked = True
    assert blocked
    assert CLOSED
