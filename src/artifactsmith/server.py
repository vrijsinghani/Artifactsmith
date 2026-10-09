"""MCP API (port 8780, bearer auth) + a separate cookieless preview origin (port 8781)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import uvicorn
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from . import __version__
from .config import CFG
from .service import AMError, Service, unsign

SVC: Service | None = None

INSTRUCTIONS = (
    "Artifact service. create and edit queue a background build and return artifact_id, version, and job_id "
    "immediately. Call status with wait (up to 90 seconds) until the job is done, failed, or needs_input, then "
    "show the card (title, version, preview_url, assumptions). Pass the user's exact words in verbatim_request. "
    "Pass researched content in source_content or source_files. If a needed fact is missing, the job ends as "
    "needs_input with a short missing message and stores no file. create does not publish a share link. share "
    "publishes a public link for one exact version (lifetime AM_SHARE_TTL_DAYS; 0 means until revoked). unshare "
    "revokes immediately. A token reaches only its own workspace and only the permissions it was granted."
)


def _transport_security() -> TransportSecuritySettings:
    hosts = list(CFG.allowed_hosts)
    origins = list(CFG.allowed_origins)
    enabled = bool(hosts or origins)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=enabled,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


mcp = FastMCP(
    "artifacts",
    instructions=INSTRUCTIONS,
    stateless_http=True,
    json_response=True,
    streamable_http_path="/mcp",
    transport_security=_transport_security(),
)


def _svc() -> Service:
    if SVC is None:
        raise AMError("server is not ready")
    return SVC


def _principal(ctx: Context[Any, Any, Any]) -> dict[str, Any]:
    req = ctx.request_context.request
    principal = req.scope.get("am_principal") if req is not None else None
    if not isinstance(principal, dict):
        raise AMError("unauthenticated")
    return principal


def _run(fn: Callable[..., dict[str, Any]], *a: Any, **k: Any) -> dict[str, Any]:
    try:
        return fn(*a, **k)
    except AMError as e:
        return {"error": str(e)}


@mcp.tool()
def create(
    ctx: Context[Any, Any, Any],
    verbatim_request: str,
    display_name: str | None = None,
    slug: str | None = None,
    kind: str = "web_static",
    source_content: str | None = None,
    source_files: list[dict[str, str]] | None = None,
    format: str | None = None,
    style: str | None = None,
    workspace: str | None = None,
    model: str | None = None,
    capabilities: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Queue a new artifact. Returns artifact_id, version, and job_id immediately.
    Then call status(job_id, wait=90).

    Required: verbatim_request (the user's exact words; the whole scope).

    display_name is the human title. slug is the URL key (a-z, 0-9, hyphens). Provide at least one:
    if slug is omitted it is derived from display_name; if display_name is omitted it defaults to slug.
    kind defaults to web_static (the only kind in this release).

    format is html (default), markdown, pdf, docx, or xlsx. The model writes content. A fixed renderer
    writes the bytes. html uses the visual style (house, bold, editorial, playful, terminal, swiss;
    default AM_DEFAULT_STYLE, usually house). style is accepted on markdown, pdf, docx, and xlsx
    but ignored: those formats still use the writing-only prompt.

    source_content (optional, up to 200 KB total with source_files) is researched material the page
    is built from. The builder uses only the request plus this material for facts.

    source_files (optional) is a list of {name, content} objects under the same 200 KB cap.

    If a needed fact is missing, the job ends as needs_input with a short missing message and stores
    no file. Call create again with the same slug after you supply the data.

    create does not create a share link. When checks fail, the server retries the model once, so a
    failed build takes about twice as long to report as a clean one."""
    return _run(
        _svc().create,
        _principal(ctx),
        slug=slug,
        display_name=display_name,
        kind=kind,
        verbatim_request=verbatim_request,
        format=format,
        workspace=workspace,
        model=model,
        source_content=source_content,
        source_files=source_files,
        capabilities=capabilities,
        idempotency_key=idempotency_key,
        style=style,
    )


