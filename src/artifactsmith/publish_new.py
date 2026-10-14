"""Queue the first version of a caller-published artifact."""

from __future__ import annotations

from typing import Any

from .builder import PUBLISHED_MODEL
from .config import CFG
from .publish_common import dump_payload, idem_hit, idem_store, insert_job, insert_version, require_body, style_or_error
from .renderers import SUPPORTED_FORMATS
from .service import SLUG_RE, WS_RE, AMError, Service, now, rid, slugify

_EXISTS = "publish a new version with artifact_id and base_version"


def publish_new(
    svc: Service,
    principal: dict[str, Any],
    *,
    html: str | None,
    markdown: str | None,
    summary: str,
    assumptions: list[str],
    slug: str | None,
    display_name: str | None,
    format: str | None,
    workspace: str | None,
    idempotency_key: str | None,
    style: str | None,
) -> dict[str, Any]:
    ws = workspace or principal["workspace"]
    if not WS_RE.match(ws or ""):
        raise AMError("invalid workspace name")
    svc.check_access(principal, ws)
    fmt = (format or "html").lower().strip()
    if fmt not in SUPPORTED_FORMATS:
        raise AMError(f"format must be one of {sorted(SUPPORTED_FORMATS)}")
    require_body(fmt, html, markdown)
    chosen = style_or_error(style, default=CFG.default_style)
    title = (display_name or "").strip()
    raw_slug = (slug or "").strip()
    if not title and not raw_slug:
        raise AMError("provide display_name or slug")
    if not title:
        title = raw_slug
    if not raw_slug:
        raw_slug = slugify(title)
    if not SLUG_RE.match(raw_slug):
        raise AMError("slug must be 2-63 chars of a-z, 0-9 and '-'")
    name = svc._validate_display_name(title, raw_slug)
    fingerprint = svc._request_fingerprint(
        {
            "op": "publish",
            "slug": raw_slug,
            "display_name": name,
            "format": fmt,
            "workspace": ws,
            "html": html,
            "markdown": markdown,
            "summary": summary,
            "assumptions": assumptions,
            "style": chosen,
        }
    )
    prior = svc._idem_lookup(principal, idempotency_key, fingerprint)
    if prior:
        return dict(prior, idempotent_replay=True)
    aid, jid, t = rid("art_"), rid("job_"), now()
    prev = svc.db.one("SELECT * FROM artifacts WHERE workspace=? AND slug=?", ws, raw_slug)
    newv = 1
    if prev:
        if prev.get("deleting_at"):
            raise AMError("artifact is being deleted")
        statuses = {r["status"] for r in svc.db.all("SELECT status FROM versions WHERE artifact_id=?", prev["id"])}
        if statuses & {"done", "queued", "building"}:
            raise AMError(f"slug '{raw_slug}' already exists in workspace '{ws}'; {_EXISTS}")
        aid = str(prev["id"])
        newv = svc.db.must("SELECT COALESCE(MAX(version),0) m FROM versions WHERE artifact_id=?", aid)["m"] + 1
    payload = dump_payload(html, markdown, summary, assumptions)
    result = {
        "artifact_id": aid,
        "slug": raw_slug,
        "workspace": ws,
        "version": newv,
        "job_id": jid,
        "model": PUBLISHED_MODEL,
        "status": "queued",
        "style": chosen,
    }
    with svc.db.tx() as c:
        svc._check_quota(c, principal)
        replay = idem_hit(c, principal, idempotency_key, fingerprint)
        if replay:
            return replay
        if prev:
            row = c.execute("SELECT deleting_at FROM artifacts WHERE id=?", (aid,)).fetchone()
            if row and row["deleting_at"]:
                raise AMError("artifact is being deleted")
            c.execute(
                "UPDATE artifacts SET display_name=?, kind=?, format=?, updated_at=? WHERE id=?",
                (name, "web_static", fmt, t, aid),
            )
        elif c.execute("SELECT 1 FROM artifacts WHERE workspace=? AND slug=?", (ws, raw_slug)).fetchone():
            raise AMError(f"slug '{raw_slug}' already exists in workspace '{ws}'; {_EXISTS}")
        else:
            c.execute(
                "INSERT INTO artifacts (id,workspace,slug,display_name,kind,format,created_by,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (aid, ws, raw_slug, name, "web_static", fmt, principal["name"], t, t),
            )
        insert_version(
            c,
            aid=aid,
            version=newv,
            base_version=None,
            jid=jid,
            payload=payload,
            style=chosen,
            actor=principal["name"],
            t=t,
            format=fmt,
        )
        insert_job(c, jid=jid, aid=aid, version=newv, client=principal["id"], t=t)
        idem_store(svc, c, principal, idempotency_key, fingerprint, result, t)
    svc.audit(principal, "publish", artifact_id=aid, slug=raw_slug, format=fmt, model=PUBLISHED_MODEL)
    svc._enqueue(jid)
    return result
