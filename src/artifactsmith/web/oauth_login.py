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


EFFECTIVE_DATE = "October 20, 2026"
# Legal entity line. Vikas has not confirmed the legal name yet.
OPERATOR = "Neuralami"
CONTACT_EMAIL = "kai@neuralami.ai"


def _contact() -> str:
    return trial.abuse_email() or CONTACT_EMAIL


def _model_maker(model: str) -> str:
    head = str(model).split("/", 1)[0].strip().lower()
    named = {"anthropic": "Anthropic", "openai": "OpenAI", "chatgpt": "OpenAI", "google": "Google"}.get(head, "")
    if named:
        return named
    if head.startswith("gpt-"):
        return "OpenAI"
    return ""


def _model_line() -> str:
    model = str(CFG.trial_model)
    maker = _model_maker(model)
    return f"{maker} ({model})" if maker else model


def _configured_share_days() -> int | None:
    """Days a hand-made account's new share lasts. None means until it is turned off.

    Same rule as Service._share_exp when the caller does not pass a shorter ttl:
    AM_SHARE_TTL_DAYS, and AM_SHARE_TTL_MAX_DAYS when that ceiling is lower.
    """
    policy = int(CFG.share_ttl_days)
    if policy <= 0:
        return None
    ceiling = max(1, int(CFG.share_ttl_max_days or 365))
    return max(1, min(policy, ceiling))


def _hosted_share_days() -> int:
    """Days a hosted sign-up's new share lasts.

    Service.share caps trial and demo principals at trial.SHARE_DAYS, and also
    at a positive AM_SHARE_TTL_DAYS. The page shows the shorter of the two.
    """
    cap = int(trial.SHARE_DAYS)
    configured = _configured_share_days()
    if configured is None:
        return cap
    return min(cap, configured)


def _lasts(days: int | None) -> str:
    if days is None or days <= 0:
        return "until you turn the link off"
    unit = "day" if days == 1 else "days"
    return f"{days} {unit}, or until you turn the link off"


def _share_who() -> str:
    hosted = _lasts(_hosted_share_days())
    invited = _lasts(_configured_share_days())
    return (
        "Anyone with a share link can open that page. "
        f"Links from hosted sign-ups last {hosted}. "
        f"Links from accounts we set up for invited people last {invited}."
    )


def _share_kept() -> str:
    hosted = _lasts(_hosted_share_days())
    invited = _lasts(_configured_share_days())
    return (
        f"Share links from hosted sign-ups last {hosted}. "
        f"Share links from accounts we set up for invited people last {invited}."
    )


def _invited_model_sentence() -> str:
    maker = _model_maker(str(CFG.default_model))
    if maker:
        return f"Accounts we set up for invited people use {maker} models by default."
    return "Accounts we set up for invited people use the configured model by default."


_GATEWAY_LOGS = (
    "A copy of each request sent to the model stays in the model gateway's logs after you delete the page. "
    "Those logs are not deleted on a schedule today."
)


def _legal(title: str, sections: list[tuple[str, list[str]]], intro: str, nonce: str) -> Response:
    parts = [f"<h1>{esc(title)}</h1>", f"<p>Effective date: {esc(EFFECTIVE_DATE)}</p>", f"<p>{intro}</p>"]
    for head, items in sections:
        parts.append(f"<h2>{esc(head)}</h2><ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>")
    return HTMLResponse(_shell(title, "".join(parts), nonce), status_code=200, headers=security_headers(nonce))


