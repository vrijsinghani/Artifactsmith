"""Read-only catalog for one workspace. Other workspaces are absent, not forbidden."""

from __future__ import annotations

import re
import time
from typing import Any

from ..config import CFG
from ..db import DB
from ..service import iso
from ..styles import DEFAULT_STYLE

STATUSES = ("queued", "building", "done", "failed", "needs_input", "cancelled")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_QUERY_MAX = 200
_LIST_LIMIT = 50


def valid_artifact_id(artifact_id: str) -> bool:
    return _ID_RE.fullmatch(artifact_id or "") is not None


def like_contains(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def list_pages(db: DB, workspace: str, query: str, status: str) -> list[dict[str, Any]]:
    sql = (
        "SELECT a.*, v.version AS latest_version, v.status AS latest_status, v.style AS latest_style, "
        "v.format AS latest_format FROM artifacts a "
        "LEFT JOIN versions v ON v.artifact_id = a.id AND v.version = ("
        "SELECT MAX(version) FROM versions WHERE artifact_id = a.id) "
        "WHERE a.workspace=? AND a.deleting_at IS NULL"
    )
    args: list[Any] = [workspace]
    text = query.strip()[:_QUERY_MAX]
    if text:
        pattern = like_contains(text)
        sql += " AND (a.slug LIKE ? ESCAPE '\\' OR a.display_name LIKE ? ESCAPE '\\')"
        args.extend([pattern, pattern])
    if status:
        sql += " AND v.status=?"
        args.append(status)
    sql += " ORDER BY a.updated_at DESC LIMIT ?"
    args.append(_LIST_LIMIT)
    rows = []
    for art in db.all(sql, *args):
        rows.append(_card(db, art))
    return rows


def page_detail(db: DB, workspace: str, artifact_id: str) -> dict[str, Any] | None:
    if not valid_artifact_id(artifact_id):
        return None
    art = db.one(
        "SELECT * FROM artifacts WHERE id=? AND workspace=? AND deleting_at IS NULL",
        artifact_id,
        workspace,
    )
    if art is None:
        return None
    versions = db.all("SELECT * FROM versions WHERE artifact_id=? ORDER BY version DESC", art["id"])
    job_ids = [str(v["job_id"]) for v in versions if v.get("job_id")]
    jobs: dict[str, dict[str, Any]] = {}
    if job_ids:
        marks = ",".join("?" for _ in job_ids)
        for row in db.all(f"SELECT * FROM jobs WHERE id IN ({marks})", *job_ids):
            jobs[str(row["id"])] = row
    history = []
    for ver in versions:
        job = jobs.get(str(ver.get("job_id") or ""))
        history.append(
            {
                "version": int(ver["version"]),
                "status": str(ver["status"]),
                "format": str(ver.get("format") or art["format"]),
                "style": str(ver.get("style") or DEFAULT_STYLE),
                "created_at": iso(ver["created_at"]) or "",
                "job_status": str(job["status"]) if job else "",
                "duration": duration_label(job),
                "check": check_name(str(job["error"]) if job and job.get("error") else ""),
                "note": note_text(ver, job),
            }
        )
    return {
        "title": str(art["display_name"]),
        "slug": str(art["slug"]),
        "format": str(art["format"]),
        "updated_at": iso(art["updated_at"]) or "",
        "share_url": public_share_url(db, str(art["id"])),
        "versions": history,
    }


def public_share_url(db: DB, artifact_id: str) -> str:
    row = db.one(
        "SELECT id FROM shares WHERE artifact_id=? AND pinned=0 AND revoked_at IS NULL AND expires_at>? "
        "ORDER BY expires_at DESC LIMIT 1",
        artifact_id,
        time.time(),
    )
    if row is None:
        return ""
    return f"{CFG.share_url}/s/{row['id']}/"


def _card(db: DB, art: dict[str, Any]) -> dict[str, Any]:
    version = art.get("latest_version")
    return {
        "artifact_id": str(art["id"]),
        "title": str(art["display_name"]),
        "slug": str(art["slug"]),
        "format": str(art.get("latest_format") or art["format"]),
        "style": str(art.get("latest_style") or DEFAULT_STYLE),
        "latest_version": int(version) if version is not None else None,
        "status": str(art.get("latest_status") or ""),
        "updated_at": iso(art["updated_at"]) or "",
        "share_url": public_share_url(db, str(art["id"])),
    }


def check_name(error: str) -> str:
    text = error.strip()
    if not text:
        return ""
    if ": " in text:
        tail = text.rsplit(": ", 1)[-1].strip()
        if tail:
            return tail[:300]
    return text[:300]


def note_text(version: dict[str, Any], job: dict[str, Any] | None) -> str:
    summary = str(version.get("summary") or "").strip()
    if summary:
        return summary[:300]
    if job is None:
        return ""
    return str(job.get("progress") or "").strip()[:300]


def duration_label(job: dict[str, Any] | None) -> str:
    if job is None or job.get("started_at") is None:
        return ""
    started = float(job["started_at"])
    finished = job.get("finished_at")
    if finished is None:
        if str(job.get("status")) in ("queued", "building"):
            return "running"
        return ""
    seconds = max(0, int(float(finished) - started))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"
