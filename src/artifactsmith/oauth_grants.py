"""Authorization codes, refresh rotation, and bearer lookup."""

from __future__ import annotations

import secrets
import time
from typing import Any

from .db import DB
from .oauth_store import (
    ACCESS_TTL,
    CODE_TTL,
    REFRESH_TTL,
    GrantError,
    digest,
    pkce_ok,
)
from .web.session import principal_from_row


def _insert_pair(conn: Any, *, family: str, client_id: str, token_id: str, scope: str) -> dict[str, Any]:
    access = "asmo_" + secrets.token_urlsafe(32)
    refresh = "asmr_" + secrets.token_urlsafe(32)
    now = time.time()
    conn.execute(
        "INSERT INTO oauth_tokens (id, family_id, access_hash, refresh_hash, client_id, token_id, scope, "
        "access_expires_at, refresh_expires_at, rotated_at, revoked_at) VALUES (?,?,?,?,?,?,?,?,?,NULL,NULL)",
        (
            "oat_" + secrets.token_urlsafe(8),
            family,
            digest(access),
            digest(refresh),
            client_id,
            token_id,
            scope,
            now + ACCESS_TTL,
            now + REFRESH_TTL,
        ),
    )
    return {"access_token": access, "refresh_token": refresh, "expires_in": ACCESS_TTL, "scope": scope}


def _live_token(conn: Any, token_id: str) -> bool:
    row = conn.execute("SELECT revoked_at FROM tokens WHERE id=?", (token_id,)).fetchone()
    return row is not None and row["revoked_at"] is None


def issue_code(db: DB, pending_hash: str, token_id: str) -> tuple[str, str, str]:
    """Return the raw code, redirect URI, and state. Deletes the pending row."""
    if not token_id:
        raise GrantError("expired")
    with db.tx() as conn:
        pending = conn.execute("SELECT * FROM oauth_pending WHERE id_hash=?", (pending_hash,)).fetchone()
        if pending is None or float(pending["expires_at"]) < time.time():
            raise GrantError("expired")
        if not _live_token(conn, token_id):
            raise GrantError("closed")
        code = secrets.token_urlsafe(32)
        now = time.time()
        conn.execute(
            "INSERT INTO oauth_grants (code_hash, client_id, redirect_uri, challenge, token_id, scope, resource, "
            "expires_at, used_at, family_id) VALUES (?,?,?,?,?,?,?,?,NULL,NULL)",
            (
                digest(code),
                pending["client_id"],
                pending["redirect_uri"],
                pending["challenge"],
                token_id,
                pending["scope"],
                pending["resource"],
                now + CODE_TTL,
            ),
        )
        conn.execute("DELETE FROM oauth_pending WHERE id_hash=?", (pending_hash,))
        return code, str(pending["redirect_uri"]), str(pending["state"])


def _revoke_family(conn: Any, family: str) -> None:
    conn.execute(
        "UPDATE oauth_tokens SET revoked_at=? WHERE family_id=? AND revoked_at IS NULL",
        (time.time(), family),
    )


def peek_confirm(db: DB, link_raw: str, cookie_raw: str) -> dict[str, Any]:
    """Return the pending row when the cookie matches. Does not spend the link."""
    if not link_raw or len(link_raw) > 200:
        raise GrantError("expired")
    link = db.one("SELECT * FROM oauth_links WHERE code_hash=?", digest(link_raw))
    if link is None or link["used_at"] is not None or float(link["expires_at"]) < time.time():
        raise GrantError("expired")
    if not cookie_raw or len(cookie_raw) > 200 or digest(cookie_raw) != str(link["pending_hash"]):
        raise GrantError("browser")
    pending = db.one("SELECT * FROM oauth_pending WHERE id_hash=?", link["pending_hash"])
    if pending is None or float(pending["expires_at"]) < time.time():
        raise GrantError("expired")
    found = dict(pending)
    found["email_hash"] = link["email_hash"]
    found["token_id"] = link["token_id"]
    found["link_hash"] = digest(link_raw)
    return found


def confirm_link(db: DB, link_raw: str, cookie_raw: str) -> tuple[str, str, str]:
    """Spend the link only when the browser cookie matches. Return code, redirect, state."""
    if not link_raw or len(link_raw) > 200:
        raise GrantError("expired")
    if not cookie_raw or len(cookie_raw) > 200:
        raise GrantError("browser")
    with db.tx() as conn:
        link = conn.execute("SELECT * FROM oauth_links WHERE code_hash=?", (digest(link_raw),)).fetchone()
        if link is None or link["used_at"] is not None or float(link["expires_at"]) < time.time():
            raise GrantError("expired")
        if digest(cookie_raw) != str(link["pending_hash"]):
            raise GrantError("browser")
        pending = conn.execute("SELECT * FROM oauth_pending WHERE id_hash=?", (link["pending_hash"],)).fetchone()
        if pending is None or float(pending["expires_at"]) < time.time():
            raise GrantError("expired")
        cur = conn.execute(
            "UPDATE oauth_links SET used_at=? WHERE code_hash=? AND used_at IS NULL",
            (time.time(), digest(link_raw)),
        )
        if cur.rowcount != 1:
            raise GrantError("expired")
        token_id = str(link["token_id"]) if link["token_id"] else ""
        if not token_id:
            raise GrantError("mint")
        if not _live_token(conn, token_id):
            raise GrantError("closed")
        code = secrets.token_urlsafe(32)
        now = time.time()
        conn.execute(
            "INSERT INTO oauth_grants (code_hash, client_id, redirect_uri, challenge, token_id, scope, resource, "
            "expires_at, used_at, family_id) VALUES (?,?,?,?,?,?,?,?,NULL,NULL)",
            (
                digest(code),
                pending["client_id"],
                pending["redirect_uri"],
                pending["challenge"],
                token_id,
                pending["scope"],
                pending["resource"],
                now + CODE_TTL,
            ),
        )
        conn.execute("DELETE FROM oauth_pending WHERE id_hash=?", (link["pending_hash"],))
        return code, str(pending["redirect_uri"]), str(pending["state"])


