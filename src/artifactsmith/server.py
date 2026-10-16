"""MCP API (port 8780, bearer auth) + a separate cookieless preview origin (port 8781)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from typing import Annotated, Any
from urllib.parse import urlparse

import uvicorn
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from . import __version__
from .config import CFG
from .markdown_view import MarkdownViewError, is_markdown_primary, markdown_share_html
from .renderers import MIME, SOURCE_NAME
from .service import AMError, Service, page_title, stamp_share_title, unsign
from .web.routes import build_pages
from .web.trial_pages import _CARD_PATH

SVC: Service | None = None

INSTRUCTIONS = (
    "Artifact service. Want a styled page? Pass Markdown to publish and it is styled automatically "
    "(omit format when you pass markdown and no html; style defaults to house and is applied when the "
    "page is opened). You can still pass format=markdown. Or call create with source_content. Raw HTML "
    "in publish is only for a page you designed yourself: it is shown exactly as given, with no "
    "ArtifactSmith styling. Pass format for pdf, docx, or xlsx. "
    "create and edit queue a background build and return artifact_id, version, and job_id immediately. "
    "publish skips the model and runs the same safety checks. An optional note is stored and shown by inspect. "
    "include_content on inspect reads that version's text back. Call status with wait (up to 90 seconds) "
    "until the job is done, failed, or needs_input, then show the card (title, version, preview_url, "
    "assumptions). Pass the user's exact words in verbatim_request. Pass researched content in "
    "source_content or source_files. If a needed fact is missing, the job ends as needs_input with a short "
    "missing message and stores no file. create and publish do not publish a share link. share publishes a "
    "public link for one exact version (lifetime AM_SHARE_TTL_DAYS; 0 means until revoked). unshare revokes "
    "immediately. A token reaches only its own workspace and only the permissions it was granted."
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


_list_tools = mcp.list_tools


async def _list_tools_for_caller() -> list[Any]:
    """Trial tokens do not see publish. Other callers see every tool."""
    tools = await _list_tools()
    try:
        request = mcp._mcp_server.request_context.request
    except LookupError:
        return tools
    principal = getattr(request, "scope", {}).get("am_principal") if request is not None else None
    if isinstance(principal, dict) and principal.get("trial"):
        return [tool for tool in tools if tool.name != "publish"]
    return tools


mcp.list_tools = _list_tools_for_caller  # type: ignore[method-assign]
mcp._mcp_server.list_tools()(_list_tools_for_caller)  # type: ignore[no-untyped-call]


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
    display_name: Annotated[
        str | None,
        Field(
            description=(
                "Human title shown on cards and in the catalog. Optional if slug is set "
                "(then it defaults to the slug). At most 200 characters. "
                'Example: "Pilot store brief". Provide display_name or slug (at least one).'
            )
        ),
    ] = None,
    slug: Annotated[
        str | None,
        Field(
            description=(
                "URL key unique in the workspace. Optional if display_name is set "
                "(then derived from the title: lowercase, accents folded, other characters become hyphens). "
                "2-63 characters of a-z, 0-9, and hyphen; must start with a letter or digit. "
                'Example: "pilot-store-brief". Provide display_name or slug (at least one).'
            )
        ),
    ] = None,
    kind: str = "web_static",
    source_content: str | None = None,
    source_files: list[dict[str, str]] | None = None,
    format: str | None = None,
    style: str | None = None,
    mode: Annotated[
        str | None,
        Field(
            description=(
                "Build mode for a first HTML page: fast or full. "
                "fast writes Markdown and the server renders the style. "
                "full is the HTML build. Default is AM_BUILD_MODE (full), "
                "or fast when the style is listed in AM_FAST_STYLES. "
                "A complete HTML source, or a request to publish that HTML as-is, stays full when mode is omitted. "
                "Mentioning HTML next to unchanged does not. "
                "Other formats ignore fast."
            )
        ),
    ] = None,
    workspace: str | None = None,
    model: str | None = None,
    capabilities: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Queue a new artifact. Returns artifact_id, version, and job_id immediately.
    Then call status(job_id, wait=90).

    Want a styled page from text you already wrote? Publish that Markdown. It is styled automatically
    when format is omitted and no html is passed (format=markdown; style defaults to house).
    Want the server to write the page? Pass source_content here.
    Raw HTML in publish is shown exactly as given, with no ArtifactSmith styling.

    Required: verbatim_request (the user's exact words; the whole scope).

    display_name is the human title on cards (optional if slug is set; defaults to the slug;
    at most 200 characters; example "Pilot store brief"). slug is the workspace URL key
    (optional if display_name is set; otherwise derived from the title; 2-63 chars of a-z, 0-9,
    and hyphen, starting with a letter or digit; example "pilot-store-brief"). Provide at least one.
    kind defaults to web_static (the only kind in this release).

    format is html (default), markdown, pdf, docx, or xlsx. The model writes content. A fixed renderer
    writes the bytes. html uses the visual style (house, bold, editorial, playful, terminal, swiss;
    default AM_DEFAULT_STYLE, usually house). On markdown, pdf, docx, and xlsx the model prompt does
    not use that visual style. A markdown page is still shown in the recorded style when it is opened.

    mode is fast or full (default AM_BUILD_MODE, usually full). When mode is omitted and the
    style is listed in AM_FAST_STYLES, the default is fast instead. fast applies to a first HTML
    build: the model writes Markdown and the server renders it. full is the HTML build.
    Other formats ignore fast. An explicit mode still wins. A complete HTML source, or a request
    to publish that HTML as-is, stays full when mode is omitted. Mentioning HTML beside "unchanged" does not.

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
        mode=mode,
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
    format: str | None = None,
    display_name: str | None = None,
) -> dict[str, Any]:
    """Build a new version from base_version (the latest done version) using the user's exact change request.
    Returns a conflict if base_version is stale. Inspect, then edit from the latest.

    source_content and source_files (optional, 200 KB total) replace the base version's source.
    If omitted, the base version's source is reused.

    style (optional) is house, bold, editorial, playful, terminal, or swiss. Omit it to keep the
    style the artifact was last built with. It applies to HTML only; other formats accept it and
    ignore it.

    format (optional) is html, markdown, pdf, docx, or xlsx. Omit it to keep the artifact's current
    format. A different format builds this version in that format. When the version is done, the
    artifact's format updates. The artifact id and slug stay the same. Older versions keep the format
    they were built with.

    display_name (optional) is the card title. When this version finishes, that name replaces the
    artifact's display_name. Omit it and a changed page title (the HTML title, or the first Markdown
    heading) becomes the card title instead. The slug stays the same.

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
        format=format,
        display_name=display_name,
    )


