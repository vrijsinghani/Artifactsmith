"""Core operations shared by the MCP tools, the HTTP routes and the CLI."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from datetime import UTC, datetime
from html import escape, unescape
from typing import Any

from . import builder, llm, trial
from .build_mode import ModeError, parse_build_mode
from .config import CFG
from .db import DB, jloads
from .renderers import EXTENSION, SUPPORTED_FORMATS
from .renderers.safety import check_fields, strip_ooxml_controls
from .store import Store
from .styles import DEFAULT_STYLE, StyleError, parse_style

log = logging.getLogger("artifactsmith.service")

ALL_PERMS = {"create", "read", "edit", "publish", "export", "share", "delete"}

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
WS_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
SOURCE_CAP_BYTES = 200 * 1024
VERBATIM_CAP_CHARS = 32_000
DISPLAY_NAME_CAP_CHARS = 200
_HTML_TITLE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.I | re.S)
_HTML_H1 = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.I | re.S)
_MD_HEADING = re.compile(r"^#\s+(.+?)\s*$", re.M)
_TAGS = re.compile(r"<[^>]+>")
_TEXT_NAMES = (".html", ".md", ".txt")


def _plain_fragment(fragment: str) -> str:
    return " ".join(_TAGS.sub("", unescape(fragment)).split())


def page_title(files: dict[str, bytes], primary: str) -> str | None:
    """Title stored in a rendered page: HTML title, else the first Markdown heading."""
    ordered: list[tuple[str, bytes]] = []
    if primary in files:
        ordered.append((primary, files[primary]))
    for name in ("index.html", "document.md", "content.md"):
        if name in files and name != primary:
            ordered.append((name, files[name]))
    for name, data in ordered:
        if not name.endswith(_TEXT_NAMES):
            continue
        text = data.decode("utf-8", errors="replace")
        if name.endswith(".html"):
            found = _html_title(text)
        else:
            found = _md_heading(text)
        if found:
            return found
    return None


def _html_title(text: str) -> str | None:
    match = _HTML_TITLE.search(text)
    if match:
        plain = _plain_fragment(match.group(1))
        if plain:
            return plain
    match = _HTML_H1.search(text)
    if match:
        plain = _plain_fragment(match.group(1))
        if plain:
            return plain
    return None


_OG_TITLE = re.compile(r"""<meta\b[^>]*property=["']og:title["'][^>]*>""", re.I)


def stamp_share_title(html: bytes, title: str) -> bytes:
    """Set the served page's title and Open Graph title without touching stored bytes."""
    safe = escape(title, quote=True)
    text = html.decode("utf-8", errors="replace")
    title_tag = f"<title>{safe}</title>"
    if _HTML_TITLE.search(text):
        text = _HTML_TITLE.sub(lambda _match: title_tag, text, count=1)
    else:
        text = re.sub(r"(?i)<head[^>]*>", lambda match: match.group(0) + "\n" + title_tag, text, count=1)
    og = f'<meta property="og:title" content="{safe}">'
    if _OG_TITLE.search(text):
        text = _OG_TITLE.sub(lambda _match: og, text, count=1)
    else:
        text = re.sub(r"(?i)<head[^>]*>", lambda match: match.group(0) + "\n" + og, text, count=1)
    return text.encode("utf-8")


def _md_heading(text: str) -> str | None:
    fenced = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = _MD_HEADING.match(line)
        if not match:
            continue
        heading = match.group(1).strip()
        if heading:
            return heading
    return None


def resolved_display_name(
    requested: str | None,
    current: str,
    slug: str,
    files: dict[str, bytes],
    primary: str,
    *,
    adopt_page_title: bool,
) -> str | None:
    """Name to store when a version finishes. None means leave the artifact name.

    A caller-supplied name wins. A changed page title is used only for a later
    version, so the first publish keeps the name the caller set.
    """
    explicit = (requested or "").strip()
    if explicit:
        return explicit
    if not adopt_page_title:
        return None
    found = page_title(files, primary)
    if not found or found == current:
        return None
    try:
        return Service._validate_display_name(found, slug)
    except AMError:
        return None


IDEMPOTENCY_KEY_CAP_CHARS = 128
HISTORY_CAP_ENTRIES = 50
HISTORY_CAP_CHARS = 64_000
KINDS = {"web_static", "file"}


def slugify(text: str) -> str:
    """Derive a URL slug from a display title (a-z, 0-9, hyphens; 2–63 chars)."""
    s = unicodedata_normalize_ascii(text).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    if len(s) < 2:
        s = (s + "-item")[:63]
    return s[:63].rstrip("-") or "item"


def unicodedata_normalize_ascii(text: str) -> str:
    import unicodedata

    # Fold accents so "Café Brief" → "Cafe Brief" before slugifying.
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c))


class AMError(Exception):
    """User-facing error (the message is safe to return to callers)."""


