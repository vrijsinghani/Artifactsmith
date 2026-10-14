"""Shared checks and rows for a caller-published page."""

from __future__ import annotations

import json
import re
from typing import Any

from .builder import PUBLISHED_MODEL
from .config import CFG
from .renderers import safety
from .renderers.safety import CONTENT_MAX_CHARS
from .service import AMError, Service, requested_format
from .styles import StyleError, parse_style

# Shown in version history. Not sent to a model.
PUBLISH_VERBATIM = "Published a finished page."

_WARNING_TAIL = (
    "so the page will look unstyled. "
    "For ArtifactSmith styling, publish Markdown with format=markdown (style defaults to house). "
    "Raw HTML is shown exactly as given."
)
_INFER_FORMATS = frozenset({"html", "markdown"})
_STYLE_BLOCK = re.compile(r"<style\b[^>]*>(.*?)</style>", re.I | re.S)
_STYLE_ATTR = re.compile(r"""\bstyle\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)
_CSS_ENOUGH = 24


def unstyled_html_warning(fmt: str, html: str | None) -> str | None:
    """Return a warning when published HTML will open without a stylesheet."""
    if fmt != "html":
        return None
    css = "".join(match.group(1) or "" for match in _STYLE_BLOCK.finditer(html or ""))
    for match in _STYLE_ATTR.finditer(html or ""):
        css += match.group(1) or match.group(2) or ""
    compact = re.sub(r"\s+", "", css)
    if len(compact) >= _CSS_ENOUGH:
        return None
    kind = "little" if compact else "no"
    return f"This HTML has {kind} CSS, {_WARNING_TAIL}"


def load_publish_payload(raw: str) -> dict[str, Any]:
    """Parse the stored page. Raises ``AMError`` when the JSON is not a publish payload."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AMError("publish payload is invalid") from exc
    if not isinstance(data, dict):
        raise AMError("publish payload is invalid")
    html = data.get("html")
    markdown = data.get("markdown")
    summary = data.get("summary") or ""
    assumptions = data.get("assumptions") or []
    if html is not None and not isinstance(html, str):
        raise AMError("publish payload is invalid")
    if markdown is not None and not isinstance(markdown, str):
        raise AMError("publish payload is invalid")
    if not isinstance(summary, str) or not isinstance(assumptions, list):
        raise AMError("publish payload is invalid")
    if any(not isinstance(item, str) for item in assumptions):
        raise AMError("publish payload is invalid")
    return {"html": html, "markdown": markdown, "summary": summary, "assumptions": assumptions}


def optional_text(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AMError(f"{label} must be a string")
    if len(value) > CONTENT_MAX_CHARS:
        raise AMError(f"content exceeds {CONTENT_MAX_CHARS} characters")
    return value


def _reject_long_metadata(text: str) -> None:
    """Reject one card field before it is stored.

    Every tool that accepts ``summary`` or ``assumptions`` must run the value
    through :func:`summary_text` or :func:`assumption_list`, which call this.
    The message matches :func:`artifactsmith.renderers.safety.check_fields`.
    """
    limit = safety.METADATA_MAX_CHARS
    if len(text) > limit:
        raise AMError(f"metadata exceeds {limit} characters")


def note_text(value: str | None) -> str:
    """Optional publish note. Rejected before a version row exists."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise AMError("note must be a string")
    text = value.strip()
    if not text:
        return ""
    problems = safety.check_fields(
        text,
        block_private_links=CFG.block_private_links,
        allowed_link_domains=CFG.allowed_link_domains,
        label="note",
    )
    if problems:
        raise AMError("; ".join(problems))
    return text


def summary_text(value: str | None) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise AMError("summary must be a string")
    text = value.strip()
    _reject_long_metadata(text)
    return text


def assumption_list(value: list[str] | None) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise AMError("assumptions must be a list of strings")
    out: list[str] = []
    for i, item in enumerate(value):
        if not isinstance(item, str):
            raise AMError(f"assumptions[{i}] must be a string")
        text = item.strip()
        if not text:
            continue
        _reject_long_metadata(text)
        out.append(text)
    if len(out) > 20:
        raise AMError("assumptions: at most 20 entries")
    return out


def inferred_publish_format(
    requested: str | None,
    html: str | None,
    markdown: str | None,
    *,
    current: str | None = None,
) -> str:
    """Pick a publish format when the caller leaves format blank.

    A non-blank request wins. Markdown with no HTML is markdown only when the
    artifact is html or markdown, or when the artifact is new. A pdf, docx, or
    xlsx artifact that receives only markdown keeps its format. HTML with no
    Markdown is html. Both bodies, or neither, keep the artifact format on a
    version and stay html on a new artifact.
    """
    if requested is not None and str(requested).strip():
        return requested_format(current, requested)
    has_html = bool((html or "").strip())
    has_md = bool((markdown or "").strip())
    kept = str(current or "").strip().lower()
    if has_md and not has_html:
        if kept and kept not in _INFER_FORMATS:
            return requested_format(current, None)
        return "markdown"
    if has_html and not has_md:
        return "html"
    if current is not None:
        return requested_format(current, None)
    return "html"


def require_body(fmt: str, html: str | None, markdown: str | None) -> None:
    if fmt == "html":
        if not (html or "").strip():
            raise AMError("html is required when format is html")
        return
    if not (markdown or "").strip():
        raise AMError(f"markdown is required when format is {fmt}")


def style_or_error(style: str | None, *, default: str | None = None) -> str:
    try:
        if default is None:
            return parse_style(style)
        return parse_style(style, default=default)
    except StyleError as exc:
        raise AMError(str(exc)) from exc


def dump_payload(html: str | None, markdown: str | None, summary: str, assumptions: list[str]) -> str:
    body: dict[str, Any] = {"summary": summary, "assumptions": assumptions}
    if html is not None:
        body["html"] = html
    if markdown is not None:
        body["markdown"] = markdown
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def idem_hit(c: Any, principal: dict[str, Any], key: str | None, fingerprint: str) -> dict[str, Any] | None:
    if not key:
        return None
    existing = c.execute(
        "SELECT result_json FROM idempotency WHERE client=? AND key=?",
        (principal["id"], key),
    ).fetchone()
    if not existing:
        return None
    data = json.loads(existing["result_json"])
    if isinstance(data, dict) and data.get("_fp") == fingerprint and isinstance(data.get("body"), dict):
        return dict(data["body"], idempotent_replay=True)
    raise AMError("idempotency_key was already used with a different request")


def idem_store(
    svc: Service,
    c: Any,
    principal: dict[str, Any],
    key: str | None,
    fingerprint: str,
    result: dict[str, Any],
    t: float,
) -> None:
    if not key:
        return
    c.execute(
        "INSERT INTO idempotency VALUES (?,?,?,?)",
        (principal["id"], key, svc._idem_payload(fingerprint, result), t),
    )


def insert_version(
    c: Any,
    *,
    aid: str,
    version: int,
    base_version: int | None,
    jid: str,
    payload: str,
    style: str,
    actor: str,
    t: float,
    format: str,
    note: str = "",
) -> None:
    c.execute(
        "INSERT INTO versions (artifact_id,version,base_version,verbatim_request,status,job_id,model,"
        "source_key,publish_json,style,format,note,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            aid,
            version,
            base_version,
            PUBLISH_VERBATIM,
            "queued",
            jid,
            PUBLISHED_MODEL,
            None,
            payload,
            style,
            format,
            note or None,
            actor,
            t,
        ),
    )


def insert_job(c: Any, *, jid: str, aid: str, version: int, client: str, t: float) -> None:
    c.execute(
        "INSERT INTO jobs (id,artifact_id,version,client,status,created_at) VALUES (?,?,?,?,?,?)",
        (jid, aid, version, client, "queued", t),
    )