@mcp.tool()
def publish(
    ctx: Context[Any, Any, Any],
    html: str | None = None,
    markdown: str | None = None,
    display_name: str | None = None,
    slug: str | None = None,
    artifact_id: str | None = None,
    base_version: int | None = None,
    format: str | None = None,
    summary: str | None = None,
    assumptions: list[str] | None = None,
    workspace: str | None = None,
    style: str | None = None,
    idempotency_key: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Store a page you have already finished. Use this when you already have the finished page.
    No model call. Returns artifact_id, version, and job_id immediately. Then call status(job_id, wait=90).
    When the job is done, status and inspect list safety_changes for each safety edit that changed the page.
    HTML publish does not turn bare URLs into links. The document title is stored as given and escaped in the page.
    note is optional. Inspect shows a non-empty note beside "Published a finished page." It is not the
    text a later edit sends to the model. A secret, private link, or over-long note is rejected before a row exists.

    Want a styled page? Pass Markdown and it is styled automatically. Omit format when you pass
    markdown and no html. style is house, bold, editorial, playful, terminal, or swiss (default house)
    and is applied when the page is opened. The stored file stays Markdown. You can still pass
    format=markdown. On a new artifact, if you also pass html, html is the page unless you set format.
    Raw HTML is shown
    exactly as given, with no ArtifactSmith styling. If that HTML has little or no CSS, the result
    includes warning. Pass format for pdf, docx, or xlsx.

    html is the complete HTML document when format is html. markdown is the finished Markdown when
    format is markdown, pdf, docx, or xlsx. You may pass the other field as well: it is checked for
    secrets and private links and is not rendered. PDF, DOCX, and XLSX do not use the HTML style sheet.

    New artifact: pass display_name or slug (same rules as create). New version of an existing artifact:
    pass artifact_id and base_version (the latest done version). Omit format on an html or markdown
    artifact and markdown with no html is markdown; html with no markdown is html; both fields keep
    the artifact's format. A pdf, docx, or xlsx artifact keeps that format when format is omitted and
    the new page is markdown. Pass format to choose html, markdown, pdf, docx, or xlsx. Pass display_name
    to rename the card when this version finishes. Omit display_name and a changed page title (the HTML
    title, or the first Markdown heading) becomes the card title. The artifact id and slug stay the same.
    Older versions keep the format they were published with.

    The page runs the same safety pipeline as model output: size caps, secret scan, private-link checks,
    the sanitizer, the renderer for format (including PDF, DOCX, and XLSX), and the final sweep. A rejected
    page ends as failed, and error names the check that failed. publish does not create a share link.
    It counts toward the per-token build quota and requires the publish permission.

    summary and assumptions are optional card fields. Each is rejected when the call arrives if it is
    longer than the metadata cap (the same 'metadata exceeds N characters' error the job would report).
    They are also scanned like model output. On markdown, style changes the opened page. On HTML, style
    is recorded and the submitted page is not restyled."""
    return _run(
        _svc().publish,
        _principal(ctx),
        html=html,
        markdown=markdown,
        slug=slug,
        display_name=display_name,
        artifact_id=artifact_id,
        base_version=base_version,
        format=format,
        summary=summary,
        assumptions=assumptions,
        workspace=workspace,
        idempotency_key=idempotency_key,
        style=style,
        note=note,
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
def inspect(
    ctx: Context[Any, Any, Any],
    artifact_id: str,
    version: int | None = None,
    include_content: bool = False,
) -> dict[str, Any]:
    """Manifest, files, build log, request history, and share state. Defaults to the latest done version.

    include_content is optional and defaults to false. When true, the target done version adds
    content with file, size_bytes, and text when that text is within the publish character cap.
    PDF, DOCX, and XLSX return content.md and set primary_binary; they do not return the binary.
    Over the cap, size_bytes is set and text is omitted. A store failure sets content_error to
    store_read_failed. The call still requires read permission on this workspace.
    """
    return _run(_svc().inspect, _principal(ctx), artifact_id, version, include_content)


@mcp.tool()
def export(
    ctx: Context[Any, Any, Any],
    artifact_id: Annotated[str, Field(description="Artifact to download. Required.")],
    version: Annotated[
        int | None, Field(description="Done version to download. Optional; defaults to the latest done version.")
    ] = None,
) -> dict[str, Any]:
    """Download link for one rendered file (html, md, pdf, docx, or xlsx).

    Permission: export.
    Arguments: artifact_id (required), version (optional; latest done version).
    Returns: download_url (valid 15 minutes), file, version, sha256, self_contained, expires_in.
    Example: export(artifact_id="art_example", version=1)"""
    return _run(_svc().export, _principal(ctx), artifact_id, version)


@mcp.tool()
def share(
    ctx: Context[Any, Any, Any],
    artifact_id: Annotated[str, Field(description="Artifact to publish. Required.")],
    version: Annotated[
        int | None,
        Field(
            description=(
                "Done version to pin. Optional. Omit it to follow the latest done version. "
                "Passing a version freezes that version on its own link."
            )
        ),
    ] = None,
    ttl_days: Annotated[
        int | None,
        Field(
            description=(
                "Share lifetime in days. Optional. Defaults to AM_SHARE_TTL_DAYS. "
                "Ignored when AM_SHARE_TTL_DAYS is 0 (the link lasts until revoked). "
                "Otherwise capped by AM_SHARE_TTL_MAX_DAYS."
            )
        ),
    ] = None,
    pin: Annotated[
        bool,
        Field(
            description=(
                "Freeze one version. Optional. True with no version pins the latest done version now. "
                "False with no version follows later versions."
            )
        ),
    ] = False,
) -> dict[str, Any]:
    """Publish a public link. Anyone with the link can open it.

    Permission: share.
    Arguments: artifact_id (required), version (optional; passing it pins that version), ttl_days (optional; ignored when AM_SHARE_TTL_DAYS is 0), pin (optional).
    Returns: state, url, version, follows ("latest" or "pinned"), expires_at, lifetime, reused.
    Omit version to keep serving the latest done version on the same URL.
    Example: share(artifact_id="art_example")
    unshare revokes it immediately."""
    return _run(_svc().share, _principal(ctx), artifact_id, version, ttl_days, pin)


@mcp.tool()
def unshare(
    ctx: Context[Any, Any, Any],
    artifact_id: Annotated[str, Field(description="Artifact whose public links should be revoked. Required.")],
    version: Annotated[
        int | None,
        Field(
            description=(
                "Revoke only this version. Optional. Omit it, or pass 0, to revoke every version of the artifact."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """Revoke public share links. Takes effect immediately; the old URL returns 404.

    Permission: share.
    Arguments: artifact_id (required), version (optional). A version revokes only pinned links for that version. Omit it, or pass 0, to revoke every public link, including the one that follows the latest version.
    Returns: artifact_id, revoked_links (count), and state. state is "shared" when a public link is still live (with url and follows), and "not_shared" only when none is.
    Example: unshare(artifact_id="art_example")"""
    return _run(_svc().unshare, _principal(ctx), artifact_id, version)


@mcp.tool()
def revoke_previews(ctx: Context[Any, Any, Any], artifact_id: str, version: int | None = None) -> dict[str, Any]:
    """Expire private /p/ preview links for an artifact (or one version). Token revocation and signing-key
    rotation do not expire these database-backed capabilities; call this explicitly."""
    return _run(_svc().revoke_previews, _principal(ctx), artifact_id, version)


@mcp.tool()
def delete(
    ctx: Context[Any, Any, Any],
    artifact_id: Annotated[str, Field(description="Artifact to purge. Required.")],
    confirm_token: Annotated[
        str | None,
        Field(
            description=(
                "Confirmation from the first delete call. Optional on the first call (returns a token "
                'valid for 10 minutes). Required on the second call to purge. Example: "AbCdEf...".'
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """Two-step purge of an artifact. Warn the user before the second call.

    Permission: delete.
    Arguments: artifact_id (required), confirm_token (optional until the second call).
    Returns: first call is step "confirm" with confirm_token, title, versions, expires_in "10m", and a warning.
    Second call returns deleted true, artifact_id, and purged_object_versions.
    Example: delete(artifact_id="art_example") then delete(artifact_id="art_example", confirm_token="...")"""
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
                    "trial": row["kind"] == "trial" if "kind" in row.keys() else False,
                }
        if not principal:
            resp = JSONResponse({"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
            await resp(scope, receive, send)
            return
        scope.update({"am_principal": principal})
        await self.app(scope, receive, send)
        return


class ApiApp:
    """MCP bearer API, plus the pages on this same origin.

    /app/, /trial/, and the exact card path are not wrapped in BearerAuth.
    The preview origin never mounts them.
    """

    def __init__(self) -> None:
        self.pages = build_pages(_svc)
        self.api = BearerAuth(mcp.streamable_http_app())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Lifespan has no path. It must reach the MCP app so its session manager starts.
        if scope["type"] == "http":
            path = scope["path"]
            if (
                path == "/app"
                or path.startswith("/app/")
                or path == "/trial"
                or path.startswith("/trial/")
                or path == _CARD_PATH
            ):
                await self.pages(scope, receive, send)
                return
        await self.api(scope, receive, send)


async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "service": "artifactsmith", "version": __version__})


mcp.custom_route("/healthz", methods=["GET"])(healthz)


# ---------------- preview origin (separate port = separate origin; no cookies, strict CSP) ----------------
CSP = (
    "default-src 'none'; img-src data:; media-src data:; style-src 'unsafe-inline'; script-src 'none'; "
    "font-src data:; connect-src 'none'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'; "
    "sandbox allow-popups allow-popups-to-escape-sandbox allow-top-navigation-by-user-activation"
)


def _trial_chrome(aid: str, data: bytes) -> bytes:
    """noindex and an abuse footer for a trial workspace. Other pages are unchanged."""
    from . import trial as trial_mod

    row = _svc().db.one("SELECT workspace FROM artifacts WHERE id=?", aid)
    if row is None or not trial_mod.workspace_is_trial(_svc().db, str(row["workspace"])):
        return data
    return trial_mod.stamp_trial_chrome(data, trial_mod.abuse_email())


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


def _not_found() -> PlainTextResponse:
    return PlainTextResponse("not found", status_code=404, headers={"Cache-Control": "no-store"})


async def _open_page(
    *,
    aid: str,
    version: int,
    sha: str | None,
    raw: bool,
    attachment: bool,
) -> Response:
    try:
        data, ct, name, title, style = await asyncio.to_thread(_svc().read_primary_view, aid, version)
    except AMError:
        return _not_found()
    if sha is not None and hashlib.sha256(data).hexdigest() != sha:
        return _not_found()
    if raw:
        return _serve(data, ct, name, attachment=attachment)
    latest = await asyncio.to_thread(_svc().latest_done_version, aid)
    shown = title if latest == version else (page_title({name: data}, name) or title)
    if is_markdown_primary(name, ct):
        try:
            html = await asyncio.to_thread(markdown_share_html, data, title=shown, style=style)
        except MarkdownViewError:
            logging.getLogger("artifactsmith").warning("markdown view refused artifact=%s version=%s", aid, version)
            return _not_found()
        return _serve(
            _trial_chrome(aid, stamp_share_title(html, shown)), MIME["html"], SOURCE_NAME["html"], attachment=False
        )
    if name.endswith(".html") or (ct or "").lower().startswith("text/html"):
        page = stamp_share_title(data, shown)
        return _serve(_trial_chrome(aid, page), ct, name, attachment=False)
    return _serve(data, ct, name, attachment=False)


async def preview(request: Request) -> Response:
    tok = request.path_params["token"]
    sl = _svc().short_lookup(tok)
    if not sl:
        return PlainTextResponse("link expired or invalid", status_code=404, headers={"Cache-Control": "no-store"})
    download = request.url.path.endswith("/" + SOURCE_NAME["markdown"])
    raw = download or request.query_params.get("raw") == "1"
    return await _open_page(
        aid=sl["artifact_id"],
        version=int(sl["version"]),
        sha=None,
        raw=raw,
        attachment=download,
    )


async def download(request: Request) -> Response:
    p = unsign(request.path_params["token"])
    if not p or "f" not in p:
        return PlainTextResponse("link expired or invalid", status_code=404, headers={"Cache-Control": "no-store"})
    try:
        data, ct, _ = await asyncio.to_thread(_svc().read_file, p["a"], int(p["v"]), p["f"])
    except AMError:
        return _not_found()
    return _serve(data, ct, p.get("n") or p["f"], attachment=True)


async def shared(request: Request) -> Response:
    s = _svc().share_lookup(request.path_params["sid"])
    if not s:
        return _not_found()
    target = await asyncio.to_thread(_svc().share_target, s)
    if target is None:
        return _not_found()
    version, sha = target
    download = request.url.path.endswith("/" + SOURCE_NAME["markdown"])
    raw = download or request.query_params.get("raw") == "1"
    return await _open_page(
        aid=s["artifact_id"],
        version=version,
        sha=sha,
        raw=raw,
        attachment=download,
    )


preview_app = Starlette(
    routes=[
        Route("/p/{token}", preview),
        Route("/p/{token}/", preview),
        Route("/p/{token}/document.md", preview),
        Route("/dl/{token}", download),
        Route("/s/{sid}", shared),
        Route("/s/{sid}/", shared),
        Route("/s/{sid}/document.md", shared),
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
    api_app = ApiApp()
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
