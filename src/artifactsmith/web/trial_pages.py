"""Self-serve /trial pages. The token is shown once. The snippet uses a placeholder."""

from __future__ import annotations

import json
from collections.abc import Callable
from html import escape
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route

from ..config import CFG
from ..service import Service
from ..trial import (
    CLOSED,
    MAIL_UNAVAILABLE,
    TrialMailError,
    consume_link,
    email_hash,
    is_disposable,
    issue_link,
    link_pending,
    link_url,
    mint_trial,
    normalize_email,
    note_signup,
    send_magic_link,
    signup_blocked,
)
from .client_addr import client_ip, limit_key
from .pages import _shell, new_nonce, security_headers
from .session import csrf_signature_ok, issue_csrf, same_origin, tokens_match

_COOKIE = "am_trial_csrf"


def _cookie_kwargs() -> dict[str, Any]:
    return {"path": "/trial", "httponly": True, "secure": bool(CFG.cookie_secure), "samesite": "strict"}


def _html(body: str, nonce: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(_shell("ArtifactSmith trial", body, nonce), status_code=status, headers=security_headers(nonce))


def _field(form: Any, name: str) -> str:
    value = form.get(name) if hasattr(form, "get") else None
    return value if isinstance(value, str) else ""


def _client_snippets(api_url: str) -> str:
    """Claude Desktop, Cursor, and a generic client. The token is a placeholder."""
    url = api_url.rstrip("/") + "/mcp"
    claude = {
        "mcpServers": {
            "artifactsmith": {
                "type": "http",
                "url": url,
                "headers": {"Authorization": "Bearer YOUR_TOKEN"},
            }
        }
    }
    cursor = {
        "mcpServers": {
            "artifactsmith": {
                "url": url,
                "headers": {"Authorization": "Bearer YOUR_TOKEN"},
            }
        }
    }
    generic = f"Server URL: {url}\nAuthorization: Bearer YOUR_TOKEN"
    blocks = [
        ("Claude Desktop", json.dumps(claude, indent=2)),
        ("Cursor", json.dumps(cursor, indent=2)),
        ("Generic MCP client", generic),
    ]
    parts = [f"<h2>{escape(title)}</h2><pre>{escape(text)}</pre>" for title, text in blocks]
    return "".join(parts)


def _form(csrf: str, message: str = "") -> str:
    note = f'<p class="error">{escape(message)}</p>' if message else ""
    builds = int(CFG.trial_build_limit)
    return (
        "<h1>Start a trial</h1>"
        f"<p class=muted>One workspace, {builds} free builds, on the house model.</p>"
        f"{note}"
        '<form method=post action="/trial">'
        f'<input type=hidden name=csrf value="{escape(csrf, quote=True)}">'
        "<label for=email>Email</label>"
        "<input id=email name=email type=email autocomplete=email required maxlength=254>"
        "<button class=primary type=submit>Email me a link</button>"
        "</form>"
    )


def _confirm(csrf: str, code: str) -> str:
    return (
        "<h1>Show your trial token</h1>"
        "<p>This link stays valid until you continue.</p>"
        '<form method=post action="/trial/verify">'
        f'<input type=hidden name=csrf value="{escape(csrf, quote=True)}">'
        f'<input type=hidden name=code value="{escape(code, quote=True)}">'
        "<button class=primary type=submit>Show my token</button>"
        "</form>"
    )


def trial_routes(current: Callable[[], Service | None]) -> list[Route]:
    async def trial_get(request: Request) -> Response:
        svc: Service | None = current()
        if svc is None:
            return HTMLResponse("not ready", status_code=503)
        nonce = new_nonce()
        if not CFG.trial_enabled:
            return _html("<h1>Trial</h1><p>Trial sign-up is closed.</p>", nonce)
        csrf = issue_csrf()
        response = _html(_form(csrf), nonce)
        response.set_cookie(_COOKIE, csrf, max_age=int(CFG.csrf_ttl_s), **_cookie_kwargs())
        return response

    async def trial_post(request: Request) -> Response:
        svc = current()
        if svc is None:
            return HTMLResponse("not ready", status_code=503)
        nonce = new_nonce()
        if not CFG.trial_enabled:
            return _html("<h1>Trial</h1><p>Trial sign-up is closed.</p>", nonce)
        form = await request.form()
        posted = _field(form, "csrf")
        if (
            not same_origin(request)
            or not tokens_match(posted, request.cookies.get(_COOKIE, ""))
            or not csrf_signature_ok(posted)
        ):
            return _html("<h1>Trial</h1><p class=error>The form expired. Load the page again.</p>", nonce, status=403)
        ip = limit_key(client_ip(request))
        if signup_blocked(svc.db, ip):
            return _html(
                "<h1>Trial</h1><p class=error>Too many sign-ups from this network today.</p>", nonce, status=429
            )
        try:
            normalized = normalize_email(_field(form, "email"))
        except ValueError:
            csrf = issue_csrf()
            response = _html(_form(csrf, "Enter a valid email address."), nonce, status=400)
            response.set_cookie(_COOKIE, csrf, max_age=int(CFG.csrf_ttl_s), **_cookie_kwargs())
            return response
        note_signup(svc.db, ip)
        if is_disposable(normalized):
            return _html("<h1>Trial</h1><p class=error>Use a regular email address.</p>", nonce, status=400)
        raw = issue_link(svc.db, email_hash(normalized))
        try:
            send_magic_link(normalized, link_url(raw))
        except TrialMailError:
            return _html(f"<h1>Trial</h1><p class=error>{escape(MAIL_UNAVAILABLE)}</p>", nonce, status=503)
        return _html(
            "<h1>Check your inbox</h1><p>If that address can receive mail, a sign-up link is on its way.</p>", nonce
        )

    async def verify_get(request: Request) -> Response:
        svc = current()
        if svc is None:
            return HTMLResponse("not ready", status_code=503)
        nonce = new_nonce()
        if not CFG.trial_enabled:
            return _html(f"<h1>Trial</h1><p>{escape(CLOSED)}</p>", nonce)
        code = request.query_params.get("code", "")
        if not link_pending(svc.db, code):
            return _html("<h1>Trial</h1><p class=error>That link is invalid or already used.</p>", nonce, status=400)
        csrf = issue_csrf()
        response = _html(_confirm(csrf, code), nonce)
        response.set_cookie(_COOKIE, csrf, max_age=int(CFG.csrf_ttl_s), **_cookie_kwargs())
        response.headers["Cache-Control"] = "no-store"
        return response

    async def verify_post(request: Request) -> Response:
        svc = current()
        if svc is None:
            return HTMLResponse("not ready", status_code=503)
        nonce = new_nonce()
        if not CFG.trial_enabled:
            return _html(f"<h1>Trial</h1><p>{escape(CLOSED)}</p>", nonce)
        form = await request.form()
        posted = _field(form, "csrf")
        if (
            not same_origin(request)
            or not tokens_match(posted, request.cookies.get(_COOKIE, ""))
            or not csrf_signature_ok(posted)
        ):
            return _html("<h1>Trial</h1><p class=error>The form expired. Load the page again.</p>", nonce, status=403)
        digest = consume_link(svc.db, _field(form, "code"))
        if digest is None:
            return _html("<h1>Trial</h1><p class=error>That link is invalid or already used.</p>", nonce, status=400)
        minted = mint_trial(svc.db, digest)
        if minted is None:
            return _html("<h1>Trial</h1><p>This email already has a trial. The token was shown once.</p>", nonce)
        raw, workspace = minted
        body = (
            "<h1>Your trial token</h1>"
            "<p>Copy it now. It is not shown again.</p>"
            f'<p><input readonly value="{escape(raw, quote=True)}"></p>'
            f"<p class=muted>Workspace {escape(workspace)}. Builds left: {int(CFG.trial_build_limit)}.</p>"
            "<p>Paste the token where the snippet says YOUR_TOKEN.</p>"
            f"{_client_snippets(CFG.api_url)}"
            '<p><a href="/app/login">Open My pages</a></p>'
        )
        response = _html(body, nonce)
        response.headers["Cache-Control"] = "no-store"
        return response

    return [
        Route("/trial", trial_get, methods=["GET"]),
        Route("/trial", trial_post, methods=["POST"]),
        Route("/trial/verify", verify_get, methods=["GET"]),
        Route("/trial/verify", verify_post, methods=["POST"]),
    ]
