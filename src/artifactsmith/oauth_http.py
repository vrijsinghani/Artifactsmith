"""OAuth metadata, dynamic registration, token, and revoke. No browser pages."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from .oauth_grants import exchange_code, refresh_grant, revoke_token
from .oauth_origin import public_origin, redirect_allowed, resource_url
from .oauth_store import (
    MAX_REDIRECTS,
    REGISTER_DAY_CAP,
    REGISTER_LIMIT,
    REGISTER_WINDOW_S,
    SCOPE,
    GrantError,
    client_row,
    clients_today,
    note_failure,
    register_client,
    retry_after,
)

_GRANTS = {"authorization_code", "refresh_token"}


def issuer() -> str:
    """Public issuer, or empty when this copy must not publish a host."""
    return public_origin() or ""


def redirect_to(uri: str, params: dict[str, str]) -> RedirectResponse:
    query = urlencode({key: value for key, value in params.items() if value != ""})
    joiner = "&" if "?" in uri else "?"
    response = RedirectResponse(f"{uri}{joiner}{query}", status_code=303)
    response.headers["Cache-Control"] = "no-store"
    return response


def _json(body: dict[str, Any], status: int = 200) -> JSONResponse:
    response = JSONResponse(body, status_code=status)
    response.headers["Cache-Control"] = "no-store"
    return response


def _oauth_error(error: str, status: int = 400) -> JSONResponse:
    return _json({"error": error}, status)


def metadata(_request: Request) -> Response:
    origin = public_origin()
    if origin is None:
        return _json({"error": "not_found"}, 404)
    return _json(
        {
            "issuer": origin,
            "authorization_endpoint": origin + "/oauth/authorize",
            "token_endpoint": origin + "/oauth/token",
            "registration_endpoint": origin + "/oauth/register",
            "revocation_endpoint": origin + "/oauth/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "authorization_response_iss_parameter_supported": True,
            "scopes_supported": [SCOPE],
        }
    )


def protected_resource(_request: Request) -> Response:
    origin = public_origin()
    if origin is None:
        return _json({"error": "not_found"}, 404)
    return _json(
        {
            "resource": resource_url(origin),
            "authorization_servers": [origin],
            "bearer_methods_supported": ["header"],
            "scopes_supported": [SCOPE],
        }
    )


def _redirects(body: dict[str, Any]) -> list[str]:
    raw = body.get("redirect_uris")
    if not isinstance(raw, list) or not raw or len(raw) > MAX_REDIRECTS:
        raise GrantError("invalid_redirect_uri")
    found: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not redirect_allowed(item):
            raise GrantError("invalid_redirect_uri")
        found.append(item)
    return found


def _register_body(body: dict[str, Any]) -> list[str]:
    if "client_secret" in body:
        raise GrantError("invalid_client_metadata")
    method = body.get("token_endpoint_auth_method")
    if method != "none":
        raise GrantError("invalid_client_metadata")
    grants = body.get("grant_types", ["authorization_code", "refresh_token"])
    if not isinstance(grants, list) or any(item not in _GRANTS for item in grants):
        raise GrantError("invalid_client_metadata")
    responses = body.get("response_types", ["code"])
    if responses != ["code"]:
        raise GrantError("invalid_client_metadata")
    return _redirects(body)


async def register(request: Request) -> Response:
    from .server import _svc

    db = _svc().db
    ip = _ip(request)
    wait = retry_after(db, "oauth_register", ip, limit=REGISTER_LIMIT, window_s=REGISTER_WINDOW_S)
    if wait:
        return _limited(wait)
    note_failure(db, "oauth_register", ip)
    if clients_today(db) >= REGISTER_DAY_CAP:
        return _limited(3600)
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return _oauth_error("invalid_client_metadata")
    if not isinstance(body, dict):
        return _oauth_error("invalid_client_metadata")
    try:
        redirects = _register_body(body)
    except GrantError as exc:
        return _oauth_error(exc.error)
    client_id = register_client(db, redirects)
    return _json(
        {
            "client_id": client_id,
            "redirect_uris": redirects,
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
        201,
    )


def _form_value(form: Any, name: str) -> str:
    value = form.get(name) if hasattr(form, "get") else None
    return value if isinstance(value, str) else ""


async def token(request: Request) -> Response:
    from .server import _svc

    db = _svc().db
    wait = retry_after(db, "oauth_token", _ip(request))
    if wait:
        return _limited(wait)
    form = await request.form()
    if _form_value(form, "client_secret"):
        return _oauth_error("invalid_client")
    client_id = _form_value(form, "client_id")
    if client_row(db, client_id) is None:
        note_failure(db, "oauth_token", _ip(request))
        return _oauth_error("invalid_client")
    grant = _form_value(form, "grant_type")
    try:
        if grant == "authorization_code":
            issued = exchange_code(
                db,
                code=_form_value(form, "code"),
                verifier=_form_value(form, "code_verifier"),
                client_id=client_id,
                redirect_uri=_form_value(form, "redirect_uri"),
                resource=_form_value(form, "resource"),
            )
        elif grant == "refresh_token":
            issued = refresh_grant(db, raw=_form_value(form, "refresh_token"), client_id=client_id)
        else:
            raise GrantError("unsupported_grant_type")
    except GrantError as exc:
        note_failure(db, "oauth_token", _ip(request))
        return _oauth_error(exc.error)
    return _json(
        {
            "access_token": issued["access_token"],
            "token_type": "Bearer",
            "expires_in": issued["expires_in"],
            "refresh_token": issued["refresh_token"],
            "scope": issued["scope"],
        }
    )


async def revoke(request: Request) -> Response:
    from .server import _svc

    form = await request.form()
    revoke_token(_svc().db, _form_value(form, "token"))
    return _json({})


def _ip(request: Request) -> str:
    from .web.client_addr import client_ip

    return client_ip(request)


def _limited(wait: int) -> JSONResponse:
    response = _oauth_error("slow_down", 429)
    response.headers["Retry-After"] = str(wait)
    return response
