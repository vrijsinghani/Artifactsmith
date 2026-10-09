"""MCP API (port 8780, bearer auth) + a separate cookieless preview origin (port 8781)."""
from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

import uvicorn
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from .config import CFG
from .service import AMError, Service, unsign

SVC: Service | None = None

INSTRUCTIONS = (
    "Artifact service. create/edit queue a background build and return at once (artifact_id, version, job_id); "
    "call status with wait (<=90s) until done, failed or needs_input, then present the card (title, version, "
    "preview_url, assumptions). The user's exact words are the whole scope: pass them verbatim; pass researched "
    "content separately in source_content (or source_files). A build that lacks a needed fact ends as needs_input "
    "with a short missing message and stores no page. create never publishes. share publishes a public link for one "
    "exact version (lifetime AM_SHARE_TTL_DAYS, 0 = until revoked); unshare revokes immediately. A token reaches only "
    "its own workspace and only the permissions it was granted."
)

mcp = FastMCP(
    "artifacts",
    instructions=INSTRUCTIONS,
    stateless_http=True,
    json_response=True,
    streamable_http_path="/mcp",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def _principal(ctx: Context) -> dict:
    req = ctx.request_context.request
    principal = req.scope.get("am_principal") if req is not None else None
    if not principal:
        raise AMError("unauthenticated")
    return principal


def _run(fn, *a, **k) -> dict:
    try:
        return fn(*a, **k)
    except AMError as e:
        return {"error": str(e)}


@mcp.tool()
def create(ctx: Context, slug: str, display_name: str, kind: str, verbatim_request: str,
           source_content: str | None = None, source_files: list[dict[str, str]] | None = None,
           format: str | None = None, workspace: str | None = None, model: str | None = None,
           capabilities: dict[str, Any] | None = None, idempotency_key: str | None = None) -> dict:
    """Start building a new artifact (kind=web_static: one self-contained HTML page). Returns artifact_id, version,
    job_id at once; then call status(job_id, wait=90).

    - verbatim_request: the user's exact words; the entire scope. Put no research/data here beyond what the user said.
    - source_content (optional, up to 200 KB total with source_files): researched content/data the page is built from.
      The builder uses ONLY the request plus this material for facts.
    - source_files (optional): list of {"name": str, "content": str}, same purpose and shared 200 KB cap.
    - If facts the request needs are missing, the build ends with status "needs_input" and a short "missing" message
      (no placeholder page). Supply the data and call create again with the same slug.
    - create never creates a share link."""
    return _run(SVC.create, _principal(ctx), slug=slug, display_name=display_name, kind=kind,
                verbatim_request=verbatim_request, format=format, workspace=workspace, model=model,
                source_content=source_content, source_files=source_files, capabilities=capabilities,
                idempotency_key=idempotency_key)


@mcp.tool()
def edit(ctx: Context, artifact_id: str, base_version: int, verbatim_request: str,
         source_content: str | None = None, source_files: list[dict[str, str]] | None = None,
         workspace: str | None = None, model: str | None = None, idempotency_key: str | None = None) -> dict:
    """Build a new version from base_version (the latest done version) using the user's exact change request.
    Refused with a conflict if base_version is stale — inspect, then edit from the latest.
    - source_content / source_files (optional, 200 KB total): new source material for this version; replaces the base
      version's source. If omitted, the base version's source is reused.
    - Ends as "needs_input" with a "missing" message if the change needs facts that were not supplied."""
    return _run(SVC.edit, _principal(ctx), artifact_id=artifact_id, base_version=base_version,
                verbatim_request=verbatim_request, source_content=source_content, source_files=source_files,
                workspace=workspace, model=model, idempotency_key=idempotency_key)


@mcp.tool()
async def status(ctx: Context, artifact_id: str | None = None, job_id: str | None = None, wait: int = 0) -> dict:
    """Build state (queued/building/done/failed/needs_input; needs_input carries a "missing" message), progress and the
    presentation card. wait (0-90s) holds the call open until the build finishes."""
    try:
        return await SVC.status(_principal(ctx), artifact_id=artifact_id, job_id=job_id, wait=wait)
    except AMError as e:
        return {"error": str(e)}


@mcp.tool()
def list_artifacts(ctx: Context, workspace: str | None = None, kind: str | None = None,
                   query: str | None = None, limit: int = 50) -> dict:
    """Catalog of artifacts in your workspace, newest first. Optional kind filter and text query on slug/title."""
    return _run(SVC.list, _principal(ctx), workspace=workspace, kind=kind, query=query, limit=limit)


@mcp.tool()
def inspect(ctx: Context, artifact_id: str, version: int | None = None) -> dict:
    """Manifest, files, build log, request history and share state (latest done version by default)."""
    return _run(SVC.inspect, _principal(ctx), artifact_id, version)


@mcp.tool()
def export(ctx: Context, artifact_id: str, version: int | None = None) -> dict:
    """Short-lived (15 min) download link for the self-contained .html."""
    return _run(SVC.export, _principal(ctx), artifact_id, version)


@mcp.tool()
def share(ctx: Context, artifact_id: str, version: int | None = None, ttl_days: int | None = None) -> dict:
    """Publish a PUBLIC link for one exact version (default: latest done version), live immediately. create does not
    do this; anyone with the link can open it. Lifetime follows AM_SHARE_TTL_DAYS (0 = until revoked; otherwise
    capped by AM_SHARE_TTL_MAX_DAYS). Links never follow later versions. unshare revokes at once."""
    return _run(SVC.share, _principal(ctx), artifact_id, version, ttl_days)


@mcp.tool()
def unshare(ctx: Context, artifact_id: str, version: int | None = None) -> dict:
    """Revoke share links for an artifact (or one version). Takes effect immediately."""
    return _run(SVC.unshare, _principal(ctx), artifact_id, version)


@mcp.tool()
def delete(ctx: Context, artifact_id: str, confirm_token: str | None = None) -> dict:
    """Permanent two-step delete. First call returns a confirm_token; the second call with it purges every stored
    version and all links. Warn the user before the second call."""
    return _run(SVC.delete, _principal(ctx), artifact_id, confirm_token)


# ---------------- bearer auth + per-token permissions for the API origin ----------------
class BearerAuth:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in ("/healthz",):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        principal = None
        if auth.lower().startswith("bearer "):
            h = hashlib.sha256(auth[7:].strip().encode()).hexdigest()
            row = SVC.db.one("SELECT * FROM tokens WHERE token_hash=? AND revoked_at IS NULL", h)
            if row:
                principal = {"id": row["id"], "name": row["name"], "workspace": row["workspace"],
                             "perms": set(json.loads(row["perms"]))}
        if not principal:
            resp = JSONResponse({"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
            return await resp(scope, receive, send)
        scope["am_principal"] = principal
        return await self.app(scope, receive, send)


async def healthz(request: Request):
    return JSONResponse({"ok": True, "service": "artifactsmith", "version": "0.1.0"})


mcp.custom_route("/healthz", methods=["GET"])(healthz)


# ---------------- preview origin (separate port = separate origin; no cookies, strict CSP) ----------------
CSP = ("default-src 'none'; img-src data:; media-src data:; style-src 'unsafe-inline'; script-src 'none'; "
       "font-src data:; connect-src 'none'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'; sandbox")


def _serve(data: bytes, ctype: str, fname: str, attachment: bool) -> Response:
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
               "Content-Security-Policy": CSP, "Cross-Origin-Resource-Policy": "same-origin"}
    if attachment:
        headers["Content-Disposition"] = f'attachment; filename="{fname}"'
    return Response(data, media_type=ctype, headers=headers)


async def preview(request: Request):
    tok = request.path_params["token"]
    sl = SVC.short_lookup(tok)
    p = {"a": sl["artifact_id"], "v": sl["version"]} if sl else None
    if not p:
        return PlainTextResponse("link expired or invalid", status_code=404, headers={"Cache-Control": "no-store"})
    try:
        data, ct, name = await asyncio.to_thread(SVC.read_file, p["a"], int(p["v"]), None)
    except AMError:
        return PlainTextResponse("not found", status_code=404, headers={"Cache-Control": "no-store"})
    return _serve(data, ct, name, attachment=False)


async def download(request: Request):
    p = unsign(request.path_params["token"])
    if not p or "f" not in p:
        return PlainTextResponse("link expired or invalid", status_code=404, headers={"Cache-Control": "no-store"})
    try:
        data, ct, _ = await asyncio.to_thread(SVC.read_file, p["a"], int(p["v"]), p["f"])
    except AMError:
        return PlainTextResponse("not found", status_code=404, headers={"Cache-Control": "no-store"})
    return _serve(data, ct, p.get("n") or p["f"], attachment=True)


async def shared(request: Request):
    s = SVC.share_lookup(request.path_params["sid"])
    if not s:
        return PlainTextResponse("not found", status_code=404, headers={"Cache-Control": "no-store"})
    try:
        data, ct, name = await asyncio.to_thread(SVC.read_file, s["artifact_id"], s["version"], None)
    except AMError:
        return PlainTextResponse("not found", status_code=404, headers={"Cache-Control": "no-store"})
    if hashlib.sha256(data).hexdigest() != s["sha256"]:
        return PlainTextResponse("not found", status_code=404, headers={"Cache-Control": "no-store"})
    return _serve(data, ct, name, attachment=False)


preview_app = Starlette(routes=[
    Route("/p/{token}", preview), Route("/p/{token}/", preview),
    Route("/dl/{token}", download),
    Route("/s/{sid}", shared), Route("/s/{sid}/", shared),
    Route("/healthz", healthz),
])


async def main() -> None:
    global SVC
    SVC = Service()
    SVC.store.ensure_bucket()
    api_app = BearerAuth(mcp.streamable_http_app())
    api = uvicorn.Server(uvicorn.Config(api_app, host=CFG.host, port=CFG.api_port, log_level="info",
                                        proxy_headers=False, server_header=False))
    prev = uvicorn.Server(uvicorn.Config(preview_app, host=CFG.host, port=CFG.preview_port, log_level="warning",
                                         proxy_headers=False, server_header=False))

    async def start_after_boot():
        await asyncio.sleep(0.5)
        SVC.start_workers()

    await asyncio.gather(api.serve(), prev.serve(), start_after_boot())


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