def write_audit(who: str, action: str, **fields: Any) -> None:
    """Append one JSON line to the audit log."""
    rec = {"ts": iso(now()), "actor": who, "action": action, **fields}
    try:
        CFG.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with open(CFG.audit_path, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except OSError:
        pass


def now() -> float:
    return time.time()


def iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds") if ts else None


def rid(prefix: str, n: int = 10) -> str:
    return prefix + base64.b32encode(secrets.token_bytes(n)).decode().lower().rstrip("=")[: n + 4]


def shortid(n: int = 12) -> str:
    return "".join(secrets.choice("abcdefghijkmnpqrstuvwxyz23456789") for _ in range(n))


_CHECK_PREFIX = "builder output failed checks twice: "
_HTML_ATTR = "blocked or authority-confused HTML "
_DROP_HOST = ("private or local link host", "link host not on allow-list")


def _redact_problem(problem: str) -> str:
    """Keep the check name. Drop attribute values, hosts, and raw URLs."""
    if problem.startswith(_HTML_ATTR):
        name = problem[len(_HTML_ATTR) :].split(":", 1)[0].strip()
        return _HTML_ATTR + name
    label = next((item for item in _DROP_HOST if problem.startswith(item + ":")), "")
    return label or problem


def _job_check_error(message: str) -> str:
    """Keep the check name. Drop attribute values, hosts, and raw URLs.

    A ``needs_input:`` prefix is kept. The check after it is redacted the same way.
    """
    if not message.startswith(_CHECK_PREFIX):
        return "build failed: BuildError"
    parts: list[str] = []
    for raw in message[len(_CHECK_PREFIX) :].split("; "):
        problem = raw.strip()
        if not problem:
            continue
        prefix = ""
        if problem.startswith("needs_input:"):
            prefix = "needs_input: "
            problem = problem.split(":", 1)[1].strip()
        parts.append(prefix + _redact_problem(problem))
    return _CHECK_PREFIX + "; ".join(parts)


# ---------- signed short-lived tokens for private preview / download ----------
def sign(payload: dict[str, Any], ttl_s: int) -> str:
    payload = dict(payload, exp=int(now() + ttl_s))
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    mac = hmac.new(CFG.signing_key(), body.encode(), hashlib.sha256).digest()[:16]
    return body + "." + base64.urlsafe_b64encode(mac).decode().rstrip("=")


def unsign(token: str) -> dict[str, Any] | None:
    try:
        body, mac = token.split(".", 1)
        want = (
            base64.urlsafe_b64encode(hmac.new(CFG.signing_key(), body.encode(), hashlib.sha256).digest()[:16])
            .decode()
            .rstrip("=")
        )
        if not hmac.compare_digest(want, mac):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if not isinstance(payload, dict):
            return None
        if payload.get("exp", 0) < now():
            return None
        return payload
    except Exception:
        return None


def requested_format(current: str | None, requested: str | None) -> str:
    """Blank request keeps the artifact format. Any other value must be a supported format."""
    if requested is None or not str(requested).strip():
        fmt = str(current or "html").lower().strip()
    else:
        fmt = str(requested).lower().strip()
    if fmt not in SUPPORTED_FORMATS:
        raise AMError(f"format must be one of {sorted(SUPPORTED_FORMATS)}")
    return fmt


def version_format(v: dict[str, Any], a: dict[str, Any]) -> str:
    """Format of this version. A blank column falls back only for rows written before the column existed."""
    raw = v.get("format")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().lower()
    return str(a.get("format") or "html")


class Service:
    # Far-future stamp used when AM_SHARE_TTL_DAYS=0 ("until revoked"). Still a real timestamp so queries work.
    NEVER_EXPIRES = 4102444800.0  # 2100-01-01 UTC

    def __init__(self) -> None:
        self.db = DB(CFG.db_path)
        self.store = Store()
        # Internal jobs are preferred. A second trial job waits so one slot stays free.
        self.internal_q: asyncio.Queue[str] = asyncio.Queue()
        self.trial_q: asyncio.Queue[str] = asyncio.Queue()
        self.queue = self.internal_q
        self.progress: dict[str, str] = {}
        self._workers: list[asyncio.Task[None]] = []
        self._retention_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._trial_running = 0
        self._sched_lock = asyncio.Lock()
        self._wake = asyncio.Event()

    def _job_is_trial(self, jid: str) -> bool:
        row = self.db.one(
            "SELECT tokens.kind AS kind FROM jobs JOIN tokens ON tokens.id = jobs.client WHERE jobs.id=?",
            jid,
        )
        return bool(row and row["kind"] == "trial")

    def _token_is_trial(self, token_id: str) -> bool:
        row = self.db.one("SELECT kind FROM tokens WHERE id=?", token_id)
        return bool(row and row["kind"] == "trial")

    def _put_job(self, q: asyncio.Queue[str], jid: str) -> None:
        def deliver() -> None:
            q.put_nowait(jid)
            self._wake.set()

        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(deliver)
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None:
            running.call_soon_threadsafe(deliver)
            return
        deliver()

    def _enqueue(self, jid: str) -> None:
        """Wake a worker from any thread. asyncio.Queue is not thread-safe for put_nowait."""
        q = self.trial_q if self._job_is_trial(jid) else self.internal_q
        self._put_job(q, jid)

    # ---------------- access ----------------
    @staticmethod
    def require(principal: dict[str, Any], perm: str) -> None:
        if perm not in principal["perms"]:
            raise AMError(f"token '{principal['name']}' is not permitted to {perm}")

    @staticmethod
    def check_access(principal: dict[str, Any], workspace: str) -> None:
        """A token reaches only its own workspace. Knowing an id is not access."""
        if workspace != principal["workspace"]:
            raise AMError("forbidden: the artifact belongs to a different workspace")

    def get_artifact(
        self,
        principal: dict[str, Any],
        artifact_id: str | None = None,
        slug: str | None = None,
        workspace: str | None = None,
    ) -> dict[str, Any]:
        if artifact_id:
            a = self.db.one("SELECT * FROM artifacts WHERE id=?", artifact_id)
        elif slug and workspace:
            a = self.db.one("SELECT * FROM artifacts WHERE workspace=? AND slug=?", workspace, slug)
        else:
            raise AMError("artifact_id is required")
        if not a:
            raise AMError("artifact not found")
        self.check_access(principal, a["workspace"])
        return a

    @staticmethod
    def _request_fingerprint(parts: dict[str, Any]) -> str:
        blob = json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(blob.encode()).hexdigest()

    def _idem_lookup(self, principal: dict[str, Any], key: str | None, fingerprint: str) -> dict[str, Any] | None:
        """Return a prior result, or raise if the same key was used with a different request."""
        if not key:
            return None
        r = self.db.one("SELECT result_json FROM idempotency WHERE client=? AND key=?", principal["id"], key)
        if not r:
            return None
        data = json.loads(r["result_json"])
        if isinstance(data, dict) and "_fp" in data and "body" in data:
            if data["_fp"] != fingerprint:
                raise AMError("idempotency_key was already used with a different request")
            return data["body"] if isinstance(data["body"], dict) else None
        # Legacy rows without a fingerprint: treat as a conflict rather than a silent mismatch.
        raise AMError("idempotency_key was already used with a different request")

    @staticmethod
    def _idem_payload(fingerprint: str, result: dict[str, Any]) -> str:
        return json.dumps({"_fp": fingerprint, "body": result}, separators=(",", ":"))

    def _check_quota(self, c: Any, principal: dict[str, Any]) -> None:
        if principal.get("trial"):
            if not CFG.trial_enabled:
                raise AMError(trial.CLOSED)
            # Reservations survive delete. Counting jobs would hand the builds back.
            total = c.execute(
                "SELECT COUNT(*) n FROM trial_reservations WHERE token_id=?",
                (principal["id"],),
            ).fetchone()["n"]
            if int(total) >= int(CFG.trial_build_limit):
                raise AMError(trial.quota_error())
            hour_limit = int(CFG.trial_builds_per_hour)
            n = c.execute(
                "SELECT COUNT(*) n FROM trial_reservations WHERE token_id=? AND created_at>?",
                (principal["id"], now() - 3600),
            ).fetchone()["n"]
        else:
            hour_limit = int(CFG.builds_per_hour)
            n = c.execute(
                "SELECT COUNT(*) n FROM jobs WHERE client=? AND created_at>?",
                (principal["id"], now() - 3600),
            ).fetchone()["n"]
        if int(n) >= hour_limit:
            raise AMError(f"quota: {hour_limit} builds/hour reached")

    def _hold_trial(self, c: Any, principal: dict[str, Any], jid: str, when: float) -> None:
        if not principal.get("trial"):
            return
        try:
            trial.reserve(c, jid, when, str(principal["id"]))
        except trial.TrialLimit as exc:
            raise AMError(exc.message) from exc

    def _builds_remaining(self, principal: dict[str, Any]) -> int:
        row = self.db.one("SELECT COUNT(*) AS n FROM trial_reservations WHERE token_id=?", principal["id"])
        used = int(row["n"]) if row else 0
        return max(0, int(CFG.trial_build_limit) - used)

    def _annotate_trial(self, principal: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        if principal.get("trial"):
            result["builds_remaining"] = self._builds_remaining(principal)
        return result

    @staticmethod
    def _validate_idempotency_key(key: str | None) -> str | None:
        if key is None:
            return None
        if not isinstance(key, str) or not key.strip():
            raise AMError("idempotency_key must be a non-empty string")
        if len(key) > IDEMPOTENCY_KEY_CAP_CHARS:
            raise AMError(f"idempotency_key exceeds {IDEMPOTENCY_KEY_CAP_CHARS} characters")
        return key

    @staticmethod
    def _validate_verbatim(verbatim_request: str) -> str:
        text = (verbatim_request or "").strip()
        if not text:
            raise AMError("verbatim_request is required")
        if len(text) > VERBATIM_CAP_CHARS:
            raise AMError(f"verbatim_request exceeds {VERBATIM_CAP_CHARS} characters")
        return text

    @staticmethod
    def _validate_display_name(display_name: str, slug: str) -> str:
        name = strip_ooxml_controls((display_name or slug or "").strip() or slug)
        if not name:
            name = slug
        if len(name) > DISPLAY_NAME_CAP_CHARS:
            raise AMError(f"display_name exceeds {DISPLAY_NAME_CAP_CHARS} characters")
        return name

    def _requested_name(self, display_name: str | None, slug: str) -> str | None:
        """Blank means the page title may rename the artifact when the version finishes."""
        if display_name is None or not str(display_name).strip():
            return None
        name = self._validate_display_name(str(display_name), slug)
        problems = check_fields(
            name,
            block_private_links=CFG.block_private_links,
            allowed_link_domains=CFG.allowed_link_domains,
            max_chars=DISPLAY_NAME_CAP_CHARS,
            label="display_name",
        )
        if problems:
            raise AMError(problems[0])
        return name

    def latest_done_version(self, artifact_id: str) -> int | None:
        row = self.db.one(
            "SELECT MAX(version) AS version FROM versions WHERE artifact_id=? AND status='done'",
            artifact_id,
        )
        if not row or row["version"] is None:
            return None
        return int(row["version"])

    @staticmethod
    def _model(model: str | None) -> str:
        m = (model or "").strip() or CFG.default_model
        if not MODEL_RE.match(m):
            raise AMError("model must be a plain model name")
        return m

    def _resolve_model(self, principal: dict[str, Any], model: str | None) -> str:
        """Trial builds ignore the caller's model name."""
        if not principal.get("trial"):
            return self._model(model)
        if not CFG.trial_enabled:
            raise AMError(trial.CLOSED)
        if not MODEL_RE.match(CFG.trial_model):
            raise AMError("hosted model is not configured")
        return CFG.trial_model

    @staticmethod
    def _source(source_content: str | None, source_files: list[dict[str, Any]] | None) -> dict[str, Any] | None:
        files = []
        for i, f in enumerate(source_files or []):
            if not isinstance(f, dict) or not isinstance(f.get("content"), str):
                raise AMError(f"source_files[{i}] must be an object {{name, content}} with text content")
            files.append({"name": str(f.get("name") or f"file{i + 1}")[:120], "content": f["content"]})
        if len(files) > 20:
            raise AMError("source_files: at most 20 files")
        if source_content is not None and not isinstance(source_content, str):
            raise AMError("source_content must be a string")
        content = source_content if (source_content or "").strip() else None
        if not content and not files:
            return None
        size = len((content or "").encode()) + sum(len(f["content"].encode()) for f in files)
        if size > SOURCE_CAP_BYTES:
            raise AMError(f"source material is {size} bytes; the cap is {SOURCE_CAP_BYTES} (200 KB) total")
        return {"source_content": content, "source_files": files, "bytes": size}

    def _put_source(self, workspace: str, aid: str, version: int, bundle: dict[str, Any] | None) -> str | None:
        if not bundle:
            return None
        key = Store.prefix(workspace, aid, version) + "_input/source.json"
        self.store.put(key, json.dumps(bundle).encode(), "application/json")
        return key

    # ---------------- create ----------------
    def create(
        self,
        principal: dict[str, Any],
        *,
        verbatim_request: str,
        slug: str | None = None,
        display_name: str | None = None,
        kind: str = "web_static",
        format: str | None = None,
        workspace: str | None = None,
        model: str | None = None,
        source_content: str | None = None,
        source_files: list[dict[str, Any]] | None = None,
        capabilities: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        style: str | None = None,
        mode: str | None = None,
    ) -> dict[str, Any]:
        self.require(principal, "create")
        ws = workspace or principal["workspace"]
        if not WS_RE.match(ws or ""):
            raise AMError("invalid workspace name")
        self.check_access(principal, ws)
        kind = (kind or "web_static").strip()
        if kind not in KINDS:
            raise AMError(f"kind must be one of {sorted(KINDS)}")
        if kind == "file":
            raise AMError("only kind=web_static is available in this build")
        fmt = (format or "html").lower().strip()
        if fmt not in SUPPORTED_FORMATS:
            raise AMError(f"format must be one of {sorted(SUPPORTED_FORMATS)}")
        format = fmt
        try:
            style = parse_style(style, default=CFG.default_style)
        except StyleError as e:
            raise AMError(str(e)) from e
        mode_omitted = mode is None or str(mode).strip() == ""
        try:
            default_mode = CFG.build_mode
            if mode_omitted and style in CFG.fast_styles:
                default_mode = "fast"
            mode = parse_build_mode(mode, default=default_mode)
        except ModeError as e:
            raise AMError(str(e)) from e
        if fmt != "html":
            mode = "full"
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
        slug = raw_slug
        model = self._resolve_model(principal, model)
        verbatim_request = self._validate_verbatim(verbatim_request)
        display_name = self._validate_display_name(title, slug)
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        caps = capabilities or {}
        if not isinstance(caps, dict):
            raise AMError("capabilities must be an object")
        if caps.get("web") or caps.get("connectors"):
            raise AMError("capabilities web/connectors are not available yet")
        bundle = self._source(source_content, source_files)
        # A finished HTML page is not a Markdown fast build. Explicit mode="fast" still wins.
        if mode_omitted and mode == "fast" and builder.skip_fast_for_finished_html(verbatim_request, bundle):
            mode = "full"
        fingerprint = self._request_fingerprint(
            {
                "op": "create",
                "slug": slug,
                "display_name": display_name,
                "kind": kind,
                "format": format,
                "workspace": ws,
                "model": model,
                "verbatim_request": verbatim_request,
                "source_content": source_content,
                "source_files": source_files,
                "capabilities": caps,
                "style": style,
                "mode": mode,
            }
        )
        prior = self._idem_lookup(principal, idempotency_key, fingerprint)
        if prior:
            return self._annotate_trial(principal, dict(prior, idempotent_replay=True))
        aid, jid, t = rid("art_"), rid("job_"), now()
        prev = self.db.one("SELECT * FROM artifacts WHERE workspace=? AND slug=?", ws, slug)
        newv = 1
        if prev:
            if prev.get("deleting_at"):
                raise AMError("artifact is being deleted")
            st = {r["status"] for r in self.db.all("SELECT status FROM versions WHERE artifact_id=?", prev["id"])}
            if st & {"done", "queued", "building"}:
                raise AMError(f"slug '{slug}' already exists in workspace '{ws}'; use edit")
            aid = prev["id"]
            newv = self.db.must("SELECT COALESCE(MAX(version),0) m FROM versions WHERE artifact_id=?", aid)["m"] + 1
        skey = self._put_source(ws, aid, newv, bundle)
        result = {
            "artifact_id": aid,
            "slug": slug,
            "workspace": ws,
            "version": newv,
            "job_id": jid,
            "model": model,
            "status": "queued",
            "source_bytes": bundle["bytes"] if bundle else 0,
            "style": style,
            "mode": mode,
        }
        with self.db.tx() as c:
            self._check_quota(c, principal)
            if idempotency_key:
                existing = c.execute(
                    "SELECT result_json FROM idempotency WHERE client=? AND key=?",
                    (principal["id"], idempotency_key),
                ).fetchone()
                if existing:
                    data = json.loads(existing["result_json"])
                    if isinstance(data, dict) and data.get("_fp") == fingerprint and isinstance(data.get("body"), dict):
                        return self._annotate_trial(principal, dict(data["body"], idempotent_replay=True))
                    raise AMError("idempotency_key was already used with a different request")
            if prev:
                row = c.execute("SELECT deleting_at FROM artifacts WHERE id=?", (aid,)).fetchone()
                if row and row["deleting_at"]:
                    raise AMError("artifact is being deleted")
                c.execute(
                    "UPDATE artifacts SET display_name=?, kind=?, format=?, updated_at=? WHERE id=?",
                    (display_name, kind, format, t, aid),
                )
            else:
                if c.execute("SELECT 1 FROM artifacts WHERE workspace=? AND slug=?", (ws, slug)).fetchone():
                    raise AMError(f"slug '{slug}' already exists in workspace '{ws}'; use edit")
                c.execute(
                    "INSERT INTO artifacts (id,workspace,slug,display_name,kind,format,created_by,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (aid, ws, slug, display_name, kind, format, principal["name"], t, t),
                )
            c.execute(
                "INSERT INTO versions (artifact_id,version,base_version,verbatim_request,status,job_id,model,"
                "source_key,style,mode,format,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    aid,
                    newv,
                    None,
                    verbatim_request,
                    "queued",
                    jid,
                    model,
                    skey,
                    style,
                    mode,
                    format,
                    principal["name"],
                    t,
                ),
            )
            c.execute(
                "INSERT INTO jobs (id,artifact_id,version,client,status,created_at) VALUES (?,?,?,?,?,?)",
                (jid, aid, newv, principal["id"], "queued", t),
            )
            self._hold_trial(c, principal, jid, t)
            if idempotency_key:
                c.execute(
                    "INSERT INTO idempotency VALUES (?,?,?,?)",
                    (principal["id"], idempotency_key, self._idem_payload(fingerprint, result), t),
                )
        self.audit(principal, "create", artifact_id=aid, slug=slug, kind=kind, model=model)
        self._enqueue(jid)
        return self._annotate_trial(principal, result)

    # ---------------- edit ----------------
    def edit(
        self,
        principal: dict[str, Any],
        *,
        base_version: int,
        verbatim_request: str,
        artifact_id: str,
        workspace: str | None = None,
        model: str | None = None,
        source_content: str | None = None,
        source_files: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
        style: str | None = None,
        format: str | None = None,
        display_name: str | None = None,
    ) -> dict[str, Any]:
        self.require(principal, "edit")
        a = self.get_artifact(principal, artifact_id)
        if workspace is not None and workspace != a["workspace"]:
            raise AMError("workspace does not match artifact")
        if a.get("deleting_at"):
            raise AMError("artifact is being deleted")
        verbatim_request = self._validate_verbatim(verbatim_request)
        model = self._resolve_model(principal, model)
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        if style is None or str(style).strip() == "":
            prev = self.db.one(
                "SELECT style FROM versions WHERE artifact_id=? AND status='done' ORDER BY version DESC LIMIT 1",
                a["id"],
            )
            style = (prev or {}).get("style") or DEFAULT_STYLE
        try:
            style = parse_style(style)
        except StyleError as e:
            raise AMError(str(e)) from e
        fmt = requested_format(str(a.get("format") or ""), format)
        requested_name = self._requested_name(display_name, str(a["slug"]))
        bundle = self._source(source_content, source_files)
        fingerprint = self._request_fingerprint(
            {
                "op": "edit",
                "artifact_id": a["id"],
                "base_version": int(base_version),
                "model": model,
                "verbatim_request": verbatim_request,
                "source_content": source_content,
                "source_files": source_files,
                "style": style,
                "format": fmt,
                "display_name": requested_name,
            }
        )
        prior = self._idem_lookup(principal, idempotency_key, fingerprint)
        if prior:
            return self._annotate_trial(principal, dict(prior, idempotent_replay=True))
        jid, t = rid("job_"), now()
        with self.db.tx() as c:
            self._check_quota(c, principal)
            if idempotency_key:
                existing = c.execute(
                    "SELECT result_json FROM idempotency WHERE client=? AND key=?",
                    (principal["id"], idempotency_key),
                ).fetchone()
                if existing:
                    data = json.loads(existing["result_json"])
                    if isinstance(data, dict) and data.get("_fp") == fingerprint and isinstance(data.get("body"), dict):
                        return self._annotate_trial(principal, dict(data["body"], idempotent_replay=True))
                    raise AMError("idempotency_key was already used with a different request")
            art = c.execute("SELECT deleting_at FROM artifacts WHERE id=?", (a["id"],)).fetchone()
            if art and art["deleting_at"]:
                raise AMError("artifact is being deleted")
            latest = c.execute(
                "SELECT version,status FROM versions WHERE artifact_id=? "
                "AND status NOT IN ('failed','needs_input') ORDER BY version DESC LIMIT 1",
                (a["id"],),
            ).fetchone()
            if not latest:
                raise AMError("artifact has no successful version to edit")
            if int(base_version) != latest["version"]:
                raise AMError(
                    f"conflict: base_version {base_version} is stale; latest is v{latest['version']}. "
                    "Inspect it and edit from that version."
                )
            if latest["status"] != "done":
                raise AMError(f"conflict: v{latest['version']} is still {latest['status']}; wait for it, then edit")
            newv = c.execute("SELECT MAX(version) m FROM versions WHERE artifact_id=?", (a["id"],)).fetchone()["m"] + 1
            skey = (
                self._put_source(a["workspace"], a["id"], newv, bundle)
                if bundle
                else c.execute(
                    "SELECT source_key FROM versions WHERE artifact_id=? AND version=?", (a["id"], int(base_version))
                ).fetchone()["source_key"]
            )
            c.execute(
                "INSERT INTO versions (artifact_id,version,base_version,verbatim_request,status,job_id,model,"
                "source_key,style,format,requested_name,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    a["id"],
                    newv,
                    int(base_version),
                    verbatim_request,
                    "queued",
                    jid,
                    model,
                    skey,
                    style,
                    fmt,
                    requested_name,
                    principal["name"],
                    t,
                ),
            )
            c.execute(
                "INSERT INTO jobs (id,artifact_id,version,client,status,created_at) VALUES (?,?,?,?,?,?)",
                (jid, a["id"], newv, principal["id"], "queued", t),
            )
            self._hold_trial(c, principal, jid, t)
            c.execute("UPDATE artifacts SET updated_at=? WHERE id=?", (t, a["id"]))
            result = {
                "artifact_id": a["id"],
                "slug": a["slug"],
                "workspace": a["workspace"],
                "version": newv,
                "base_version": int(base_version),
                "job_id": jid,
                "model": model,
                "status": "queued",
                "style": style,
            }
            if idempotency_key:
                c.execute(
                    "INSERT INTO idempotency VALUES (?,?,?,?)",
                    (principal["id"], idempotency_key, self._idem_payload(fingerprint, result), t),
                )
        self.audit(principal, "edit", artifact_id=a["id"], version=newv, base_version=base_version, model=model)
        self._enqueue(jid)
        return self._annotate_trial(principal, result)

    def publish(
        self,
        principal: dict[str, Any],
        *,
        html: str | None = None,
        markdown: str | None = None,
        slug: str | None = None,
        display_name: str | None = None,
        artifact_id: str | None = None,
        base_version: int | None = None,
        format: str | None = None,
        summary: str | None = None,
        assumptions: list[str] | None = None,
        workspace: str | None = None,
        idempotency_key: str | None = None,
        style: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Queue a finished page. The worker skips the model and runs the output checks."""
        from .publishing import admit_publish

        if principal.get("trial"):
            raise AMError("hosted tokens cannot publish")
        return admit_publish(
            self,
            principal,
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

    # ---------------- worker ----------------
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Record the running loop so _enqueue works before workers start."""
        self._loop = loop

    def start_workers(self) -> None:
        self.bind_loop(asyncio.get_running_loop())
        # One process per data directory: requeue every queued/building job.
        # Claim stays atomic (UPDATE … WHERE status='queued'), so a mid-build restart finishes.
        for j in self.db.all("SELECT id FROM jobs WHERE status IN ('queued','building') ORDER BY created_at"):
            self.db.exec(
                "UPDATE jobs SET status='queued', progress='re-queued after restart' WHERE id=?",
                j["id"],
            )
            self._enqueue(j["id"])
        try:
            trial.purge_idle_trials(self.db, self.store)
        except Exception:  # noqa: BLE001 — retention must not stop the server
            log.exception("trial retention sweep failed")
        self._retention_task = asyncio.create_task(self._retention_loop())
        for _ in range(CFG.max_concurrent_builds):
            self._workers.append(asyncio.create_task(self._worker()))

    def _retention_interval_s(self) -> float:
        return 3600.0

    async def _retention_loop(self) -> None:
        """Sweep idle trials on the hour. A failed sweep stays in this loop."""
        while True:
            await asyncio.sleep(self._retention_interval_s())
            try:
                await asyncio.to_thread(trial.purge_idle_trials, self.db, self.store)
            except Exception:  # noqa: BLE001 — retention must not stop the server
                log.exception("trial retention sweep failed")

    async def shutdown(self) -> None:
        """Cancel workers and close the database. Safe to call more than once."""
        pending = list(self._workers)
        if self._retention_task is not None:
            pending.append(self._retention_task)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._workers.clear()
        self._retention_task = None
        self.db.close()

    def _take(self, q: asyncio.Queue[str]) -> str | None:
        try:
            return q.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def _claim_next(self) -> tuple[str, bool] | None:
        """Internal work first. At most one trial build is in flight."""
        jid = self._take(self.internal_q)
        if jid is not None:
            return jid, False
        if self._trial_running < 1:
            jid = self._take(self.trial_q)
            if jid is not None:
                self._trial_running += 1
                return jid, True
        return None

    async def _next_job(self) -> tuple[str, bool]:
        while True:
            async with self._sched_lock:
                found = self._claim_next()
                if found is not None:
                    return found
                # A job can arrive between the empty check and the clear.
                self._wake.clear()
                found = self._claim_next()
                if found is not None:
                    return found
            await self._wake.wait()

    async def _worker(self) -> None:
        while True:
            jid, trial_slot = await self._next_job()
            try:
                await self._run_job(jid)
            except Exception as e:  # noqa: BLE001
                log.exception("worker failed job=%s", jid)
                self._fail(jid, f"internal error: {type(e).__name__}")
            finally:
                if trial_slot:
                    async with self._sched_lock:
                        self._trial_running = max(0, self._trial_running - 1)
                (self.trial_q if trial_slot else self.internal_q).task_done()
                self._wake.set()

    def _set_job(self, jid: str, status: str, error: str | None = None, progress_msg: str | None = None) -> None:
        j = self.db.one("SELECT * FROM jobs WHERE id=?", jid)
        if not j:
            return
        with self.db.tx() as c:
            c.execute(
                "UPDATE jobs SET status=?, error=?, progress=?, finished_at=? WHERE id=?",
                (status, error, progress_msg, now(), jid),
            )
            c.execute(
                "UPDATE versions SET status=?, publish_json=NULL WHERE artifact_id=? AND version=?",
                (status, j["artifact_id"], j["version"]),
            )
        self.progress.pop(jid, None)

    def _fail(self, jid: str, err: str) -> None:
        j = self.db.one("SELECT * FROM jobs WHERE id=?", jid)
        if j:
            self._set_job(jid, "failed", error=err[:2000])
            self.audit_by_client(
                j["client"], "build_failed", artifact_id=j["artifact_id"], version=j["version"], error=err[:300]
            )

    def _needs_input(self, jid: str, missing: str) -> None:
        j = self.db.one("SELECT * FROM jobs WHERE id=?", jid)
        if j:
            self._set_job(jid, "needs_input", error=missing[:2000], progress_msg="needs input")
            self.audit_by_client(
                j["client"],
                "build_needs_input",
                artifact_id=j["artifact_id"],
                version=j["version"],
                missing=missing[:300],
            )

    def _source_name(self, a: dict[str, Any], v: dict[str, Any]) -> str:
        files = [f["name"] for f in jloads(v["files_json"], [])]
        src = builder.source_name(a["kind"], version_format(v, a))
        return src if src in files else v["primary_file"]

    async def _run_job(self, jid: str) -> None:
        j = self.db.one("SELECT * FROM jobs WHERE id=?", jid)
        if not j or j["status"] not in ("queued", "building"):
            return
        a = self.db.one("SELECT * FROM artifacts WHERE id=?", j["artifact_id"])
        v = self.db.one("SELECT * FROM versions WHERE artifact_id=? AND version=?", j["artifact_id"], j["version"])
        if not a or not v:
            self._fail(jid, "artifact or version vanished")
            trial.settle(self.db, jid, [])
            return
        if a.get("deleting_at"):
            self._fail(jid, "artifact is being deleted")
            trial.settle(self.db, jid, [])
            return
        is_trial = self._job_is_trial(jid)
        with self.db.tx() as c:
            art = c.execute("SELECT deleting_at FROM artifacts WHERE id=?", (a["id"],)).fetchone()
            if art and art["deleting_at"]:
                c.execute(
                    "UPDATE jobs SET status='failed', error=?, finished_at=? WHERE id=?",
                    ("artifact is being deleted", now(), jid),
                )
                c.execute(
                    "UPDATE versions SET publish_json=NULL WHERE artifact_id=? AND version=?",
                    (a["id"], v["version"]),
                )
                trial.settle(self.db, jid, [])
                return
            claimed = c.execute(
                "UPDATE jobs SET status='building', started_at=? WHERE id=? AND status='queued'",
                (now(), jid),
            ).rowcount
            # Another worker already claimed this job (or it left queued). Do not build twice.
            if not claimed:
                return
            c.execute(
                "UPDATE versions SET status='building' WHERE artifact_id=? AND version=?", (a["id"], v["version"])
            )

        if is_trial and not CFG.trial_enabled:
            self._fail(jid, trial.CLOSED)
            trial.settle(self.db, jid, [])
            return
        cap_token = llm.set_output_cap(int(CFG.trial_max_output_tokens)) if is_trial else None
        usage_token = llm.begin_usage() if is_trial else None

        def progress(msg: str) -> None:
            self.progress[jid] = msg
            self.db.exec("UPDATE jobs SET progress=? WHERE id=?", msg, jid)

        try:
            await asyncio.wait_for(
                self._execute_build(jid, j, a, v, progress),
                timeout=CFG.build_timeout_s,
            )
        except TimeoutError:
            self._fail(jid, f"build timed out after {CFG.build_timeout_s}s")
        except builder.NeedsInput as e:
            self._needs_input(jid, str(e))
        except AMError as e:
            self._fail(jid, str(e))
        except builder.BuildError as exc:
            self._fail(jid, _job_check_error(str(exc)))
        except Exception as e:  # noqa: BLE001
            log.exception("build failed job=%s", jid)
            # Do not leak LLM/renderer/store internals to clients.
            self._fail(jid, f"build failed: {type(e).__name__}")
        finally:
            if usage_token is not None and cap_token is not None:
                trial.settle(self.db, jid, llm.end_usage(usage_token))
                llm.reset_output_cap(cap_token)

    async def _execute_build(
        self,
        jid: str,
        j: dict[str, Any],
        a: dict[str, Any],
        v: dict[str, Any],
        progress: Any,
    ) -> None:
        """Load sources, build, render, and upload under the caller's deadline."""
        fmt = version_format(v, a)
        if v.get("publish_json"):
            from .publishing import load_publish_payload

            payload = load_publish_payload(v["publish_json"])
            try:
                res = await builder.publish_page(
                    fmt=fmt,
                    display_name=a["display_name"],
                    html=payload["html"],
                    markdown=payload["markdown"],
                    summary=payload["summary"],
                    assumptions=payload["assumptions"],
                    render_timeout=CFG.render_timeout_s,
                    progress=progress,
                )
            except builder.BuildError as exc:
                raise AMError(str(exc)) from exc
        else:
            base_source = None
            base_fmt = fmt
            if v["base_version"]:
                bv = self.db.one("SELECT * FROM versions WHERE artifact_id=? AND version=?", a["id"], v["base_version"])
                if not bv:
                    raise AMError("base version vanished")
                base_fmt = version_format(bv, a)
                key = Store.prefix(a["workspace"], a["id"], bv["version"]) + self._source_name(a, bv)
                data, _ = await asyncio.to_thread(self.store.get, key)
                base_source = data.decode("utf-8", errors="replace")
            history_rows = self.db.all(
                "SELECT verbatim_request FROM versions WHERE artifact_id=? AND version<? AND status='done' "
                "ORDER BY version DESC LIMIT ?",
                a["id"],
                v["version"],
                HISTORY_CAP_ENTRIES,
            )
            history = list(reversed([r["verbatim_request"] for r in history_rows]))
            hist_chars = sum(len(h) for h in history)
            while history and hist_chars > HISTORY_CAP_CHARS:
                hist_chars -= len(history.pop(0))

            source = None
            if v.get("source_key"):
                raw, _ = await asyncio.to_thread(self.store.get, v["source_key"])
                source = json.loads(raw)
            build_mode = v.get("mode") or "full"
            if fmt != "html" or base_source is not None:
                build_mode = "full"
            res = await builder.run_build(
                kind=a["kind"],
                slug=a["slug"],
                display_name=a["display_name"],
                verbatim=v["verbatim_request"],
                model=CFG.trial_model if self._token_is_trial(str(j["client"])) else (v["model"] or CFG.default_model),
                base_source=base_source,
                base_version=v["base_version"],
                history=history,
                progress=progress,
                source=source,
                render_timeout=CFG.render_timeout_s,
                format=fmt,
                style=v.get("style") or DEFAULT_STYLE,
                mode=build_mode,
                patch=base_fmt == fmt,
            )
        total = sum(len(b) for b in res.files.values())
        if total > CFG.max_output_bytes:
            raise AMError(f"output {total} bytes exceeds cap")
        progress("uploading to object store")
        prefix = Store.prefix(a["workspace"], a["id"], v["version"])
        file_meta = []
        for name, data in res.files.items():
            ct = builder.MIME.get(name.rsplit(".", 1)[-1], "application/octet-stream")
            vid = await asyncio.to_thread(self.store.put, prefix + name, data, ct)
            file_meta.append(
                {
                    "name": name,
                    "sha256": builder.sha256(data),
                    "size": len(data),
                    "content_type": ct,
                    "s3_version_id": vid,
                }
            )
        primary_sha = builder.sha256(res.files[res.primary])
        current_name = str(a.get("display_name") or "")
        rename = resolved_display_name(
            str(v.get("requested_name") or "") or None,
            current_name,
            str(a.get("slug") or ""),
            res.files,
            res.primary,
            adopt_page_title=bool(v.get("base_version")),
        )
        shown_name = rename if rename and rename != current_name else current_name
        manifest = {
            "artifact_id": a["id"],
            "workspace": a["workspace"],
            "slug": a["slug"],
            "display_name": shown_name,
            "kind": a["kind"],
            "format": fmt,
            "version": v["version"],
            "base_version": v["base_version"],
            "verbatim_request": v["verbatim_request"],
            "builder_model": res.model,
            "primary_file": res.primary,
            "sha256": primary_sha,
            "files": file_meta,
            "assumptions": res.assumptions,
            "summary": res.summary,
            "created_by": v["created_by"],
            "created_at": iso(v["created_at"]),
            "built_at": iso(now()),
            "style": v.get("style") or DEFAULT_STYLE,
            "mode": v.get("mode") or "full",
            "notes": res.notes,
        }
        if res.model == builder.PUBLISHED_MODEL:
            manifest["safety_changes"] = list(res.safety_changes)
        await asyncio.to_thread(
            self.store.put, prefix + "manifest.json", json.dumps(manifest, indent=2).encode(), "application/json"
        )
        with self.db.tx() as c:
            c.execute(
                "UPDATE versions SET status='done', model=?, sha256=?, primary_file=?, files_json=?, "
                "assumptions_json=?, summary=?, size=?, publish_json=NULL WHERE artifact_id=? AND version=?",
                (
                    res.model,
                    primary_sha,
                    res.primary,
                    json.dumps(file_meta),
                    json.dumps(res.assumptions),
                    res.summary,
                    total,
                    a["id"],
                    v["version"],
                ),
            )
            c.execute("UPDATE jobs SET status='done', progress='done', finished_at=? WHERE id=?", (now(), jid))
            latest_row = c.execute(
                "SELECT MAX(version) AS version FROM versions WHERE artifact_id=? AND status='done'",
                (a["id"],),
            ).fetchone()
            # This version is already marked done above. A newer done version keeps the name it set.
            is_latest = latest_row is not None and int(latest_row["version"]) == int(v["version"])
            c.execute(
                "UPDATE artifacts SET display_name=?, format=?, updated_at=? WHERE id=?",
                (shown_name if is_latest else current_name, fmt, now(), a["id"]),
            )
        self.progress.pop(jid, None)
        self.audit_by_client(j["client"], "build_done", artifact_id=a["id"], version=v["version"], sha256=primary_sha)

    # ---------------- read paths ----------------
    def preview_link(self, a: dict[str, Any], version: int, ttl_s: int = 86400) -> str:
        r = self.db.one(
            "SELECT id FROM short_links WHERE artifact_id=? AND version=? AND expires_at>? "
            "ORDER BY expires_at DESC LIMIT 1",
            a["id"],
            version,
            now() + 3600,
        )
        if r:
            sid = r["id"]
        else:
            sid = shortid(10)
            self.db.exec("INSERT INTO short_links VALUES (?,?,?,?,?)", sid, a["id"], version, "preview", now() + ttl_s)
            self.db.exec("DELETE FROM short_links WHERE expires_at<?", now())
        return f"{CFG.preview_url}/p/{sid}"

    def short_lookup(self, sid: str) -> dict[str, Any] | None:
        return self.db.one("SELECT * FROM short_links WHERE id=? AND expires_at>?", sid, now())

    def revoke_previews(
        self, principal: dict[str, Any], artifact_id: str, version: int | None = None
    ) -> dict[str, Any]:
        """Expire private /p/ preview capabilities for an artifact (or one version)."""
        self.require(principal, "export")
        a = self.get_artifact(principal, artifact_id)
        t = now()
        with self.db.tx() as c:
            if version is None:
                n = c.execute(
                    "UPDATE short_links SET expires_at=? WHERE artifact_id=? AND expires_at>?",
                    (t, a["id"], t),
                ).rowcount
            else:
                n = c.execute(
                    "UPDATE short_links SET expires_at=? WHERE artifact_id=? AND version=? AND expires_at>?",
                    (t, a["id"], int(version), t),
                ).rowcount
        self.audit(principal, "revoke_previews", artifact_id=a["id"], version=version, revoked=n)
        return {"artifact_id": a["id"], "revoked_previews": n}

    def download_link(self, a: dict[str, Any], version: int, fname: str, as_name: str, ttl_s: int = 900) -> str:
        return f"{CFG.preview_url}/dl/{sign({'a': a['id'], 'v': version, 'f': fname, 'n': as_name}, ttl_s)}"

    def card(self, a: dict[str, Any], v: dict[str, Any]) -> dict[str, Any]:
        j = self.db.one("SELECT * FROM jobs WHERE id=?", v["job_id"]) or {}
        card = {
            "title": a["display_name"],
            "artifact_id": a["id"],
            "slug": a["slug"],
            "workspace": a["workspace"],
            "kind": a["kind"],
            "format": a["format"],
            "version": v["version"],
            "base_version": v["base_version"],
            "status": v["status"],
            "progress": self.progress.get(v["job_id"]) or j.get("progress"),
            "job_id": v["job_id"],
            "created_by": v["created_by"],
            "created_at": iso(v["created_at"]),
            "style": v.get("style") or DEFAULT_STYLE,
        }
        if v["status"] == "done":
            card.update(
                {
                    "preview_url": self.preview_link(a, v["version"]),
                    "preview_expires_in": "24h",
                    "size_bytes": v["size"],
                    "builder": v["model"],
                    "sha256": v["sha256"],
                    "primary_file": v["primary_file"],
                    "assumptions": jloads(v["assumptions_json"], []),
                    "summary": v["summary"],
                }
            )
        if v["status"] == "failed":
            card["error"] = j.get("error")
        if v["status"] == "needs_input":
            card["missing"] = j.get("error")
            card["next"] = (
                "supply the missing data as source_content, then call create again with the same slug "
                "(if no version was ever built) or edit from the latest done version"
            )
        if v.get("source_key"):
            card["has_source"] = True
        return card

    async def status(
        self, principal: dict[str, Any], artifact_id: str | None = None, job_id: str | None = None, wait: int = 0
    ) -> dict[str, Any]:
        self.require(principal, "read")
        if job_id:
            j = self.db.one("SELECT * FROM jobs WHERE id=?", job_id)
            if not j:
                raise AMError("job not found")
            a = self.get_artifact(principal, j["artifact_id"])
            version = j["version"]
        else:
            a = self.get_artifact(principal, artifact_id)
            version = self.db.must("SELECT MAX(version) m FROM versions WHERE artifact_id=?", a["id"])["m"]
        deadline = now() + max(0, min(int(wait or 0), 90))
        while True:
            v = self.db.one("SELECT * FROM versions WHERE artifact_id=? AND version=?", a["id"], version)
            if not v:
                raise AMError("version not found (deleted?)")
            if v["status"] in ("done", "failed", "needs_input") or now() >= deadline:
                break
            await asyncio.sleep(0.5)
        # The done transaction may have changed artifacts.format while this call waited.
        a = self.get_artifact(principal, a["id"])
        out = self.card(a, v)
        out["share"] = self._share_state(a["id"], version)
        if principal.get("trial"):
            out["builds_remaining"] = self._builds_remaining(principal)
        if v["status"] == "done" and v.get("model") == builder.PUBLISHED_MODEL:
            recorded = await asyncio.to_thread(self._manifest, a, v)
            if isinstance(recorded, dict) and "safety_changes" in recorded:
                out["safety_changes"] = recorded["safety_changes"]
            elif recorded is None:
                out["manifest_error"] = "store_read_failed"
        return out

    def _manifest(self, a: dict[str, Any], v: dict[str, Any]) -> dict[str, Any] | None:
        try:
            data, _ct = self.store.get(Store.prefix(a["workspace"], a["id"], int(v["version"])) + "manifest.json")
            loaded = json.loads(data)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "manifest read failed artifact=%s version=%s: %s: %s",
                a["id"],
                v["version"],
                type(e).__name__,
                e,
            )
            return None
        return loaded if isinstance(loaded, dict) else None

    def _share_state(self, aid: str, version: int) -> dict[str, Any]:
        floating = self.db.one(
            "SELECT * FROM shares WHERE artifact_id=? AND pinned=0 AND revoked_at IS NULL AND expires_at>? "
            "ORDER BY expires_at DESC LIMIT 1",
            aid,
            now(),
        )
        pinned = None
        if floating is None:
            pinned = self.db.one(
                "SELECT * FROM shares WHERE artifact_id=? AND version=? AND pinned=1 "
                "AND revoked_at IS NULL AND expires_at>? ORDER BY created_at DESC LIMIT 1",
                aid,
                version,
                now(),
            )
        s = floating or pinned
        if s:
            until = s["expires_at"] >= self.NEVER_EXPIRES - 1
            follows = "latest" if int(s["pinned"] or 0) == 0 else "pinned"
            return {
                "state": "shared",
                "url": self.share_url(s["id"]),
                "follows": follows,
                "expires_at": None if until else iso(s["expires_at"]),
                "lifetime": "until revoked" if until else iso(s["expires_at"]),
                "note": "public link; unshare revokes immediately",
            }
        return {"state": "not_shared"}

    def _remaining_share(self, aid: str, version: int | None) -> dict[str, Any]:
        """Share state after a revoke. Floating wins; otherwise any live pin."""
        state = self._share_state(aid, int(version) if version else 0)
        if state["state"] == "not_shared":
            other = self.db.one(
                "SELECT version FROM shares WHERE artifact_id=? AND pinned=1 AND revoked_at IS NULL "
                "AND expires_at>? ORDER BY created_at DESC LIMIT 1",
                aid,
                now(),
            )
            if other:
                state = self._share_state(aid, int(other["version"]))
        if state["state"] != "shared":
            return {"state": "not_shared"}
        return {"state": "shared", "url": state["url"], "follows": state["follows"]}

    def list(
        self,
        principal: dict[str, Any],
        workspace: str | None = None,
        kind: str | None = None,
        query: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        self.require(principal, "read")
        ws = workspace or principal["workspace"]
        self.check_access(principal, ws)
        sql = "SELECT * FROM artifacts WHERE workspace=?"
        args: list[Any] = [ws]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        if query:
            sql += " AND (slug LIKE ? OR display_name LIKE ?)"
            args += [f"%{query}%", f"%{query}%"]
        sql += " ORDER BY updated_at DESC LIMIT ?"
        args.append(max(1, min(int(limit), 200)))
        items = []
        for a in self.db.all(sql, *args):
            vs = self.db.all("SELECT version,status FROM versions WHERE artifact_id=? ORDER BY version", a["id"])
            done = [x["version"] for x in vs if x["status"] == "done"]
            items.append(
                {
                    "artifact_id": a["id"],
                    "slug": a["slug"],
                    "title": a["display_name"],
                    "kind": a["kind"],
                    "format": a["format"],
                    "latest_version": vs[-1]["version"] if vs else None,
                    "latest_status": vs[-1]["status"] if vs else None,
                    "latest_done_version": done[-1] if done else None,
                    "created_by": a["created_by"],
                    "updated_at": iso(a["updated_at"]),
                }
            )
        return {"workspace": ws, "count": len(items), "artifacts": items}

    def inspect(
        self,
        principal: dict[str, Any],
        artifact_id: str,
        version: int | None = None,
        include_content: bool = False,
    ) -> dict[str, Any]:
        self.require(principal, "read")
        a = self.get_artifact(principal, artifact_id)
        vs = self.db.all("SELECT * FROM versions WHERE artifact_id=? ORDER BY version", a["id"])
        history = []
        for x in vs:
            row = {
                "version": x["version"],
                "base_version": x["base_version"],
                "status": x["status"],
                "verbatim_request": x["verbatim_request"],
                "created_by": x["created_by"],
                "created_at": iso(x["created_at"]),
                "model": x["model"],
                "sha256": x["sha256"],
                "style": x.get("style") or DEFAULT_STYLE,
                "mode": x.get("mode") or "full",
                "format": version_format(x, a),
            }
            stored_note = (x.get("note") or "").strip()
            if stored_note:
                row["note"] = stored_note
            history.append(row)
        target = version or next((x["version"] for x in reversed(vs) if x["status"] == "done"), None)
        out = {
            "artifact_id": a["id"],
            "slug": a["slug"],
            "workspace": a["workspace"],
            "title": a["display_name"],
            "kind": a["kind"],
            "format": a["format"],
            "created_by": a["created_by"],
            "history": history,
        }
        if target:
            v = next((x for x in vs if x["version"] == int(target)), None)
            if not v:
                raise AMError("version not found")
            j = self.db.one("SELECT * FROM jobs WHERE id=?", v["job_id"]) or {}
            out["version"] = int(target)
            out["style"] = v.get("style") or DEFAULT_STYLE
            out["build_log"] = {"status": j.get("status"), "progress": j.get("progress"), "error": j.get("error")}
            if v["status"] == "done":
                try:
                    data, _ = self.store.get(Store.prefix(a["workspace"], a["id"], int(target)) + "manifest.json")
                    out["manifest"] = json.loads(data)
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "inspect manifest read failed artifact=%s version=%s: %s: %s",
                        a["id"],
                        target,
                        type(e).__name__,
                        e,
                    )
                    out["manifest_error"] = "store_read_failed"
                out["card"] = self.card(a, v)
                recorded = out.get("manifest")
                if isinstance(recorded, dict) and "safety_changes" in recorded:
                    out["safety_changes"] = recorded["safety_changes"]
            out["share"] = self._share_state(a["id"], int(target))
            if include_content and v["status"] == "done":
                self._attach_inspected_content(out, a, v)
        return out

    def _attach_inspected_content(self, out: dict[str, Any], a: dict[str, Any], v: dict[str, Any]) -> None:
        """Read the target version's text after check_access. Never the binary primary."""
        from .renderers.safety import CONTENT_MAX_CHARS

        fmt = version_format(v, a)
        binary = fmt in {"pdf", "docx", "xlsx"}
        name = builder.source_name(a["kind"], fmt) if binary else str(v.get("primary_file") or "")
        if binary:
            names = {item.get("name") for item in jloads(v.get("files_json"), [])}
            if name not in names:
                out["content"] = {"file": "content.md", "primary_binary": True}
                return
        if not name:
            return
        key = Store.prefix(a["workspace"], a["id"], int(v["version"])) + name
        try:
            data, _ct = self.store.get(key)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "inspect content read failed artifact=%s version=%s: %s: %s",
                a["id"],
                v["version"],
                type(e).__name__,
                e,
            )
            out["content_error"] = "store_read_failed"
            return
        content: dict[str, Any] = {"file": name, "size_bytes": len(data)}
        if binary:
            content["primary_binary"] = True
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            out["content"] = content
            return
        if len(text) <= CONTENT_MAX_CHARS:
            content["text"] = text
        out["content"] = content

    def _done_version(self, a: dict[str, Any], version: int | None) -> dict[str, Any]:
        if version:
            v = self.db.one("SELECT * FROM versions WHERE artifact_id=? AND version=?", a["id"], int(version))
        else:
            v = self.db.one(
                "SELECT * FROM versions WHERE artifact_id=? AND status='done' ORDER BY version DESC LIMIT 1", a["id"]
            )
        if not v:
            raise AMError("version not found")
        if v["status"] != "done":
            raise AMError(f"v{v['version']} is {v['status']}, not done")
        return v

    def export(self, principal: dict[str, Any], artifact_id: str, version: int | None = None) -> dict[str, Any]:
        self.require(principal, "export")
        a = self.get_artifact(principal, artifact_id)
        v = self._done_version(a, version)
        primary = v["primary_file"]
        ext = EXTENSION.get(version_format(v, a), "html")
        as_name = f"{a['slug']}-v{v['version']:03d}.{ext}"
        self.audit(principal, "export", artifact_id=a["id"], version=v["version"])
        return {
            "artifact_id": a["id"],
            "version": v["version"],
            "file": as_name,
            "sha256": v["sha256"],
            "self_contained": True,
            "download_url": self.download_link(a, v["version"], primary, as_name),
            "expires_in": "15m",
        }

    # ---------------- share / unshare ----------------
    def share_url(self, sid: str) -> str:
        return f"{CFG.share_url}/s/{sid}/"

    def _share_exp(self, t: float, ttl_days: int) -> float:
        policy = int(CFG.share_ttl_days)
        if policy == 0:
            return self.NEVER_EXPIRES
        cap = max(1, int(CFG.share_ttl_max_days or 365))
        days = max(1, min(int(ttl_days or policy), cap))
        return t + days * 86400

    def share(
        self,
        principal: dict[str, Any],
        artifact_id: str,
        version: int | None = None,
        ttl_days: int | None = None,
        pin: bool = False,
    ) -> dict[str, Any]:
        """Publish a public link. Omit version to follow the latest done version.

        create never does this. unshare revokes at once. Passing version, or pin,
        freezes that one done version on its own link.
        """
        self.require(principal, "share")
        a = self.get_artifact(principal, artifact_id)
        pinned = bool(pin) or version is not None
        v = self._done_version(a, version)
        if not v.get("sha256"):
            raise AMError("version has no sha256")
        window = now() + 86400
        if pinned:
            s = self.db.one(
                "SELECT * FROM shares WHERE artifact_id=? AND version=? AND pinned=1 "
                "AND revoked_at IS NULL AND expires_at>? ORDER BY expires_at DESC LIMIT 1",
                a["id"],
                v["version"],
                window,
            )
        else:
            s = self.db.one(
                "SELECT * FROM shares WHERE artifact_id=? AND pinned=0 AND revoked_at IS NULL AND expires_at>? "
                "ORDER BY expires_at DESC LIMIT 1",
                a["id"],
                window,
            )
        if s:
            sid, exp, reused = s["id"], s["expires_at"], True
        else:
            sid = shortid(16)
            t = now()
            exp = self._share_exp(t, ttl_days if ttl_days is not None else CFG.share_ttl_days)
            if principal.get("trial"):
                exp = min(exp, t + trial.SHARE_DAYS * 86400)
            self.db.exec(
                "INSERT INTO shares (id, artifact_id, version, sha256, created_by, created_at, expires_at, "
                "revoked_at, pinned) VALUES (?,?,?,?,?,?,?,NULL,?)",
                sid,
                a["id"],
                v["version"],
                v["sha256"],
                principal["name"],
                t,
                exp,
                1 if pinned else 0,
            )
            reused = False
            self.audit(
                principal,
                "share_public",
                artifact_id=a["id"],
                version=v["version"],
                share_id=sid,
                pinned=pinned,
            )
        until = exp >= self.NEVER_EXPIRES - 1
        return {
            "state": "shared",
            "scope": "public internet link (anyone with the link)",
            "artifact_id": a["id"],
            "version": v["version"],
            "follows": "pinned" if pinned else "latest",
            "url": self.share_url(sid),
            "expires_at": None if until else iso(exp),
            "lifetime": "until revoked" if until else iso(exp),
            "reused": reused,
            "revoke": "unshare(artifact_id) takes effect immediately",
        }

    def share_target(self, row: dict[str, Any]) -> tuple[int, str] | None:
        """Version and recorded sha256 a public share id should serve. None is a 404.

        A pin uses the hash stored on the share row. A floating link uses the
        latest done version's hash, not the hash copied when the row was created.
        """
        if int(row.get("pinned") or 0):
            sha = str(row.get("sha256") or "")
            if not sha:
                return None
            return int(row["version"]), sha
        latest = self.db.one(
            "SELECT version, sha256 FROM versions WHERE artifact_id=? AND status='done' ORDER BY version DESC LIMIT 1",
            row["artifact_id"],
        )
        if not latest or not latest.get("sha256"):
            return None
        return int(latest["version"]), str(latest["sha256"])

    def unshare(self, principal: dict[str, Any], artifact_id: str, version: int | None = None) -> dict[str, Any]:
        self.require(principal, "share")
        a = self.get_artifact(principal, artifact_id)
        t = now()
        with self.db.tx() as c:
            if version:
                n = c.execute(
                    "UPDATE shares SET revoked_at=? WHERE artifact_id=? AND version=? AND pinned=1 "
                    "AND revoked_at IS NULL",
                    (t, a["id"], int(version)),
                ).rowcount
            else:
                n = c.execute(
                    "UPDATE shares SET revoked_at=? WHERE artifact_id=? AND revoked_at IS NULL", (t, a["id"])
                ).rowcount
        self.audit(principal, "unshare", artifact_id=a["id"], version=version, revoked=n)
        return {"artifact_id": a["id"], "revoked_links": n, **self._remaining_share(a["id"], version)}

    # ---------------- delete ----------------
    def delete(self, principal: dict[str, Any], artifact_id: str, confirm_token: str | None = None) -> dict[str, Any]:
        self.require(principal, "delete")
        a = self.get_artifact(principal, artifact_id)
        if not confirm_token:
            tok = secrets.token_urlsafe(12)
            self.db.exec("INSERT INTO delete_tokens VALUES (?,?,?,?)", tok, a["id"], principal["id"], now() + 600)
            nv = self.db.must("SELECT COUNT(*) n FROM versions WHERE artifact_id=?", a["id"])["n"]
            return {
                "step": "confirm",
                "artifact_id": a["id"],
                "title": a["display_name"],
                "versions": nv,
                "confirm_token": tok,
                "expires_in": "10m",
                "warning": "Permanent: purges every stored version, all share links and the DB rows. "
                "Call delete again with this confirm_token to proceed.",
            }
        t = self.db.one("SELECT * FROM delete_tokens WHERE token=?", confirm_token)
        if not t or t["artifact_id"] != a["id"] or t["client"] != principal["id"] or t["expires_at"] < now():
            raise AMError("invalid or expired confirm_token")
        with self.db.tx() as c:
            row = c.execute("SELECT deleting_at FROM artifacts WHERE id=?", (a["id"],)).fetchone()
            if not row:
                raise AMError("artifact not found")
            if row["deleting_at"]:
                # Prior attempt left the artifact marked; allow retry to finish purge + row removal.
                pass
            else:
                busy = c.execute(
                    "SELECT 1 FROM jobs WHERE artifact_id=? AND status='building'",
                    (a["id"],),
                ).fetchone()
                if busy:
                    raise AMError("a build is still running; wait for it to finish, then delete")
                c.execute(
                    "UPDATE jobs SET status='cancelled', error=?, finished_at=? "
                    "WHERE artifact_id=? AND status='queued'",
                    ("cancelled: artifact deleting", now(), a["id"]),
                )
                c.execute(
                    "UPDATE versions SET status='cancelled' WHERE artifact_id=? AND status='queued'",
                    (a["id"],),
                )
                c.execute("UPDATE artifacts SET deleting_at=? WHERE id=?", (now(), a["id"]))
        purged = self.store.purge_prefix(Store.prefix(a["workspace"], a["id"]))
        with self.db.tx() as c:
            busy = c.execute(
                "SELECT 1 FROM jobs WHERE artifact_id=? AND status='building'",
                (a["id"],),
            ).fetchone()
            if busy:
                raise AMError("a build is still running during delete; retry after it finishes")
            c.execute("UPDATE shares SET revoked_at=? WHERE artifact_id=? AND revoked_at IS NULL", (now(), a["id"]))
            for tbl in ("versions", "jobs", "shares", "delete_tokens", "short_links"):
                c.execute(f"DELETE FROM {tbl} WHERE artifact_id=?", (a["id"],))
            for row in c.execute("SELECT client, key, result_json FROM idempotency").fetchall():
                try:
                    payload = json.loads(row["result_json"])
                except (TypeError, json.JSONDecodeError):
                    continue
                body = payload.get("body") if isinstance(payload, dict) else None
                aid_hit: str | None = None
                if isinstance(body, dict):
                    raw_aid = body.get("artifact_id")
                    aid_hit = raw_aid if isinstance(raw_aid, str) else None
                elif isinstance(payload, dict):
                    raw_aid = payload.get("artifact_id")
                    aid_hit = raw_aid if isinstance(raw_aid, str) else None
                if aid_hit == a["id"]:
                    c.execute("DELETE FROM idempotency WHERE client=? AND key=?", (row["client"], row["key"]))
            c.execute("DELETE FROM artifacts WHERE id=?", (a["id"],))
        self.audit(principal, "delete", artifact_id=a["id"], slug=a["slug"], purged_objects=purged)
        return {"deleted": True, "artifact_id": a["id"], "purged_object_versions": purged}

    # ---------------- object access for HTTP routes ----------------
    def read_file(self, aid: str, version: int, fname: str | None) -> tuple[bytes, str, str]:
        data, ct, name, _title, _style = self._read_stored(aid, version, fname)
        return data, ct, name

    def read_primary_view(self, aid: str, version: int) -> tuple[bytes, str, str, str, str]:
        """Stored primary plus the title and style already loaded with that row."""
        return self._read_stored(aid, version, None)

    def _read_stored(self, aid: str, version: int, fname: str | None) -> tuple[bytes, str, str, str, str]:
        a = self.db.one("SELECT * FROM artifacts WHERE id=?", aid)
        v = (
            self.db.one("SELECT * FROM versions WHERE artifact_id=? AND version=? AND status='done'", aid, version)
            if a
            else None
        )
        if not a or not v:
            raise AMError("not found")
        names = {f["name"]: f for f in jloads(v["files_json"], [])}
        chosen = fname or v["primary_file"]
        if chosen not in names:
            raise AMError("not found")
        data, ct = self.store.get(Store.prefix(a["workspace"], aid, version) + chosen)
        title = str(a.get("display_name") or "Page")
        style = str(v.get("style") or DEFAULT_STYLE)
        return data, names[chosen]["content_type"] or ct, chosen, title, style

    def share_lookup(self, sid: str) -> dict[str, Any] | None:
        return self.db.one("SELECT * FROM shares WHERE id=? AND revoked_at IS NULL AND expires_at>?", sid, now())

    # ---------------- audit ----------------
    def audit(self, principal: dict[str, Any], action: str, **fields: Any) -> None:
        self._audit(principal["name"], action, **fields)

    def audit_by_client(self, client_id: str, action: str, **fields: Any) -> None:
        tok = self.db.one("SELECT name FROM tokens WHERE id=?", client_id)
        self._audit(tok["name"] if tok else client_id, action, **fields)

    def _audit(self, who: str, action: str, **fields: Any) -> None:
        write_audit(who, action, **fields)