def privacy(_request: Request) -> Response:
    nonce = new_nonce()
    days = int(CFG.trial_retention_days)
    contact = esc(_contact())
    intro = (
        "This policy covers the hosted copy of ArtifactSmith at artifactsmith.neuralami.ai, run by "
        f"{esc(OPERATOR)}. ArtifactSmith is open-source software. If you run your own copy, this policy "
        "does not apply to it."
    )
    sections = [
        (
            "What we collect",
            [
                "Your email address, as a fingerprint. We keep a one-way SHA-256 hash of it, not the address. "
                "When we send you a sign-in link, the address is used to send that one email and is not saved "
                "in our database.",
                "What you ask it to build: the request text, the notes or data your assistant sends with it, "
                "and the pages and files it makes.",
                "Sign-in records. Access keys and sign-in links are stored only as hashes. When you connect "
                "from ChatGPT, Claude or another app, we keep that app's name, its return address and hashed keys.",
                "Your IP address, for rate limits and abuse checks. It also appears in our server logs.",
                "Activity records: a log of actions on pages (created, built, shared, deleted) with times and page IDs.",
                "Cookies needed to sign in, stay signed in, block forged requests and remember your time zone. "
                "No ad or analytics cookies.",
            ],
        ),
        (
            "How we use it",
            [
                "To build, store and show your pages and files, keep you signed in, stop abuse, limit costs "
                "and fix problems.",
                "We do not sell your data, show ads, or use your content to train AI models.",
            ],
        ),
        (
            "Who sees it",
            [
                f"AI model provider: your request and notes are sent to {esc(_model_line())} to write the page.",
                _invited_model_sentence(),
                "Namecheap (Private Email) receives your email address when we send you a sign-in link.",
                "Cloudflare carries all traffic to the site, including your IP address.",
                _share_who(),
                "Authorities, if the law requires it.",
                "Your files and database are on servers we run ourselves in the United States.",
            ],
        ),
        (
            "How long we keep it",
            [
                f"Pages and files: until you delete them. A hosted workspace with no activity for {days} days "
                "is deleted with everything in it.",
                _share_kept(),
                "IP addresses used for rate limits: about one day.",
                "Unused sign-in links: removed about a day after they expire.",
                "App connections: removed about a day after they expire.",
                "Your email fingerprint: kept after your workspace is deleted, so one email cannot start "
                "over and over. Ask us and we will remove it.",
                "Server logs (which include IP addresses), activity records and database backups taken "
                "before updates: these are not deleted on a schedule today. They are kept until we delete "
                "them, and backups may still hold pages you deleted.",
                _GATEWAY_LOGS,
            ],
        ),
        (
            "Your choices",
            [
                "Delete a page: ask your assistant to delete it. This removes every version and share link.",
                "Turn off a share link: ask your assistant to unshare it. It stops working right away.",
                f"See or delete everything we hold: email {contact} from the address you signed in with. "
                "We reply within 30 days.",
                "Disconnect an app: remove ArtifactSmith in that app's settings, or ask us to revoke it.",
            ],
        ),
        (
            "Security",
            [
                "All traffic uses HTTPS. Keys, sign-in links and session cookies are stored only as hashes. "
                "Each workspace is kept separate. No system is perfectly secure; if a breach affects you, we "
                "will tell you.",
            ],
        ),
        ("Children", ["ArtifactSmith is not for children under 13, and we do not knowingly collect their data."]),
        ("Changes", ["If we change this policy, we post the new version here with a new date."]),
        ("Contact", [f"{contact}, {esc(OPERATOR)}."]),
    ]
    return _legal("Privacy policy", sections, intro, nonce)


def terms(_request: Request) -> Response:
    nonce = new_nonce()
    days = int(CFG.trial_retention_days)
    contact = esc(_contact())
    intro = (
        "These terms cover the hosted copy of ArtifactSmith at artifactsmith.neuralami.ai, run by "
        f"{esc(OPERATOR)}. By using it, you agree to them."
    )
    sections = [
        (
            "The software",
            [
                "ArtifactSmith is open-source software under the MIT License. You can get it at "
                '<a href="https://github.com/vrijsinghani/Artifactsmith">github.com/vrijsinghani/Artifactsmith</a> '
                "and run your own copy. These terms cover only our hosted copy, not your use of the code.",
            ],
        ),
        (
            "The hosted copy",
            [
                "It lets you see ArtifactSmith work before you install it. It is provided as-is, with no "
                "warranty and no promise that it will be available, fast, or free of errors.",
                f"We may limit usage, change features, or stop the hosted copy at any time. Workspaces with no "
                f"activity for {days} days are deleted.",
                "Keep your own copies of anything you need.",
            ],
        ),
        (
            "Acceptable use",
            [
                "Do not use it to break the law, or to make content that is illegal, infringes someone's "
                "rights, harasses people, or sexualizes minors.",
                "Do not use it for spam, phishing, malware, or pages that pretend to be someone else.",
                "Do not try to get around limits, get into other people's workspaces, or overload the service.",
            ],
        ),
        (
            "Your content",
            [
                "You own what you send and what it makes for you. You give us permission to store, process "
                "and show it only to run the service for you, including showing pages to anyone you share a "
                "link with.",
                "You are responsible for what you send and share, and for having the right to use it.",
                "Pages are written by an AI model and can be wrong. Check them before you rely on them.",
            ],
        ),
        (
            "Ending use",
            [
                "You can stop at any time and ask us to delete your data.",
                "We may suspend or delete accounts, pages or share links that break these terms, without notice.",
            ],
        ),
        (
            "Liability",
            [
                "To the extent the law allows, we are not liable for any indirect or consequential loss, or "
                "for lost data or profits, from using the hosted copy. Our total liability is limited to US$100.",
            ],
        ),
        ("Privacy", ['See our <a href="/privacy">privacy policy</a>.']),
        ("Changes", ["If we change these terms, we post the new version here with a new date."]),
        ("Contact", [f"{contact}, {esc(OPERATOR)}."]),
    ]
    return _legal("Terms of service", sections, intro, nonce)
