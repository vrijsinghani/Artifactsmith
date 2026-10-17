"""OAuth clients, pending sign-ins, and magic links. Only hashes are stored."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from datetime import UTC, datetime
from typing import Any

from .config import CFG
from .db import DB
from .oauth_origin import redirect_patterns

CODE_TTL = 600
ACCESS_TTL = 3600
REFRESH_TTL = 30 * 86400
PENDING_TTL = 1800
MAX_REDIRECTS = 8
SCOPE = "artifactsmith"
PENDING_PER_IP = 20
AUTHORIZE_LIMIT = 60
REGISTER_LIMIT = 10
REGISTER_WINDOW_S = 3600
REGISTER_DAY_CAP = 500
GLOBAL_FAILURES = 20
ATTEMPT_KEEP_S = 86400


class GrantError(Exception):
    """A token or confirm step failed. ``error`` is an OAuth error or a page key."""

    def __init__(self, error: str) -> None:
        self.error = error
        super().__init__(error)


def digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def pkce_ok(verifier: str, challenge: str) -> bool:
    if not verifier or len(verifier) < 43 or len(verifier) > 128 or any(c in verifier for c in "\r\n "):
        return False
    encoded = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    if len(encoded) != len(challenge):
        return False
    return secrets.compare_digest(encoded, challenge)


def ensure_static_client(db: DB) -> None:
    redirects = json.dumps(redirect_patterns())
    client_id = CFG.oauth_static_client_id
    if db.one("SELECT client_id FROM oauth_clients WHERE client_id=?", client_id) is None:
        db.exec(
            "INSERT INTO oauth_clients (client_id, redirect_json, auth_method, created_at) VALUES (?,?,?,?)",
            client_id,
            redirects,
            "none",
            time.time(),
        )
        return
    db.exec(
        "UPDATE oauth_clients SET redirect_json=?, auth_method='none' WHERE client_id=?",
        redirects,
        client_id,
    )


def client_row(db: DB, client_id: str) -> dict[str, Any] | None:
    if not client_id or len(client_id) > 200:
        return None
    return db.one("SELECT * FROM oauth_clients WHERE client_id=?", client_id)


def client_redirects(row: dict[str, Any]) -> list[str]:
    data = json.loads(str(row["redirect_json"]))
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]


def register_client(db: DB, redirects: list[str]) -> str:
    client_id = "oc_" + secrets.token_urlsafe(18)
    db.exec(
        "INSERT INTO oauth_clients (client_id, redirect_json, auth_method, created_at) VALUES (?,?,?,?)",
        client_id,
        json.dumps(redirects),
        "none",
        time.time(),
    )
    return client_id


def save_pending(
    db: DB,
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    challenge: str,
    scope: str,
    resource: str,
    client_ip: str,
) -> str:
    raw = secrets.token_urlsafe(24)
    now = time.time()
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO oauth_pending (id_hash, client_id, redirect_uri, state, challenge, scope, resource, "
            "email_hash, token_id, created_at, expires_at, client_ip) VALUES (?,?,?,?,?,?,?,NULL,NULL,?,?,?)",
            (
                digest(raw),
                client_id,
                redirect_uri,
                state,
                challenge,
                scope,
                resource,
                now,
                now + PENDING_TTL,
                client_ip[:200],
            ),
        )
        rows = conn.execute(
            "SELECT id_hash FROM oauth_pending WHERE client_ip=? ORDER BY created_at DESC, id_hash DESC",
            (client_ip[:200],),
        ).fetchall()
        for old in rows[PENDING_PER_IP:]:
            conn.execute("DELETE FROM oauth_pending WHERE id_hash=?", (old["id_hash"],))
    return raw


def pending_row(db: DB, raw: str) -> dict[str, Any] | None:
    if not raw or len(raw) > 200:
        return None
    row = db.one("SELECT * FROM oauth_pending WHERE id_hash=?", digest(raw))
    if row is None or float(row["expires_at"]) < time.time():
        return None
    return row


def remember_email(db: DB, pending_hash: str, email_hash: str, token_id: str | None) -> None:
    db.exec(
        "UPDATE oauth_pending SET email_hash=?, token_id=? WHERE id_hash=?",
        email_hash,
        token_id,
        pending_hash,
    )


def issue_link(db: DB, pending_hash: str, email_hash: str, token_id: str | None) -> str:
    """Bind the link to the email and token resolved for this send."""
    raw = secrets.token_urlsafe(32)
    now = time.time()
    db.exec(
        "INSERT INTO oauth_links (code_hash, pending_hash, expires_at, used_at, email_hash, token_id) "
        "VALUES (?,?,?,NULL,?,?)",
        digest(raw),
        pending_hash,
        now + int(CFG.trial_link_ttl_s),
        email_hash,
        token_id,
    )
    return raw


def set_link_token(db: DB, code_hash: str, token_id: str) -> None:
    """Fill a link that was mailed before its token existed. Never replace one already set."""
    db.exec(
        "UPDATE oauth_links SET token_id=? WHERE code_hash=? AND token_id IS NULL",
        token_id,
        code_hash,
    )


def link_pending(db: DB, raw: str) -> bool:
    if not raw or len(raw) > 200:
        return False
    row = db.one("SELECT expires_at, used_at FROM oauth_links WHERE code_hash=?", digest(raw))
    return row is not None and row["used_at"] is None and float(row["expires_at"]) >= time.time()


def retry_after(
    db: DB,
    kind: str,
    key: str,
    *,
    limit: int | None = None,
    window_s: int | None = None,
) -> int:
    window = int(CFG.login_window_s if window_s is None else window_s)
    cap = int(CFG.login_limit if limit is None else limit)
    db.exec("DELETE FROM login_attempts WHERE created_at<?", time.time() - ATTEMPT_KEEP_S)
    cutoff = time.time() - window
    count = db.one(
        "SELECT COUNT(*) AS n FROM login_attempts WHERE kind=? AND ip=? AND created_at>=?",
        kind,
        key,
        cutoff,
    )
    n = int(count["n"]) if count else 0
    if n < cap:
        return 0
    oldest = db.one(
        "SELECT MIN(created_at) AS t FROM login_attempts WHERE kind=? AND ip=? AND created_at>=?",
        kind,
        key,
        cutoff,
    )
    start = float(oldest["t"]) if oldest and oldest["t"] is not None else time.time()
    return max(1, int(start + window - time.time()) + 1)


def pair_wait(db: DB, kind: str, ip: str) -> int:
    """Seconds to wait when this IP or the global ceiling is already full."""
    return max(retry_after(db, kind, ip), retry_after(db, kind, "global", limit=GLOBAL_FAILURES))


def pair_note(db: DB, kind: str, ip: str) -> None:
    note_failure(db, kind, ip)
    note_failure(db, kind, "global")


def clients_today(db: DB) -> int:
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    row = db.one(
        "SELECT COUNT(*) AS n FROM oauth_clients WHERE created_at>=? AND client_id!=?",
        start,
        CFG.oauth_static_client_id,
    )
    return int(row["n"]) if row else 0


def purge_expired(db: DB, now: float) -> None:
    """Drop OAuth rows a day after they expire, and idle dynamic clients after 30 days."""
    cutoff = now - 86400
    db.exec("DELETE FROM oauth_pending WHERE expires_at<?", cutoff)
    db.exec("DELETE FROM oauth_links WHERE expires_at<?", cutoff)
    db.exec("DELETE FROM oauth_grants WHERE expires_at<?", cutoff)
    db.exec("DELETE FROM oauth_tokens WHERE refresh_expires_at<?", cutoff)
    idle = now - 30 * 86400
    rows = db.all(
        "SELECT client_id FROM oauth_clients WHERE created_at<? AND client_id!=?",
        idle,
        CFG.oauth_static_client_id,
    )
    for row in rows:
        client_id = str(row["client_id"])
        if db.one("SELECT 1 AS n FROM oauth_grants WHERE client_id=?", client_id):
            continue
        if db.one("SELECT 1 AS n FROM oauth_tokens WHERE client_id=?", client_id):
            continue
        db.exec("DELETE FROM oauth_clients WHERE client_id=?", client_id)


def note_failure(db: DB, kind: str, key: str) -> None:
    db.exec("INSERT INTO login_attempts (ip, kind, created_at) VALUES (?,?,?)", key, kind, time.time())


def token_for_email(db: DB, email_hash: str) -> dict[str, Any] | None:
    signup = db.one("SELECT token_id FROM trial_signups WHERE email_hash=?", email_hash)
    if signup:
        return db.one("SELECT * FROM tokens WHERE id=?", signup["token_id"])
    return db.one(
        "SELECT * FROM tokens WHERE email_hash=? AND kind!='demo' ORDER BY created_at LIMIT 1",
        email_hash,
    )
