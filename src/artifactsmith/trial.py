"""Hosted trial sign-up, spend holds, and retention.

Tokens stay ordinary workspace tokens. This module only adds the trial tag,
the magic link, and the daily cost hold. Mail uses the stdlib SMTP client.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import smtplib
import time
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any
from urllib.parse import quote

from disposable_email_domains import blocklist

from .config import CFG

log = logging.getLogger("artifactsmith.trial")

TRIAL_PERMS = ["create", "read", "edit", "export", "share", "delete"]
MAX_MODEL_CALLS = 3
MAX_INPUT_TOKENS = 200_000
SHARE_DAYS = 30
SELF_HOST = "https://github.com/vrijsinghani/artifactsmith#readme"
CLOSED = "trial is closed"
CAP_ERROR = "trial capacity reached for today, resets at 00:00 UTC"
QUOTA_ERROR = f"trial builds are used up. Self-host ArtifactSmith: {SELF_HOST}"
MAIL_UNAVAILABLE = "sign-up email is not available"
_GMAIL = frozenset({"gmail.com", "googlemail.com"})
_ABUSE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}$")


class TrialMailError(RuntimeError):
    """Raised when a magic link cannot be sent. The message is safe to show."""


class TrialLimit(Exception):
    """A trial quota or spend hold failed. ``message`` is safe to show."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def normalize_email(raw: str) -> str:
    """Lower-case an address. Gmail drops dots and +tags and folds googlemail.com."""
    text = (raw or "").strip().lower()
    if not text or any(ch in text for ch in "\r\n\t ") or text.count("@") != 1 or len(text) > 254:
        raise ValueError("invalid email")
    local, domain = text.split("@")
    if not local or "." not in domain or ".." in domain or domain.startswith(".") or domain.endswith("."):
        raise ValueError("invalid email")
    if domain in _GMAIL:
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"
    if not local or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+", local):
        raise ValueError("invalid email")
    if not re.fullmatch(r"[a-z0-9.-]+", domain):
        raise ValueError("invalid email")
    return f"{local}@{domain}"


def email_hash(normalized: str) -> str:
    return hashlib.sha256(normalized.encode()).hexdigest()


def is_disposable(normalized: str) -> bool:
    domain = normalized.rsplit("@", 1)[-1]
    return domain in blocklist


def abuse_email() -> str:
    raw = (CFG.trial_abuse_email or "").strip()
    return raw if _ABUSE.fullmatch(raw) else ""


def utc_day(when: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if when is None else when, UTC).strftime("%Y-%m-%d")


def _prices() -> tuple[Decimal, Decimal]:
    return Decimal(CFG.trial_price_in), Decimal(CFG.trial_price_out)


def micros_for(input_tokens: int, output_tokens: int) -> int:
    """Micro-dollars for one model call. 1 USD is 1_000_000 micros."""
    price_in, price_out = _prices()
    raw = Decimal(max(0, input_tokens)) * price_in + Decimal(max(0, output_tokens)) * price_out
    return int(raw.to_integral_value(rounding=ROUND_CEILING))


def worst_case_micros() -> int:
    """Hold for every model call a build can make, at the input and output caps."""
    one = micros_for(MAX_INPUT_TOKENS, int(CFG.trial_max_output_tokens))
    return one * MAX_MODEL_CALLS


def cap_micros() -> int:
    raw = Decimal(CFG.trial_daily_usd) * Decimal(1_000_000)
    return int(raw.to_integral_value(rounding=ROUND_FLOOR))


def day_spend_micros(conn: Any, day: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN actual_micros IS NULL THEN reserved_micros ELSE actual_micros END), 0) AS n "
        "FROM trial_reservations WHERE day=?",
        (day,),
    ).fetchone()
    return int(row["n"] if row else 0)


def reserve(conn: Any, job_id: str, when: float, token_id: str) -> None:
    """Hold worst-case cost for this UTC day. Caller must already be inside a write transaction.

    ``token_id`` is what the lifetime and hourly limits count. Deleting an artifact
    removes the job row and must not remove this one.
    """
    day = utc_day(when)
    worst = worst_case_micros()
    if day_spend_micros(conn, day) + worst > cap_micros():
        raise TrialLimit(CAP_ERROR)
    conn.execute(
        "INSERT INTO trial_reservations "
        "(job_id, token_id, day, reserved_micros, actual_micros, settled_at, created_at) "
        "VALUES (?,?,?,?,NULL,NULL,?)",
        (job_id, token_id, day, worst, when),
    )