def _resource_ok(stored: str, presented: str) -> bool:
    from .oauth_origin import public_origin, resource_matches, resource_url

    if not presented:
        return True
    origin = public_origin()
    if origin is None or not resource_matches(presented, resource_url(origin)):
        return False
    return not stored or resource_matches(presented, stored)


def exchange_code(
    db: DB,
    *,
    code: str,
    verifier: str,
    client_id: str,
    redirect_uri: str,
    resource: str,
) -> dict[str, Any]:
    if not code or len(code) > 200:
        raise GrantError("invalid_grant")
    replay = False
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM oauth_grants WHERE code_hash=?", (digest(code),)).fetchone()
        if row is None or float(row["expires_at"]) < time.time():
            raise GrantError("invalid_grant")
        if str(row["client_id"]) != client_id or str(row["redirect_uri"]) != redirect_uri:
            raise GrantError("invalid_grant")
        if not pkce_ok(verifier, str(row["challenge"])):
            raise GrantError("invalid_grant")
        if not _resource_ok(str(row["resource"] or ""), resource):
            raise GrantError("invalid_target")
        if row["used_at"] is not None:
            if row["family_id"]:
                _revoke_family(conn, str(row["family_id"]))
            replay = True
        elif not _live_token(conn, str(row["token_id"])):
            raise GrantError("invalid_grant")
        else:
            family = "fam_" + secrets.token_urlsafe(8)
            issued = _insert_pair(
                conn, family=family, client_id=client_id, token_id=str(row["token_id"]), scope=str(row["scope"])
            )
            conn.execute(
                "UPDATE oauth_grants SET used_at=?, family_id=? WHERE code_hash=?",
                (time.time(), family, digest(code)),
            )
            return issued
    if replay:
        raise GrantError("invalid_grant")
    raise GrantError("invalid_grant")


def refresh_grant(db: DB, *, raw: str, client_id: str) -> dict[str, Any]:
    if not raw or len(raw) > 300:
        raise GrantError("invalid_grant")
    stolen = False
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM oauth_tokens WHERE refresh_hash=?", (digest(raw),)).fetchone()
        if row is None or str(row["client_id"]) != client_id:
            raise GrantError("invalid_grant")
        if row["revoked_at"] is not None or float(row["refresh_expires_at"]) < time.time():
            raise GrantError("invalid_grant")
        if row["rotated_at"] is not None:
            _revoke_family(conn, str(row["family_id"]))
            stolen = True
        elif not _live_token(conn, str(row["token_id"])):
            raise GrantError("invalid_grant")
        else:
            cur = conn.execute(
                "UPDATE oauth_tokens SET rotated_at=? WHERE id=? AND rotated_at IS NULL AND revoked_at IS NULL",
                (time.time(), row["id"]),
            )
            if cur.rowcount != 1:
                _revoke_family(conn, str(row["family_id"]))
                stolen = True
            else:
                return _insert_pair(
                    conn,
                    family=str(row["family_id"]),
                    client_id=client_id,
                    token_id=str(row["token_id"]),
                    scope=str(row["scope"]),
                )
    if stolen:
        raise GrantError("invalid_grant")
    raise GrantError("invalid_grant")


def revoke_token(db: DB, raw: str) -> None:
    if not raw or len(raw) > 300:
        return
    hashed = digest(raw)
    with db.tx() as conn:
        row = conn.execute(
            "SELECT family_id FROM oauth_tokens WHERE access_hash=? OR refresh_hash=?",
            (hashed, hashed),
        ).fetchone()
        if row is None:
            return
        conn.execute(
            "UPDATE oauth_tokens SET revoked_at=? WHERE family_id=? AND revoked_at IS NULL",
            (time.time(), row["family_id"]),
        )


def resolve_bearer(db: DB, raw: str) -> dict[str, Any] | None:
    if not raw or len(raw) > 512:
        return None
    hashed = digest(raw)
    hosted = db.one("SELECT * FROM tokens WHERE token_hash=? AND revoked_at IS NULL", hashed)
    if hosted:
        return principal_from_row(hosted)
    row = db.one(
        "SELECT * FROM oauth_tokens WHERE access_hash=? AND revoked_at IS NULL AND rotated_at IS NULL",
        hashed,
    )
    if row is None or float(row["access_expires_at"]) < time.time():
        return None
    hosted = db.one("SELECT * FROM tokens WHERE id=? AND revoked_at IS NULL", row["token_id"])
    if hosted is None:
        return None
    return principal_from_row(hosted)


def drop_for_token(db: DB, token_id: str) -> None:
    now = time.time()
    db.exec("UPDATE oauth_tokens SET revoked_at=? WHERE token_id=? AND revoked_at IS NULL", now, token_id)
    db.exec("DELETE FROM oauth_pending WHERE token_id=?", token_id)
    db.exec("DELETE FROM oauth_grants WHERE token_id=?", token_id)
