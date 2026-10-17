"""Email, review-password, and hosted-token steps for the OAuth sign-in page."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from .. import trial
from ..config import CFG
from ..oauth_demo import DEMO_TOKEN_ID, email_is_demo, password_matches
from ..oauth_grants import confirm_link, issue_code, peek_confirm
from ..oauth_http import issuer, redirect_to
from ..oauth_origin import public_origin
from ..oauth_store import (
    GrantError,
    digest,
    issue_link,
    link_pending,
    note_failure,
    pair_note,
    pair_wait,
    pending_row,
    remember_email,
    retry_after,
    set_link_token,
    token_for_email,
)
from ..service import Service
from .client_addr import client_host, client_ip, signup_key
from .oauth_pages import (
    CSRF_COOKIE,
    PENDING_COOKIE,
    _cookie_kwargs,
    _current,
    _field,
    _form,
    _html,
    _notice,
    _stamp,
)
from .pages import _shell, esc, new_nonce, security_headers
from .session import csrf_signature_ok, issue_csrf, same_origin, tokens_match

_BUSY = "Too many sign-in attempts. Wait and try again."
_TOKEN_NO = "That sign-in did not work."


async def authorize_post(request: Request) -> Response:
    svc = _current(request)
    if svc is None:
        return HTMLResponse("not ready", status_code=503)
    nonce = new_nonce()
    form = await request.form()
    posted = _field(form, "csrf")
    pending_raw = request.cookies.get(PENDING_COOKIE, "")
    if (
        not same_origin(request)
        or not tokens_match(posted, request.cookies.get(CSRF_COOKIE, ""))
        or not csrf_signature_ok(posted)
        or pending_row(svc.db, pending_raw) is None
    ):
        return _notice("The form expired. Start the sign-in again.", nonce, 403)
    hosted = _field(form, "hosted_token").strip()
    if hosted:
        return _hosted(svc, request, nonce, pending_raw, hosted)
    try:
        normalized = trial.normalize_email(_field(form, "email"))
    except ValueError:
        return _again(svc, nonce, pending_raw, "Enter a valid email address.")
    if email_is_demo(normalized):
        return _demo(svc, request, nonce, pending_raw, _field(form, "password"))
    return _person(svc, request, nonce, pending_raw, normalized)


def _limited(nonce: str, wait: int) -> Response:
    response = _notice(_BUSY, nonce, 429)
    response.headers["Retry-After"] = str(wait)
    return response


def _demo(svc: Service, request: Request, nonce: str, pending_raw: str, password: str) -> Response:
    if not CFG.trial_enabled:
        return _notice(trial.CLOSED, nonce)
    ip = client_ip(request)
    wait = pair_wait(svc.db, "oauth_demo", ip)
    if wait:
        return _limited(nonce, wait)
    if not password_matches(password):
        pair_note(svc.db, "oauth_demo", ip)
        return _again(svc, nonce, pending_raw, "That password is wrong.")
    try:
        code, redirect_uri, state = issue_code(svc.db, digest(pending_raw), DEMO_TOKEN_ID)
    except GrantError as exc:
        return _grant_page(exc, nonce)
    return redirect_to(redirect_uri, {"code": code, "state": state, "iss": issuer()})


def _hosted(svc: Service, request: Request, nonce: str, pending_raw: str, raw: str) -> Response:
    ip = client_ip(request)
    wait = pair_wait(svc.db, "oauth_token_login", ip)
    if wait:
        return _limited(nonce, wait)
    row = svc.db.one(
        "SELECT id FROM tokens WHERE token_hash=? AND revoked_at IS NULL AND kind='trial'",
        digest(raw),
    )
    if row is None:
        pair_note(svc.db, "oauth_token_login", ip)
        return _again(svc, nonce, pending_raw, _TOKEN_NO)
    try:
        code, redirect_uri, state = issue_code(svc.db, digest(pending_raw), str(row["id"]))
    except GrantError as exc:
        return _grant_page(exc, nonce)
    return redirect_to(redirect_uri, {"code": code, "state": state, "iss": issuer()})


def _person(svc: Service, request: Request, nonce: str, pending_raw: str, normalized: str) -> Response:
    pending = pending_row(svc.db, pending_raw)
    if pending is None:
        return _notice("The form expired. Start the sign-in again.", nonce, 403)
    email_hash = trial.email_hash(normalized)
    locked = str(pending["email_hash"]) if pending.get("email_hash") else ""
    if locked and locked != email_hash:
        return _notice("Start the sign-in again.", nonce, 403)
    existing = token_for_email(svc.db, email_hash)
    if existing is not None:
        if existing["revoked_at"] is not None:
            return _notice("This sign-in is closed.", nonce)
        return _mail(svc, request, nonce, pending_raw, normalized, email_hash, str(existing["id"]))
    if not CFG.trial_enabled:
        return _notice(trial.CLOSED, nonce)
    if trial.signup_day_full(svc.db):
        return _notice(trial.SPOTS_TAKEN, nonce)
    ip = signup_key(client_host(request))
    if trial.signup_blocked(svc.db, ip):
        return _notice("Too many sign-ups from this network today.", nonce, 429)
    if trial.is_disposable(normalized):
        return _notice("Use a regular email address.", nonce)
    trial.note_signup(svc.db, ip)
    if CFG.trial_email_verify:
        return _mail(svc, request, nonce, pending_raw, normalized, email_hash, None)
    minted = trial.mint_trial(svc.db, email_hash, verified=False)
    if minted is None:
        return _mail(svc, request, nonce, pending_raw, normalized, email_hash, None)
    row = token_for_email(svc.db, email_hash)
    if row is None:
        return _notice("This sign-in is closed.", nonce)
    return _issue(svc, nonce, pending_raw, str(row["id"]))


def _mail(
    svc: Service,
    request: Request,
    nonce: str,
    pending_raw: str,
    normalized: str,
    email_hash: str,
    token_id: str | None,
) -> Response:
    origin = public_origin()
    if origin is None:
        return _notice("Sign-in email is not available.", nonce)
    wait = retry_after(svc.db, "oauth_mail", client_ip(request))
    if wait:
        response = _notice("Too many sign-in emails from this network. Wait and try again.", nonce, 429)
        response.headers["Retry-After"] = str(wait)
        return response
    remember_email(svc.db, digest(pending_raw), email_hash, token_id)
    raw = issue_link(svc.db, digest(pending_raw), email_hash, token_id)
    url = f"{origin}/oauth/confirm?code={quote(raw)}"
    try:
        trial.send_magic_link(normalized, url, intro="Open this link to finish signing in. It works once.")
    except trial.TrialMailError as exc:
        note_failure(svc.db, "oauth_mail", client_ip(request))
        return _notice(exc.args[0] if exc.args else trial.MAIL_UNAVAILABLE, nonce)
    note_failure(svc.db, "oauth_mail", client_ip(request))
    return _html("<h1>Check your email</h1><p>Open the sign-in link in this browser. It works once.</p>", nonce)


def _issue(svc: Service, nonce: str, pending_raw: str, token_id: str) -> Response:
    try:
        code, redirect_uri, state = issue_code(svc.db, digest(pending_raw), token_id)
    except GrantError as exc:
        return _grant_page(exc, nonce)
    return redirect_to(redirect_uri, {"code": code, "state": state, "iss": issuer()})


def _again(svc: Service, nonce: str, pending_raw: str, message: str) -> HTMLResponse:
    row = pending_row(svc.db, pending_raw)
    uri = str(row["redirect_uri"]) if row else ""
    csrf = issue_csrf()
    return _stamp(_html(_form(csrf, message), nonce, 400, uri), pending_raw, csrf)


def _grant_page(exc: GrantError, nonce: str) -> HTMLResponse:
    if exc.error == "closed":
        return _notice("This sign-in is closed.", nonce)
    return _notice("This sign-in link is not valid.", nonce)


async def confirm_get(request: Request) -> Response:
    svc = _current(request)
    if svc is None:
        return HTMLResponse("not ready", status_code=503)
    nonce = new_nonce()
    raw = request.query_params.get("code", "")
    if not link_pending(svc.db, raw):
        return _notice("This link is not valid.", nonce)
    try:
        pending = peek_confirm(svc.db, raw, request.cookies.get(PENDING_COOKIE, ""))
    except GrantError as exc:
        if exc.error == "browser":
            return _notice("Open this link in the browser where you started sign-in.", nonce)
        return _notice("This link is not valid.", nonce)
    csrf = issue_csrf()
    body = (
        "<h1>Finish sign-in</h1>"
        "<p>This link stays valid until you continue.</p>"
        '<form method=post action="/oauth/confirm">'
        f'<input type=hidden name=csrf value="{esc(csrf)}">'
        f'<input type=hidden name=code value="{esc(raw)}">'
        "<button class=primary type=submit>Continue</button></form>"
    )
    response = _html(body, nonce, redirect_uri=str(pending["redirect_uri"]))
    response.set_cookie(CSRF_COOKIE, csrf, max_age=int(CFG.csrf_ttl_s), **_cookie_kwargs())
    return response


async def confirm_post(request: Request) -> Response:
    svc = _current(request)
    if svc is None:
        return HTMLResponse("not ready", status_code=503)
    nonce = new_nonce()
    form = await request.form()
    posted = _field(form, "csrf")
    raw = _field(form, "code")
    if (
        not same_origin(request)
        or not tokens_match(posted, request.cookies.get(CSRF_COOKIE, ""))
        or not csrf_signature_ok(posted)
    ):
        return _notice("The form expired. Open the link again.", nonce, 403)
    cookie = request.cookies.get(PENDING_COOKIE, "")
    try:
        pending = peek_confirm(svc.db, raw, cookie)
    except GrantError as exc:
        if exc.error == "browser":
            return _notice("Open this link in the browser where you started sign-in.", nonce)
        return _notice("This link is not valid.", nonce)
    if not pending.get("token_id"):
        blocked = _mint_for_confirm(svc, pending)
        if blocked is not None:
            return _notice(blocked, nonce)
    try:
        code, redirect_uri, state = confirm_link(svc.db, raw, cookie)
    except GrantError as exc:
        return _grant_page(exc, nonce)
    return redirect_to(redirect_uri, {"code": code, "state": state, "iss": issuer()})


def _mint_for_confirm(svc: Service, pending: dict[str, Any]) -> str | None:
    email_hash = str(pending.get("email_hash") or "")
    if not email_hash:
        return "This link is not valid."
    if trial.signup_day_full(svc.db):
        return trial.SPOTS_TAKEN
    if not CFG.trial_enabled:
        return trial.CLOSED
    trial.mint_trial(svc.db, email_hash, verified=True)
    row = token_for_email(svc.db, email_hash)
    if row is None or row["revoked_at"] is not None:
        return "This sign-in is closed."
    set_link_token(svc.db, str(pending["link_hash"]), str(row["id"]))
    return None


def privacy(_request: Request) -> Response:
    nonce = new_nonce()
    days = int(CFG.trial_retention_days)
    share = int(CFG.share_ttl_days)
    share_text = "until it is revoked" if share == 0 else f"{share} days"
    contact = trial.abuse_email() or "the person who runs this copy"
    body = (
        "<h1>Privacy</h1>"
        "<p class=error>Draft for owner approval.</p>"
        "<p>This page is a draft. The person who runs this copy has not approved it yet.</p>"
        "<p>A sign-in stores a SHA-256 hash of the normalized email, not the email itself.</p>"
        "<p>Sign-in tokens are stored only as hashes.</p>"
        "<p>A build stores the request text, the notes sent with it, and the generated files.</p>"
        f"<p>Idle hosted workspaces are deleted after {days} days.</p>"
        f"<p>A share link lasts {esc(share_text)}.</p>"
        f"<p>Pages are written by {esc(CFG.trial_model)}.</p>"
        "<p>Your IP address is kept for up to a day for rate limits.</p>"
        "<p>This copy does not sell the data and does not show ads.</p>"
        f"<p>To ask for deletion, write to {esc(contact)}.</p>"
    )
    return HTMLResponse(_shell("Privacy", body, nonce), status_code=200, headers=security_headers(nonce))