def settle(db: Any, job_id: str, usage: list[tuple[int, int, bool]]) -> None:
    """Replace the hold with measured cost. Unknown usage keeps the full hold.

    Joins a transaction this thread already opened, so a failed claim and its
    release commit together.
    """
    with db.tx() as conn:
        row = conn.execute(
            "SELECT reserved_micros, settled_at FROM trial_reservations WHERE job_id=?",
            (job_id,),
        ).fetchone()
        if row is None or row["settled_at"] is not None:
            return
        reserved = int(row["reserved_micros"])
        known = bool(usage) and all(item[2] for item in usage)
        if usage and not known:
            actual = reserved
        elif not usage:
            actual = 0
        else:
            actual = sum(micros_for(item[0], item[1]) for item in usage)
        if actual > reserved:
            log.warning("trial job %s cost exceeded its hold", job_id)
            actual = reserved
        stamp = time.time()
        for item in usage:
            if not item[2]:
                continue
            conn.execute(
                "INSERT INTO trial_usage (job_id, model, input_tokens, output_tokens, created_at) VALUES (?,?,?,?,?)",
                (job_id, CFG.trial_model, item[0], item[1], stamp),
            )
        conn.execute(
            "UPDATE trial_reservations SET actual_micros=?, settled_at=? WHERE job_id=? AND settled_at IS NULL",
            (actual, stamp, job_id),
        )


def signup_blocked(db: Any, ip: str) -> bool:
    cutoff = time.time() - int(CFG.trial_signup_window_s)
    db.exec("DELETE FROM trial_signup_ips WHERE created_at<?", cutoff)
    row = db.one(
        "SELECT COUNT(*) AS n FROM trial_signup_ips WHERE ip=? AND created_at>=?",
        ip,
        cutoff,
    )
    return int(row["n"] if row else 0) >= int(CFG.trial_signups_per_ip)


def note_signup(db: Any, ip: str) -> None:
    db.exec("INSERT INTO trial_signup_ips (ip, created_at) VALUES (?,?)", ip, time.time())


def issue_link(db: Any, digest: str) -> str:
    raw = secrets.token_urlsafe(32)
    now = time.time()
    db.exec(
        "INSERT INTO trial_links (code_hash, email_hash, created_at, expires_at, used_at) VALUES (?,?,?,?,NULL)",
        hashlib.sha256(raw.encode()).hexdigest(),
        digest,
        now,
        now + int(CFG.trial_link_ttl_s),
    )
    return raw


def link_url(raw: str) -> str:
    base = CFG.api_url.rstrip("/")
    return f"{base}/trial/verify?code={quote(raw)}"


def send_magic_link(to: str, url: str) -> None:
    host = CFG.smtp_host.strip()
    sender = CFG.smtp_from.strip()
    if not host or not sender:
        log.error("trial email is not configured; set AM_SMTP_HOST and AM_SMTP_FROM")
        raise TrialMailError(MAIL_UNAVAILABLE)
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = "Your ArtifactSmith trial link"
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain="artifactsmith")
    message.set_content(
        "Open this link to finish signing up. It works once.\n\n"
        + url
        + "\n\nIf you did not ask for this, ignore it.\n"
    )
    try:
        with smtplib.SMTP(host, int(CFG.smtp_port), timeout=20) as smtp:
            if CFG.smtp_tls:
                smtp.starttls()
            user = CFG.smtp_user.strip()
            if user:
                smtp.login(user, CFG.smtp_password())
            smtp.send_message(message)
    except Exception as exc:
        log.error("trial email failed: %s", type(exc).__name__)
        raise TrialMailError(MAIL_UNAVAILABLE) from exc


def link_pending(db: Any, raw: str) -> bool:
    """True when this code can still be used. Does not mark it used."""
    row = _link_row(db, raw)
    return row is not None and row["used_at"] is None and float(row["expires_at"]) >= time.time()


def _link_row(db: Any, raw: str) -> Any:
    if not raw or len(raw) > 200:
        return None
    return db.one(
        "SELECT email_hash, expires_at, used_at FROM trial_links WHERE code_hash=?",
        hashlib.sha256(raw.encode()).hexdigest(),
    )


def consume_link(db: Any, raw: str) -> str | None:
    """Mark a link used and return its email hash. None when it is missing, used, or expired."""
    if not raw or len(raw) > 200:
        return None
    digest = hashlib.sha256(raw.encode()).hexdigest()
    now = time.time()
    with db.tx() as conn:
        row = conn.execute(
            "SELECT email_hash, expires_at, used_at FROM trial_links WHERE code_hash=?",
            (digest,),
        ).fetchone()
        if row is None or row["used_at"] is not None or float(row["expires_at"]) < now:
            return None
        conn.execute("UPDATE trial_links SET used_at=? WHERE code_hash=?", (now, digest))
        return str(row["email_hash"])


