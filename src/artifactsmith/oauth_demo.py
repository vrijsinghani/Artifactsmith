"""One fixed review account. Off unless both demo settings are set."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any

from .config import CFG, ConfigError
from .db import DB
from .store import Store
from .trial import TRIAL_PERMS, email_hash, normalize_email

DEMO_TOKEN_ID = "tok_oauth_demo"
DEMO_WORKSPACE = "oauth-demo"
DEMO_ARTIFACT = "art_oauth_demo"
_SAMPLE = (
    b"<!doctype html><html><head><meta charset=utf-8><title>Sample page</title></head>"
    b"<body><h1>Sample page</h1><p>Directory review sample.</p></body></html>"
)


def configured() -> bool:
    return bool(CFG.oauth_demo_email.strip() and CFG.oauth_demo_password)


def email_is_demo(normalized: str) -> bool:
    if not configured():
        return False
    try:
        return normalize_email(CFG.oauth_demo_email) == normalized
    except ValueError:
        return False


def password_matches(got: str) -> bool:
    expected = CFG.oauth_demo_password
    if not configured() or not got:
        return False
    left = hashlib.sha256(got.encode()).digest()
    right = hashlib.sha256(expected.encode()).digest()
    return secrets.compare_digest(left, right)


def ensure(db: DB, store: Store) -> None:
    """Create the fixed token and sample page when the demo account is configured."""
    if not CFG.oauth_demo_email.strip() and not CFG.oauth_demo_password:
        return
    if not configured():
        raise ConfigError("AM_OAUTH_DEMO_EMAIL and AM_OAUTH_DEMO_PASSWORD must both be set or both be empty")
    try:
        normalized = normalize_email(CFG.oauth_demo_email)
    except ValueError as exc:
        raise ConfigError("AM_OAUTH_DEMO_EMAIL is invalid") from exc
    digest = email_hash(normalized)
    now = time.time()
    with db.tx() as conn:
        row = conn.execute("SELECT id FROM tokens WHERE id=?", (DEMO_TOKEN_ID,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO tokens (id, token_hash, name, workspace, perms, created_at, revoked_at, kind, email_hash) "
                "VALUES (?,?,?,?,?,?,NULL,'demo',?)",
                (
                    DEMO_TOKEN_ID,
                    hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
                    "demo",
                    DEMO_WORKSPACE,
                    json.dumps(TRIAL_PERMS),
                    now,
                    digest,
                ),
            )
        else:
            conn.execute(
                "UPDATE tokens SET email_hash=?, kind='demo', workspace=? WHERE id=?",
                (digest, DEMO_WORKSPACE, DEMO_TOKEN_ID),
            )
        conn.execute("DELETE FROM trial_signups WHERE token_id=?", (DEMO_TOKEN_ID,))
        if conn.execute("SELECT id FROM artifacts WHERE id=?", (DEMO_ARTIFACT,)).fetchone() is None:
            _insert_sample(conn, now)
            key = Store.prefix(DEMO_WORKSPACE, DEMO_ARTIFACT, 1) + "index.html"
            store.put(key, _SAMPLE, "text/html")


def _insert_sample(conn: Any, now: float) -> None:
    conn.execute(
        "INSERT INTO artifacts (id, workspace, slug, display_name, kind, format, created_by, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (DEMO_ARTIFACT, DEMO_WORKSPACE, "sample-page", "Sample page", "web_static", "html", "demo", now, now),
    )
    conn.execute(
        "INSERT INTO jobs (id, artifact_id, version, client, status, created_at, finished_at) VALUES (?,?,?,?,?,?,?)",
        ("job_oauth_demo", DEMO_ARTIFACT, 1, DEMO_TOKEN_ID, "done", now, now),
    )
    conn.execute(
        "INSERT INTO versions (artifact_id, version, base_version, verbatim_request, status, job_id, model, "
        "sha256, primary_file, files_json, assumptions_json, summary, size, created_by, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            DEMO_ARTIFACT,
            1,
            None,
            "Sample page for directory review.",
            "done",
            "job_oauth_demo",
            CFG.trial_model,
            hashlib.sha256(_SAMPLE).hexdigest(),
            "index.html",
            json.dumps([{"name": "index.html", "content_type": "text/html"}]),
            "[]",
            "Directory review sample.",
            len(_SAMPLE),
            "demo",
            now,
        ),
    )
