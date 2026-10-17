"""Browser sign-in for OAuth. The protocol routes live in oauth_http."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route

from ..config import CFG
from ..oauth_http import issuer, metadata, protected_resource, redirect_to, register, revoke, token
from ..oauth_origin import (
    client_redirect_ok,
    form_source,
    public_origin,
    redirect_allowed,
    resource_matches,
    resource_url,
)
from ..oauth_store import (
    AUTHORIZE_LIMIT,
    SCOPE,
    client_redirects,
    client_row,
    note_failure,
    retry_after,
    save_pending,
)
from ..service import Service
from .client_addr import client_ip
from .pages import _shell, esc, new_nonce, security_headers
from .session import issue_csrf

PENDING_COOKIE = "am_oauth"
CSRF_COOKIE = "am_oauth_csrf"
_CHALLENGE = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def _cookie_kwargs() -> dict[str, Any]:
    return {"httponly": True, "secure": bool(CFG.cookie_secure), "samesite": "lax", "path": "/"}


def _html(body: str, nonce: str, status: int = 200, redirect_uri: str = "") -> HTMLResponse:
    return HTMLResponse(
        _shell("Sign in", body, nonce),
        status_code=status,
        headers=security_headers(nonce, form_source(redirect_uri)),
    )


def _notice(message: str, nonce: str, status: int = 400) -> HTMLResponse:
    return _html(f"<h1>Sign in</h1><p class=error>{esc(message)}</p>", nonce, status)


def _field(form: Any, name: str) -> str:
    value = form.get(name) if hasattr(form, "get") else None
    return value if isinstance(value, str) else ""


def _consent(uri: str) -> str:
    host = (urlparse(uri).hostname or "").lower()
    parts = [f"<p>This will sign in and send you back to {esc(uri)}</p>"]
    if host in {"127.0.0.1", "localhost"}:
        parts.append("<p>This address is on your own computer.</p>")
    if uri == "https://claude.ai/api/mcp/auth_callback":
        parts.append("<p>Claude.ai will receive the sign-in.</p>")
    if uri == "https://chatgpt.com/connector_platform_oauth_redirect" or uri.startswith(
        "https://chatgpt.com/connector/oauth/"
    ):
        parts.append("<p>ChatGPT will receive the sign-in.</p>")
    return "".join(parts)


def _form(csrf: str, message: str = "") -> str:
    note = f'<p class="error">{esc(message)}</p>' if message else ""
    return (
        "<h1>Sign in</h1>"
        "<p>Use the email for this hosted copy. A review password is only for the directory account.</p>"
        f"{note}"
        '<form method=post action="/oauth/authorize">'
        f'<input type=hidden name=csrf value="{esc(csrf)}">'
        "<label for=email>Email</label>"
        "<input id=email name=email type=email autocomplete=username required maxlength=254>"
        "<label for=password>Password</label>"
        "<input id=password name=password type=password autocomplete=current-password maxlength=200>"
        "<label for=hosted_token>Already have a token? Paste it.</label>"
        "<input id=hosted_token name=hosted_token type=password autocomplete=off maxlength=200>"
        "<button class=primary type=submit>Continue</button></form>"
    )


def _stamp(response: HTMLResponse, pending: str, csrf: str) -> HTMLResponse:
    response.set_cookie(PENDING_COOKIE, pending, max_age=1800, **_cookie_kwargs())
    response.set_cookie(CSRF_COOKIE, csrf, max_age=int(CFG.csrf_ttl_s), **_cookie_kwargs())
    return response


def _challenge_ok(value: str) -> bool:
    return 43 <= len(value) <= 128 and all(ch in _CHALLENGE for ch in value)


def _query(request: Request, name: str) -> str:
    return request.query_params.get(name, "")[:2000]


async def authorize_get(request: Request) -> Response:
    svc = _current(request)
    if svc is None:
        return HTMLResponse("not ready", status_code=503)
    nonce = new_nonce()
    if public_origin() is None:
        return _notice("OAuth sign-in is not available on this copy.", nonce)
    ip = client_ip(request)
    wait = retry_after(svc.db, "oauth_authorize", ip, limit=AUTHORIZE_LIMIT)
    if wait:
        response = _notice("Too many sign-in attempts. Wait and try again.", nonce, 429)
        response.headers["Retry-After"] = str(wait)
        return response
    note_failure(svc.db, "oauth_authorize", ip)
    client_id = _query(request, "client_id")
    redirect_uri = _query(request, "redirect_uri")
    row = client_row(svc.db, client_id)
    registered = client_redirects(row) if row else []
    if row is None or not client_redirect_ok(registered, redirect_uri) or not redirect_allowed(redirect_uri):
        return _notice("This sign-in link is not valid.", nonce)
    state = request.query_params.get("state", "")
    if not state or len(state) > 512:
        return _notice("This sign-in link is missing state.", nonce)
    problem = _authorize_problem(request, redirect_uri, state)
    if problem is not None:
        return problem
    resource = _accepted_resource(request.query_params.get("resource", ""))
    if request.query_params.get("resource", "") and resource is None:
        return redirect_to(redirect_uri, _error("invalid_target", state))
    scope = request.query_params.get("scope", "")
    pending = save_pending(
        svc.db,
        client_id=client_id,
        redirect_uri=redirect_uri,
        state=state,
        challenge=_query(request, "code_challenge"),
        scope=scope or SCOPE,
        resource=resource or "",
        client_ip=ip,
    )
    csrf = issue_csrf()
    return _stamp(_html(_consent(redirect_uri) + _form(csrf), nonce, redirect_uri=redirect_uri), pending, csrf)


def _authorize_problem(request: Request, redirect_uri: str, state: str) -> Response | None:
    if request.query_params.get("response_type", "") != "code":
        return redirect_to(redirect_uri, _error("unsupported_response_type", state))
    method = request.query_params.get("code_challenge_method", "")
    if method != "S256" or not _challenge_ok(request.query_params.get("code_challenge", "")):
        return redirect_to(redirect_uri, _error("invalid_request", state))
    scope = request.query_params.get("scope", "")
    if scope and scope != SCOPE:
        return redirect_to(redirect_uri, _error("invalid_scope", state))
    return None


def _accepted_resource(raw: str) -> str | None:
    if not raw:
        return ""
    origin = public_origin()
    if origin is None or not resource_matches(raw, resource_url(origin)):
        return None
    return resource_url(origin)


def _error(error: str, state: str) -> dict[str, str]:
    return {"error": error, "state": state, "iss": issuer()}


def _current(request: Request) -> Service | None:
    current = getattr(request.app.state, "current", None)
    if not callable(current):
        return None
    found = current()
    return found if isinstance(found, Service) else None


def oauth_routes(current: Callable[[], Service | None]) -> list[Route]:
    from .oauth_login import authorize_post, confirm_get, confirm_post, privacy

    def bind(handler: Any) -> Any:
        async def wrapped(request: Request) -> Response:
            request.app.state.current = current
            result = await handler(request)
            if not isinstance(result, Response):
                raise TypeError("oauth handler did not return a response")
            return result

        return wrapped

    return [
        Route("/.well-known/oauth-authorization-server", metadata),
        Route("/.well-known/oauth-protected-resource", protected_resource),
        Route("/.well-known/oauth-protected-resource/mcp", protected_resource),
        Route("/oauth/register", bind(register), methods=["POST"]),
        Route("/oauth/token", bind(token), methods=["POST"]),
        Route("/oauth/revoke", bind(revoke), methods=["POST"]),
        Route("/oauth/authorize", bind(authorize_get), methods=["GET"]),
        Route("/oauth/authorize", bind(authorize_post), methods=["POST"]),
        Route("/oauth/confirm", bind(confirm_get), methods=["GET"]),
        Route("/oauth/confirm", bind(confirm_post), methods=["POST"]),
        Route("/privacy", privacy),
        Route("/privacy/", privacy),
    ]
