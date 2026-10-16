"""Paste-token login, server-side sessions, CSRF, and login rate limits.

A hosted trial can call :func:`start_session` with a token id it minted.
The pages only see the same workspace principal the bearer API uses.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from ..config import CFG
from ..db import DB
from .clock import TZ_COOKIE

SESSION_COOKIE = "am_session"
CSRF_COOKIE = "am_csrf"
COOKIE_PATH = "/app"
_TOKEN_MAX = 512
_SESSION_MAX = 200
_PREFIX_CHARS = 12
_TZ_MAX_AGE = 365 * 86400


@dataclass(frozen=True)
class LoginResult:
    status: int
    message: str = ""
    session_raw: str | None = None
    retry_after: int = 0


def tokens_match(left: str, right: str) -> bool:
    if len(left) != len(right):
        return False
    return secrets.compare_digest(left, right)


def token_prefix_key(token: str) -> str | None:
    """sha256 of a short prefix. Empty and over-long tokens are not a prefix bucket."""
    text = token.strip()
    if not text or len(text) > _TOKEN_MAX:
        return None
    return hashlib.sha256(text[:_PREFIX_CHARS].encode()).hexdigest()


def same_origin(request: Request) -> bool:
    """True for a missing Origin, a matching Origin, or an opaque Origin from this site.

    Chromium sends ``Origin: null`` for a same-document form when the page asked
    not to send a referrer. That value is accepted only with ``Sec-Fetch-Site:
    same-origin``. A real cross-site Origin still has to match Host.
    """
    host = request.headers.get("host", "").lower()
    origin = request.headers.get("origin")
    if origin:
        if origin.strip().lower() == "null":
            return request.headers.get("sec-fetch-site", "").strip().lower() == "same-origin"
        return _origin_host(origin) == host
    referer = request.headers.get("referer")
    if referer:
        return _origin_host(referer) == host
    return True


def _origin_host(value: str) -> str:
    from urllib.parse import urlparse

    return (urlparse(value).netloc or "").lower()


def issue_csrf() -> str:
    exp = int(time.time()) + int(CFG.csrf_ttl_s)
    nonce = secrets.token_urlsafe(18)
    body = f"{exp}.{nonce}"
    mac = hmac.new(CFG.signing_key(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{mac}"


def csrf_signature_ok(token: str) -> bool:
    parts = token.split(".")
    if len(parts) != 3:
        return False
    exp_s, nonce, mac = parts
    if not exp_s.isdigit() or not nonce or len(token) > 200:
        return False
    body = f"{exp_s}.{nonce}"
    expected = hmac.new(CFG.signing_key(), body.encode(), hashlib.sha256).hexdigest()[:32]
    if not tokens_match(mac, expected):
        return False
    exp = int(exp_s)
    now = time.time()
    if exp < now or exp > now + int(CFG.csrf_ttl_s) + 5:
        return False
    return True


def _purge_attempts(db: DB) -> float:
    cutoff = time.time() - int(CFG.login_window_s)
    db.exec("DELETE FROM login_attempts WHERE created_at<?", cutoff)
    return cutoff


def _bucket_wait(db: DB, kind: str, key: str, limit: int, cutoff: float) -> int:
    count = db.one(
        "SELECT COUNT(*) AS n FROM login_attempts WHERE kind=? AND ip=? AND created_at>=?",
        kind,
        key,
        cutoff,
    )
    n = int(count["n"]) if count else 0
    if n < limit:
        return 0
    oldest = db.one(
        "SELECT MIN(created_at) AS t FROM login_attempts WHERE kind=? AND ip=? AND created_at>=?",
        kind,
        key,
        cutoff,
    )
    start = float(oldest["t"]) if oldest and oldest["t"] is not None else time.time()
    return max(1, int(start + int(CFG.login_window_s) - time.time()) + 1)


def login_blocked(db: DB, ip: str, prefix_key: str | None = None) -> int:
    """Seconds to wait when the address or the token prefix is over its limit, else 0."""
    cutoff = _purge_attempts(db)
    wait = _bucket_wait(db, "ip", ip, int(CFG.login_limit), cutoff)
    if prefix_key:
        wait = max(wait, _bucket_wait(db, "prefix", prefix_key, int(CFG.login_prefix_limit), cutoff))
    return wait


def _record_failure(db: DB, kind: str, key: str) -> None:
    db.exec("INSERT INTO login_attempts (ip, kind, created_at) VALUES (?,?,?)", key, kind, time.time())


def _clear_failures(db: DB, kind: str, key: str) -> None:
    db.exec("DELETE FROM login_attempts WHERE kind=? AND ip=?", kind, key)


def principal_from_row(row: dict[str, Any]) -> dict[str, Any] | None:
    try:
        perms = set(json.loads(row["perms"]))
    except (TypeError, json.JSONDecodeError):
        return None
    if not perms:
        return None
    return {
        "id": row["id"],
        "name": row["name"],
        "workspace": row["workspace"],
        "perms": perms,
        "trial": row["kind"] == "trial" if "kind" in row.keys() else False,
    }


def find_live_token(db: DB, raw: str) -> dict[str, Any] | None:
    if not raw or len(raw) > _TOKEN_MAX:
        return None
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return db.one("SELECT * FROM tokens WHERE token_hash=? AND revoked_at IS NULL", digest)


def start_session(db: DB, token_id: str) -> str:
    """Store only the hash of the cookie and the token id. Return the raw cookie value."""
    raw = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    now = time.time()
    db.exec(
        "INSERT INTO web_sessions (id_hash, token_id, csrf, created_at, expires_at) VALUES (?,?,?,?,?)",
        hashlib.sha256(raw.encode()).hexdigest(),
        token_id,
        csrf,
        now,
        now + int(CFG.session_ttl_s),
    )
    return raw


def end_session(db: DB, raw: str) -> None:
    if not raw:
        return
    db.exec("DELETE FROM web_sessions WHERE id_hash=?", hashlib.sha256(raw.encode()).hexdigest())


def _drop_token_sessions(db: DB, token_id: str) -> None:
    db.exec("DELETE FROM web_sessions WHERE token_id=?", token_id)


def open_session(db: DB, raw: str | None) -> dict[str, Any] | None:
    """Return ``{principal, csrf}`` or None. Revoked tokens lose access on this call."""
    if not raw or len(raw) > _SESSION_MAX:
        return None
    id_hash = hashlib.sha256(raw.encode()).hexdigest()
    row = db.one("SELECT * FROM web_sessions WHERE id_hash=?", id_hash)
    if row is None:
        return None
    if float(row["expires_at"]) <= time.time():
        db.exec("DELETE FROM web_sessions WHERE id_hash=?", id_hash)
        return None
    token = db.one("SELECT * FROM tokens WHERE id=?", row["token_id"])
    if token is None or token["revoked_at"] is not None:
        _drop_token_sessions(db, str(row["token_id"]))
        return None
    principal = principal_from_row(token)
    if principal is None or "read" not in principal["perms"]:
        _drop_token_sessions(db, str(row["token_id"]))
        return None
    return {"principal": principal, "csrf": str(row["csrf"])}


def _note_failure(db: DB, ip: str, prefix_key: str | None) -> None:
    _record_failure(db, "ip", ip)
    if prefix_key:
        _record_failure(db, "prefix", prefix_key)


def attempt_login(db: DB, ip: str, form_csrf: str, cookie_csrf: str, token: str, *, origin_ok: bool) -> LoginResult:
    if not origin_ok or not tokens_match(form_csrf, cookie_csrf) or not csrf_signature_ok(form_csrf):
        return LoginResult(403, "The form expired. Load the page again.")
    prefix_key = token_prefix_key(token)
    wait = login_blocked(db, ip, prefix_key)
    if wait:
        return LoginResult(429, "Too many sign-in attempts. Try again later.", retry_after=wait)
    row = find_live_token(db, token.strip())
    principal = principal_from_row(row) if row else None
    if row is None or principal is None:
        _note_failure(db, ip, prefix_key)
        return LoginResult(401, "That token is not valid.")
    if "read" not in principal["perms"]:
        _note_failure(db, ip, prefix_key)
        return LoginResult(401, "That token cannot open My pages.")
    _clear_failures(db, "ip", ip)
    if prefix_key:
        _clear_failures(db, "prefix", prefix_key)
    return LoginResult(303, session_raw=start_session(db, str(row["id"])))


def cookie_kwargs() -> dict[str, Any]:
    return {"path": COOKIE_PATH, "httponly": True, "secure": bool(CFG.cookie_secure), "samesite": "strict"}


def set_session_cookie(response: Response, raw: str) -> None:
    response.set_cookie(SESSION_COOKIE, raw, max_age=int(CFG.session_ttl_s), **cookie_kwargs())


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, **cookie_kwargs())


def set_csrf_cookie(response: Response, token: str) -> None:
    response.set_cookie(CSRF_COOKIE, token, max_age=int(CFG.csrf_ttl_s), **cookie_kwargs())


def clear_csrf_cookie(response: Response) -> None:
    response.delete_cookie(CSRF_COOKIE, **cookie_kwargs())


def set_tz_cookie(response: Response, zone_name: str) -> None:
    response.set_cookie(TZ_COOKIE, zone_name, max_age=_TZ_MAX_AGE, **cookie_kwargs())