def mint_trial(db: Any, digest: str) -> tuple[str, str] | None:
    """Create the workspace token once. None when this email already has a trial."""
    with db.tx() as conn:
        if conn.execute("SELECT 1 FROM trial_signups WHERE email_hash=?", (digest,)).fetchone():
            return None
        workspace = "t" + secrets.token_hex(4)
        raw = "asmb_" + secrets.token_urlsafe(32)
        token_id = "tok_" + secrets.token_urlsafe(8)
        now = time.time()
        conn.execute(
            "INSERT INTO tokens (id, token_hash, name, workspace, perms, created_at, revoked_at, kind, email_hash) "
            "VALUES (?,?,?,?,?,?,NULL,'trial',?)",
            (
                token_id,
                hashlib.sha256(raw.encode()).hexdigest(),
                "trial",
                workspace,
                json.dumps(TRIAL_PERMS),
                now,
                digest,
            ),
        )
        conn.execute(
            "INSERT INTO trial_signups (email_hash, token_id, created_at) VALUES (?,?,?)",
            (digest, token_id, now),
        )
        return raw, workspace


def workspace_is_trial(db: Any, workspace: str) -> bool:
    row = db.one(
        "SELECT 1 AS n FROM tokens WHERE workspace=? AND kind='trial' LIMIT 1",
        workspace,
    )
    return row is not None


# Inline !important beats a page rule that hides footer, *, or this id.
_FOOTER_STYLE = (
    "all:revert!important;display:block!important;visibility:visible!important;opacity:1!important;"
    "position:static!important;float:none!important;clear:both!important;inset:auto!important;"
    "transform:none!important;filter:none!important;clip:auto!important;clip-path:none!important;"
    "height:auto!important;max-height:none!important;overflow:visible!important;"
    "margin:2rem 0 0!important;padding:.8rem 0!important;"
    "font:14px/1.4 system-ui,sans-serif!important;color:#3f3f46!important;background:#fff!important;"
    "text-indent:0!important;z-index:2147483647!important;"
)
_LINK_STYLE = (
    "color:#0b5cab!important;font-size:14px!important;visibility:visible!important;"
    "display:inline!important;opacity:1!important;"
)


def stamp_trial_chrome(html: bytes, mailto: str) -> bytes:
    """Add noindex and an abuse footer at serve time. Stored bytes stay unchanged.

    The footer uses an inline ``!important`` style so a page rule such as
    ``footer { display: none !important }`` cannot hide the report link.
    """
    from html import escape

    text = html.decode("utf-8", errors="replace")
    if not re.search(r"(?i)name\s*=\s*[\"']?robots", text):
        meta = '<meta name="robots" content="noindex">'
        if re.search(r"(?i)<head[^>]*>", text):
            text = re.sub(r"(?i)<head[^>]*>", lambda match: match.group(0) + meta, text, count=1)
        else:
            text = meta + text
    link = ""
    if mailto:
        link = f' <a href="mailto:{escape(mailto, quote=True)}" style="{_LINK_STYLE}">Report abuse</a>'
    footer = f'<footer id="am-trial-report" style="{_FOOTER_STYLE}">Trial page.{link}</footer>'
    if re.search(r"(?i)</body>", text):
        text = re.sub(r"(?i)</body>", footer + "</body>", text, count=1)
    else:
        text += footer
    return text.encode("utf-8")


def purge_idle_trials(db: Any, store: Any, *, now: float | None = None) -> int:
    """Delete trial workspaces whose latest build is older than the retention window."""
    from .store import Store

    stamp = time.time() if now is None else now
    cutoff = stamp - int(CFG.trial_retention_days) * 86400
    rows = db.all("SELECT id, workspace, created_at FROM tokens WHERE kind='trial' AND revoked_at IS NULL")
    removed = 0
    for token in rows:
        latest = db.one("SELECT MAX(created_at) AS t FROM jobs WHERE client=?", token["id"])
        held = db.one("SELECT MAX(created_at) AS t FROM trial_reservations WHERE token_id=?", token["id"])
        stamps = [float(token["created_at"])]
        if latest and latest["t"] is not None:
            stamps.append(float(latest["t"]))
        if held and held["t"] is not None:
            stamps.append(float(held["t"]))
        last = max(stamps)
        if last >= cutoff:
            continue
        busy = db.one(
            "SELECT 1 AS n FROM jobs JOIN artifacts ON artifacts.id=jobs.artifact_id "
            "WHERE artifacts.workspace=? AND jobs.status='building'",
            token["workspace"],
        )
        if busy:
            continue
        for art in db.all("SELECT id FROM artifacts WHERE workspace=?", token["workspace"]):
            store.purge_prefix(Store.prefix(token["workspace"], art["id"]))
            for table in ("versions", "jobs", "shares", "delete_tokens", "short_links"):
                db.exec(f"DELETE FROM {table} WHERE artifact_id=?", art["id"])
            db.exec("DELETE FROM artifacts WHERE id=?", art["id"])
        db.exec("UPDATE tokens SET revoked_at=? WHERE id=?", stamp, token["id"])
        db.exec("DELETE FROM web_sessions WHERE token_id=?", token["id"])
        removed += 1
    return removed