@mcp.tool()
def edit(
    ctx: Context[Any, Any, Any],
    artifact_id: str,
    base_version: int,
    verbatim_request: str,
    source_content: str | None = None,
    source_files: list[dict[str, str]] | None = None,
    workspace: str | None = None,
    model: str | None = None,
    style: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Build a new version from base_version (the latest done version) using the user's exact change request.
    Returns a conflict if base_version is stale. Inspect, then edit from the latest.

    source_content and source_files (optional, 200 KB total) replace the base version's source.
    If omitted, the base version's source is reused.

    style (optional) is house, bold, editorial, playful, terminal, or swiss. Omit it to keep the
    style the artifact was last built with. It applies to HTML only; other formats accept it and
    ignore it.

    Ends as needs_input with a missing message if the change needs facts that were not supplied."""
    return _run(
        _svc().edit,
        _principal(ctx),
        artifact_id=artifact_id,
        base_version=base_version,
        verbatim_request=verbatim_request,
        source_content=source_content,
        source_files=source_files,
        workspace=workspace,
        model=model,
        idempotency_key=idempotency_key,
        style=style,
    )


@mcp.tool()
async def status(
    ctx: Context[Any, Any, Any], artifact_id: str | None = None, job_id: str | None = None, wait: int = 0
) -> dict[str, Any]:
    """Build state (queued, building, done, failed, or needs_input). needs_input includes a missing message.
    Also returns progress and the presentation card. wait (0 to 90 seconds) holds the call open until the
    build finishes."""
    try:
        return await _svc().status(_principal(ctx), artifact_id=artifact_id, job_id=job_id, wait=wait)
    except AMError as e:
        return {"error": str(e)}


@mcp.tool()
def list_artifacts(
    ctx: Context[Any, Any, Any],
    workspace: str | None = None,
    kind: str | None = None,
    query: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Catalog of artifacts in your workspace, newest first. Optional kind filter and text query on slug or title."""
    return _run(_svc().list, _principal(ctx), workspace=workspace, kind=kind, query=query, limit=limit)


@mcp.tool()
def inspect(ctx: Context[Any, Any, Any], artifact_id: str, version: int | None = None) -> dict[str, Any]:
    """Manifest, files, build log, request history, and share state. Defaults to the latest done version."""
    return _run(_svc().inspect, _principal(ctx), artifact_id, version)


@mcp.tool()
def export(ctx: Context[Any, Any, Any], artifact_id: str, version: int | None = None) -> dict[str, Any]:
    """Download link for the rendered file (html, md, pdf, docx, or xlsx). Valid for 15 minutes."""
    return _run(_svc().export, _principal(ctx), artifact_id, version)


@mcp.tool()
def share(
    ctx: Context[Any, Any, Any], artifact_id: str, version: int | None = None, ttl_days: int | None = None
) -> dict[str, Any]:
    """Publish a public link for one exact version (default: the latest done version). Anyone with the link
    can open it. Lifetime follows AM_SHARE_TTL_DAYS (0 means until revoked; otherwise capped by
    AM_SHARE_TTL_MAX_DAYS). The link stays on that version. unshare revokes it immediately."""
    return _run(_svc().share, _principal(ctx), artifact_id, version, ttl_days)


@mcp.tool()
def unshare(ctx: Context[Any, Any, Any], artifact_id: str, version: int | None = None) -> dict[str, Any]:
    """Revoke share links for an artifact, or for one version. Takes effect immediately."""
    return _run(_svc().unshare, _principal(ctx), artifact_id, version)


@mcp.tool()
def revoke_previews(ctx: Context[Any, Any, Any], artifact_id: str, version: int | None = None) -> dict[str, Any]:
    """Expire private /p/ preview links for an artifact (or one version). Token revocation and signing-key
    rotation do not expire these database-backed capabilities; call this explicitly."""
    return _run(_svc().revoke_previews, _principal(ctx), artifact_id, version)


@mcp.tool()
def delete(ctx: Context[Any, Any, Any], artifact_id: str, confirm_token: str | None = None) -> dict[str, Any]:
    """Two-step delete. The first call returns a confirm_token. The second call with that token purges every
    stored version and all links. Warn the user before the second call."""
    return _run(_svc().delete, _principal(ctx), artifact_id, confirm_token)


# ---------------- bearer auth + per-token permissions for the API origin ----------------
class BearerAuth:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in ("/healthz",):
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        principal = None
        if auth.lower().startswith("bearer "):
            h = hashlib.sha256(auth[7:].strip().encode()).hexdigest()
            row = _svc().db.one("SELECT * FROM tokens WHERE token_hash=? AND revoked_at IS NULL", h)
            if row:
                principal = {
                    "id": row["id"],
                    "name": row["name"],
                    "workspace": row["workspace"],
                    "perms": set(json.loads(row["perms"])),
                }
        if not principal:
            resp = JSONResponse({"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
            await resp(scope, receive, send)
            return
        scope.update({"am_principal": principal})
        await self.app(scope, receive, send)
        return


async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "service": "artifactsmith", "version": __version__})


mcp.custom_route("/healthz", methods=["GET"])(healthz)


# ---------------- preview origin (separate port = separate origin; no cookies, strict CSP) ----------------
CSP = (
    "default-src 'none'; img-src data:; media-src data:; style-src 'unsafe-inline'; script-src 'none'; "
    "font-src data:; connect-src 'none'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'; sandbox"
)


def _serve(data: bytes, ctype: str, fname: str, attachment: bool) -> Response:
    headers = {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": CSP,
        "Cross-Origin-Resource-Policy": "same-origin",
    }
    if attachment:
        headers["Content-Disposition"] = f'attachment; filename="{fname}"'
    return Response(data, media_type=ctype, headers=headers)


async def preview(request: Request) -> Response:
    tok = request.path_params["token"]
    sl = _svc().short_lookup(tok)
    p = {"a": sl["artifact_id"], "v": sl["version"]} if sl else None
    if not p:
        return PlainTextResponse("link expired or invalid", status_code=404, headers={"Cache-Control": "no-store"})
    try:
        data, ct, name = await asyncio.to_thread(_svc().read_file, p["a"], int(p["v"]), None)
    except AMError:
        return PlainTextResponse("not found", status_code=404, headers={"Cache-Control": "no-store"})
    return _serve(data, ct, name, attachment=False)


async def download(request: Request) -> Response:
    p = unsign(request.path_params["token"])
    if not p or "f" not in p:
        return PlainTextResponse("link expired or invalid", status_code=404, headers={"Cache-Control": "no-store"})
    try:
        data, ct, _ = await asyncio.to_thread(_svc().read_file, p["a"], int(p["v"]), p["f"])
    except AMError:
        return PlainTextResponse("not found", status_code=404, headers={"Cache-Control": "no-store"})
    return _serve(data, ct, p.get("n") or p["f"], attachment=True)


async def shared(request: Request) -> Response:
    s = _svc().share_lookup(request.path_params["sid"])
    if not s:
        return PlainTextResponse("not found", status_code=404, headers={"Cache-Control": "no-store"})
    try:
        data, ct, name = await asyncio.to_thread(_svc().read_file, s["artifact_id"], s["version"], None)
    except AMError:
        return PlainTextResponse("not found", status_code=404, headers={"Cache-Control": "no-store"})
    if hashlib.sha256(data).hexdigest() != s["sha256"]:
        return PlainTextResponse("not found", status_code=404, headers={"Cache-Control": "no-store"})
    return _serve(data, ct, name, attachment=False)


preview_app = Starlette(
    routes=[
        Route("/p/{token}", preview),
        Route("/p/{token}/", preview),
        Route("/dl/{token}", download),
        Route("/s/{sid}", shared),
        Route("/s/{sid}/", shared),
        Route("/healthz", healthz),
    ]
)


def missing_openai_key_warning(base: str, key: str) -> bool:
    """True when the configured LLM host is api.openai.com and no key is set."""
    host = (urlparse(base).hostname or "").lower()
    return (not key) and host == "api.openai.com"


async def main() -> None:
    global SVC
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("artifactsmith")
    if missing_openai_key_warning(CFG.normalized_llm_base(), CFG.llm_key()):
        log.warning(
            "OPENAI_API_KEY / AM_LLM_KEY is empty while AM_LLM_BASE points at api.openai.com; "
            "create will fail with HTTP 401 until you set a key or point AM_LLM_BASE at a local gateway"
        )
    svc = Service()
    SVC = svc
    svc.store.ensure_bucket()
    # Bind the loop before accepting connections so early MCP enqueues are thread-safe.
    svc.bind_loop(asyncio.get_running_loop())
    api_app = BearerAuth(mcp.streamable_http_app())
    api = uvicorn.Server(
        uvicorn.Config(
            api_app, host=CFG.host, port=CFG.api_port, log_level="info", proxy_headers=False, server_header=False
        )
    )
    prev = uvicorn.Server(
        uvicorn.Config(
            preview_app,
            host=CFG.host,
            port=CFG.preview_port,
            log_level="warning",
            proxy_headers=False,
            server_header=False,
        )
    )

    async def start_after_boot() -> None:
        await asyncio.sleep(0.5)
        svc.start_workers()

    try:
        await asyncio.gather(api.serve(), prev.serve(), start_after_boot())
    finally:
        await svc.shutdown()
        SVC = None


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
