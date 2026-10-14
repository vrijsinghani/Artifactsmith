"""Queue a new version of an artifact from a caller-published page."""

from __future__ import annotations

from typing import Any

from .builder import PUBLISHED_MODEL
from .publish_common import (
    dump_payload,
    idem_hit,
    idem_store,
    inferred_publish_format,
    insert_job,
    insert_version,
    require_body,
    style_or_error,
    unstyled_html_warning,
)
from .service import AMError, Service, now, rid
from .styles import DEFAULT_STYLE


def publish_version(
    svc: Service,
    principal: dict[str, Any],
    *,
    artifact_id: str,
    base_version: int,
    html: str | None,
    markdown: str | None,
    summary: str,
    assumptions: list[str],
    format: str | None,
    workspace: str | None,
    idempotency_key: str | None,
    style: str | None,
    display_name: str | None,
    note: str = "",
) -> dict[str, Any]:
    artifact = svc.get_artifact(principal, artifact_id)
    if workspace is not None and workspace != artifact["workspace"]:
        raise AMError("workspace does not match artifact")
    if artifact.get("deleting_at"):
        raise AMError("artifact is being deleted")
    if artifact["kind"] != "web_static":
        raise AMError("only kind=web_static is available in this build")
    fmt = inferred_publish_format(
        format,
        html,
        markdown,
        current=str(artifact.get("format") or ""),
    )
    require_body(fmt, html, markdown)
    if display_name is not None and display_name.strip() and display_name.strip() != artifact["display_name"]:
        raise AMError("display_name is fixed on the artifact")
    chosen = _kept_style(svc, artifact["id"], style)
    fingerprint = svc._request_fingerprint(
        {
            "op": "publish_version",
            "artifact_id": artifact["id"],
            "base_version": base_version,
            "format": fmt,
            "html": html,
            "markdown": markdown,
            "summary": summary,
            "assumptions": assumptions,
            "style": chosen,
            **({"note": note} if note else {}),
        }
    )
    prior = svc._idem_lookup(principal, idempotency_key, fingerprint)
    if prior:
        return dict(prior, idempotent_replay=True)
    jid, t = rid("job_"), now()
    payload = dump_payload(html, markdown, summary, assumptions)
    with svc.db.tx() as c:
        svc._check_quota(c, principal)
        replay = idem_hit(c, principal, idempotency_key, fingerprint)
        if replay:
            return replay
        row = c.execute("SELECT deleting_at FROM artifacts WHERE id=?", (artifact["id"],)).fetchone()
        if row and row["deleting_at"]:
            raise AMError("artifact is being deleted")
        newv = _next_version(c, artifact["id"], base_version)
        insert_version(
            c,
            aid=artifact["id"],
            version=newv,
            base_version=base_version,
            jid=jid,
            payload=payload,
            style=chosen,
            actor=principal["name"],
            t=t,
            format=fmt,
            note=note,
        )
        insert_job(c, jid=jid, aid=artifact["id"], version=newv, client=principal["id"], t=t)
        c.execute("UPDATE artifacts SET updated_at=? WHERE id=?", (t, artifact["id"]))
        result = {
            "artifact_id": artifact["id"],
            "slug": artifact["slug"],
            "workspace": artifact["workspace"],
            "version": newv,
            "base_version": base_version,
            "job_id": jid,
            "model": PUBLISHED_MODEL,
            "status": "queued",
            "style": chosen,
        }
        warning = unstyled_html_warning(fmt, html)
        if warning:
            result["warning"] = warning
        idem_store(svc, c, principal, idempotency_key, fingerprint, result, t)
    svc.audit(
        principal,
        "publish",
        artifact_id=artifact["id"],
        version=newv,
        base_version=base_version,
        model=PUBLISHED_MODEL,
    )
    svc._enqueue(jid)
    return result


def _kept_style(svc: Service, artifact_id: str, style: str | None) -> str:
    if style is None or str(style).strip() == "":
        prev = svc.db.one(
            "SELECT style FROM versions WHERE artifact_id=? AND status='done' ORDER BY version DESC LIMIT 1",
            artifact_id,
        )
        return style_or_error((prev or {}).get("style") or DEFAULT_STYLE)
    return style_or_error(style)


def _next_version(c: Any, artifact_id: str, base_version: int) -> int:
    latest = c.execute(
        "SELECT version,status FROM versions WHERE artifact_id=? "
        "AND status NOT IN ('failed','needs_input') ORDER BY version DESC LIMIT 1",
        (artifact_id,),
    ).fetchone()
    if not latest:
        raise AMError("artifact has no successful version to publish")
    if base_version != latest["version"]:
        raise AMError(
            f"conflict: base_version {base_version} is stale; latest is v{latest['version']}. "
            "Inspect it and publish from that version."
        )
    if latest["status"] != "done":
        raise AMError(f"conflict: v{latest['version']} is still {latest['status']}; wait for it, then publish")
    current = c.execute("SELECT MAX(version) m FROM versions WHERE artifact_id=?", (artifact_id,)).fetchone()["m"]
    return int(current) + 1
