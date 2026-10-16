"""HTTP routes for /app/. Views stay thin; catalog and session own the rules."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from ..config import CFG
from ..service import AMError, Service
from .catalog import STATUSES, list_pages, page_detail, parse_page
from .client_addr import client_ip
from .clock import TZ_COOKIE, default_zone, zone_from_name
from .pages import (
    new_nonce,
    render_detail,
    render_list,
    render_login,
    render_message,
    render_not_found,
    security_headers,
)
from .session import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    LoginResult,
    attempt_login,
    clear_csrf_cookie,
    clear_session_cookie,
    end_session,
    issue_csrf,
    open_session,
    same_origin,
    set_csrf_cookie,
    set_session_cookie,
    set_tz_cookie,
    tokens_match,
)
from .trial_pages import trial_routes


def _stamp(response: Response) -> Response:
    nonce = new_nonce()
    for key, value in security_headers(nonce).items():
        response.headers[key] = value
    return response


def _html(body: str, nonce: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(body, status_code=status, headers=security_headers(nonce))


def _text_field(form: Any, name: str) -> str:
    value = form.get(name)
    return value if isinstance(value, str) else ""


def _not_ready() -> PlainTextResponse:
    return PlainTextResponse("not ready", status_code=503, headers=security_headers(new_nonce()))


def _viewer_zone(request: Request) -> tuple[ZoneInfo, str | None]:
    """Zone to render, and a cookie value when ``tz`` is a real zone name."""
    chosen = request.query_params.get("tz")
    if chosen is not None:
        zone = zone_from_name(chosen)
        if zone is not None:
            return zone, zone.key
    saved = zone_from_name(request.cookies.get(TZ_COOKIE, ""))
    if saved is not None:
        return saved, None
    return default_zone(), None


def _remember_zone(response: Response, zone_name: str | None) -> Response:
    if zone_name:
        set_tz_cookie(response, zone_name)
    return response


def _login_redirect() -> Response:
    response = _stamp(RedirectResponse("/app/login", status_code=303))
    clear_session_cookie(response)
    return response


def _login_response(result: LoginResult) -> Response:
    if result.status == 303 and result.session_raw:
        response = _stamp(RedirectResponse("/app/", status_code=303))
        set_session_cookie(response, result.session_raw)
        clear_csrf_cookie(response)
        return response
    if result.status == 429:
        nonce = new_nonce()
        response = _html(render_message(nonce, "My pages", result.message), nonce, status=429)
        if result.retry_after:
            response.headers["Retry-After"] = str(result.retry_after)
        return response
    csrf = issue_csrf()
    nonce = new_nonce()
    response = _html(render_login(nonce, csrf, result.message), nonce, status=result.status)
    set_csrf_cookie(response, csrf)
    return response


class HostGuard:
    """Reject a Host header outside AM_ALLOWED_HOSTS before a session cookie is set."""

    def __init__(self, app: Starlette) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") == "http":
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            allowed = {item.lower() for item in CFG.allowed_hosts}
            if headers.get("host", "").lower() not in allowed:
                denied = PlainTextResponse("bad host", status_code=400, headers=security_headers(new_nonce()))
                await denied(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_pages(get_svc: Callable[[], Service]) -> HostGuard:
    def current() -> Service | None:
        try:
            return get_svc()
        except AMError:
            return None

    async def login_get(request: Request) -> Response:
        svc = current()
        if svc is None:
            return _not_ready()
        if open_session(svc.db, request.cookies.get(SESSION_COOKIE)):
            return _stamp(RedirectResponse("/app/", status_code=303))
        csrf = issue_csrf()
        nonce = new_nonce()
        response = _html(render_login(nonce, csrf), nonce)
        set_csrf_cookie(response, csrf)
        return response

    async def login_post(request: Request) -> Response:
        svc = current()
        if svc is None:
            return _not_ready()
        form = await request.form()
        result = attempt_login(
            svc.db,
            client_ip(request),
            _text_field(form, "csrf"),
            request.cookies.get(CSRF_COOKIE, ""),
            _text_field(form, "token"),
            origin_ok=same_origin(request),
        )
        return _login_response(result)

    async def home(request: Request) -> Response:
        svc = current()
        if svc is None:
            return _not_ready()
        opened = open_session(svc.db, request.cookies.get(SESSION_COOKIE))
        if opened is None:
            return _login_redirect()
        status = request.query_params.get("status", "")
        if status and status not in STATUSES:
            nonce = new_nonce()
            return _html(render_message(nonce, "My pages", "Unknown status."), nonce, status=400)
        query = request.query_params.get("q", "")[:200]
        page = parse_page(request.query_params.get("page", "1"))
        if page is None:
            nonce = new_nonce()
            return _html(render_message(nonce, "My pages", "Unknown page."), nonce, status=400)
        workspace = str(opened["principal"]["workspace"])
        rows, has_next = list_pages(svc.db, workspace, query, status, page)
        zone, store = _viewer_zone(request)
        nonce = new_nonce()
        body = render_list(nonce, workspace, rows, query, status, str(opened["csrf"]), page, has_next, zone)
        return _remember_zone(_html(body, nonce), store)

    async def detail(request: Request) -> Response:
        svc = current()
        if svc is None:
            return _not_ready()
        opened = open_session(svc.db, request.cookies.get(SESSION_COOKIE))
        if opened is None:
            return _login_redirect()
        workspace = str(opened["principal"]["workspace"])
        page = page_detail(svc.db, workspace, str(request.path_params["artifact_id"]))
        nonce = new_nonce()
        if page is None:
            return _html(render_not_found(nonce), nonce, status=404)
        zone, store = _viewer_zone(request)
        body = render_detail(nonce, workspace, page, str(opened["csrf"]), zone)
        return _remember_zone(_html(body, nonce), store)

    async def logout(request: Request) -> Response:
        svc = current()
        if svc is None:
            return _not_ready()
        raw = request.cookies.get(SESSION_COOKIE)
        opened = open_session(svc.db, raw)
        if opened is None:
            return _login_redirect()
        form = await request.form()
        if not same_origin(request) or not tokens_match(_text_field(form, "csrf"), str(opened["csrf"])):
            nonce = new_nonce()
            return _html(render_message(nonce, "My pages", "The form expired. Load the page again."), nonce, status=403)
        end_session(svc.db, raw or "")
        return _login_redirect()

    app = Starlette(
        routes=[
            Route("/app/login", login_get, methods=["GET"]),
            Route("/app/login", login_post, methods=["POST"]),
            Route("/app", home, methods=["GET"]),
            Route("/app/", home, methods=["GET"]),
            Route("/app/a/{artifact_id}", detail, methods=["GET"]),
            Route("/app/logout", logout, methods=["POST"]),
            *trial_routes(current),
        ]
    )
    return HostGuard(app)
