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
from typing import Any

from . import builder
from .config import CFG
from .db import DB, jloads
from .renderers import EXTENSION, SUPPORTED_FORMATS
from .renderers.safety import strip_ooxml_controls
from .store import Store

log = logging.getLogger("artifactsmith.service")

ALL_PERMS = {"create", "read", "edit", "export", "share", "delete"}

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
WS_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
SOURCE_CAP_BYTES = 200 * 1024
VERBATIM_CAP_CHARS = 32_000
DISPLAY_NAME_CAP_CHARS = 200
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


def now() -> float:
    return time.time()


def iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds") if ts else None


def rid(prefix: str, n: int = 10) -> str:
    return prefix + base64.b32encode(secrets.token_bytes(n)).decode().lower().rstrip("=")[: n + 4]


def shortid(n: int = 12) -> str:
    return "".join(secrets.choice("abcdefghijkmnpqrstuvwxyz23456789") for _ in range(n))


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


class Service:
    # Far-future stamp used when AM_SHARE_TTL_DAYS=0 ("until revoked"). Still a real timestamp so queries work.
    NEVER_EXPIRES = 4102444800.0  # 2100-01-01 UTC

    def __init__(self) -> None:
        self.db = DB(CFG.db_path)
        self.store = Store()
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.progress: dict[str, str] = {}
        self._workers: list[asyncio.Task[None]] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def _enqueue(self, jid: str) -> None:
        """Wake a worker from any thread. asyncio.Queue is not thread-safe for put_nowait."""
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self.queue.put_nowait, jid)
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None:
            running.call_soon_threadsafe(self.queue.put_nowait, jid)
        else:
            self.queue.put_nowait(jid)

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
        n = c.execute(
            "SELECT COUNT(*) n FROM jobs WHERE client=? AND created_at>?",
            (principal["id"], now() - 3600),
        ).fetchone()["n"]
        if n >= CFG.builds_per_hour:
            raise AMError(f"quota: {CFG.builds_per_hour} builds/hour reached")

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

    @staticmethod
    def _model(model: str | None) -> str:
        m = (model or "").strip() or CFG.default_model
        if not MODEL_RE.match(m):
            raise AMError("model must be a plain model name")
        return m

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
        model = self._model(model)
        verbatim_request = self._validate_verbatim(verbatim_request)
        display_name = self._validate_display_name(title, slug)
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        caps = capabilities or {}
        if not isinstance(caps, dict):
            raise AMError("capabilities must be an object")
        if caps.get("web") or caps.get("connectors"):
            raise AMError("capabilities web/connectors are not available yet")
        bundle = self._source(source_content, source_files)
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
            }
        )
        prior = self._idem_lookup(principal, idempotency_key, fingerprint)
        if prior:
            return dict(prior, idempotent_replay=True)
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
                        return dict(data["body"], idempotent_replay=True)
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
                "source_key,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (aid, newv, None, verbatim_request, "queued", jid, model, skey, principal["name"], t),
            )
            c.execute(
                "INSERT INTO jobs (id,artifact_id,version,client,status,created_at) VALUES (?,?,?,?,?,?)",
                (jid, aid, newv, principal["id"], "queued", t),
            )
            if idempotency_key:
                c.execute(
                    "INSERT INTO idempotency VALUES (?,?,?,?)",
                    (principal["id"], idempotency_key, self._idem_payload(fingerprint, result), t),
                )
        self.audit(principal, "create", artifact_id=aid, slug=slug, kind=kind, model=model)
        self._enqueue(jid)
        return result

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
    ) -> dict[str, Any]:
        self.require(principal, "edit")
        a = self.get_artifact(principal, artifact_id)
        if workspace is not None and workspace != a["workspace"]:
            raise AMError("workspace does not match artifact")
        if a.get("deleting_at"):
            raise AMError("artifact is being deleted")
        verbatim_request = self._validate_verbatim(verbatim_request)
        model = self._model(model)
        idempotency_key = self._validate_idempotency_key(idempotency_key)
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
            }
        )
        prior = self._idem_lookup(principal, idempotency_key, fingerprint)
        if prior:
            return dict(prior, idempotent_replay=True)
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
                        return dict(data["body"], idempotent_replay=True)
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
                "source_key,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (a["id"], newv, int(base_version), verbatim_request, "queued", jid, model, skey, principal["name"], t),
            )
            c.execute(
                "INSERT INTO jobs (id,artifact_id,version,client,status,created_at) VALUES (?,?,?,?,?,?)",
                (jid, a["id"], newv, principal["id"], "queued", t),
            )
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
            }
            if idempotency_key:
                c.execute(
                    "INSERT INTO idempotency VALUES (?,?,?,?)",
                    (principal["id"], idempotency_key, self._idem_payload(fingerprint, result), t),
                )
        self.audit(principal, "edit", artifact_id=a["id"], version=newv, base_version=base_version, model=model)
        self._enqueue(jid)
        return result

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
        for _ in range(CFG.max_concurrent_builds):
            self._workers.append(asyncio.create_task(self._worker()))

    async def shutdown(self) -> None:
        """Cancel workers and close the database. Safe to call more than once."""
        for task in list(self._workers):
            task.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self.db.close()

    async def _worker(self) -> None:
        while True:
            jid = await self.queue.get()
            try:
                await self._run_job(jid)
            except Exception as e:  # noqa: BLE001
                log.exception("worker failed job=%s", jid)
                self._fail(jid, f"internal error: {type(e).__name__}")
            finally:
                self.queue.task_done()

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
                "UPDATE versions SET status=? WHERE artifact_id=? AND version=?",
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
        src = builder.source_name(a["kind"], a["format"])
        return src if src in files else v["primary_file"]

    async def _run_job(self, jid: str) -> None:
        j = self.db.one("SELECT * FROM jobs WHERE id=?", jid)
        if not j or j["status"] not in ("queued", "building"):
            return
        a = self.db.one("SELECT * FROM artifacts WHERE id=?", j["artifact_id"])
        v = self.db.one("SELECT * FROM versions WHERE artifact_id=? AND version=?", j["artifact_id"], j["version"])
        if not a or not v:
            self._fail(jid, "artifact or version vanished")
            return
        if a.get("deleting_at"):
            self._fail(jid, "artifact is being deleted")
            return
        with self.db.tx() as c:
            art = c.execute("SELECT deleting_at FROM artifacts WHERE id=?", (a["id"],)).fetchone()
            if art and art["deleting_at"]:
                c.execute(
                    "UPDATE jobs SET status='failed', error=?, finished_at=? WHERE id=?",
                    ("artifact is being deleted", now(), jid),
                )
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
        except Exception as e:  # noqa: BLE001
            log.exception("build failed job=%s", jid)
            # Do not leak LLM/renderer/store internals to clients.
            self._fail(jid, f"build failed: {type(e).__name__}")

    async def _execute_build(
        self,
        jid: str,
        j: dict[str, Any],
        a: dict[str, Any],
        v: dict[str, Any],
        progress: Any,
    ) -> None:
        """Load sources, build, render, and upload under the caller's deadline."""
        base_source = None
        if v["base_version"]:
            bv = self.db.one("SELECT * FROM versions WHERE artifact_id=? AND version=?", a["id"], v["base_version"])
            if not bv:
                raise AMError("base version vanished")
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
        res = await builder.run_build(
            kind=a["kind"],
            slug=a["slug"],
            display_name=a["display_name"],
            verbatim=v["verbatim_request"],
            model=v["model"] or CFG.default_model,
            base_source=base_source,
            base_version=v["base_version"],
            history=history,
            progress=progress,
            source=source,
            render_timeout=CFG.render_timeout_s,
            format=a["format"] or "html",
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
        manifest = {
            "artifact_id": a["id"],
            "workspace": a["workspace"],
            "slug": a["slug"],
            "display_name": a["display_name"],
            "kind": a["kind"],
            "format": a["format"],
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
        }
        await asyncio.to_thread(
            self.store.put, prefix + "manifest.json", json.dumps(manifest, indent=2).encode(), "application/json"
        )
        with self.db.tx() as c:
            c.execute(
                "UPDATE versions SET status='done', model=?, sha256=?, primary_file=?, files_json=?, "
                "assumptions_json=?, summary=?, size=? WHERE artifact_id=? AND version=?",
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
            c.execute("UPDATE artifacts SET updated_at=? WHERE id=?", (now(), a["id"]))
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
        out = self.card(a, v)
        out["share"] = self._share_state(a["id"], version)
        return out

    def _share_state(self, aid: str, version: int) -> dict[str, Any]:
        s = self.db.one(
            "SELECT * FROM shares WHERE artifact_id=? AND version=? AND revoked_at IS NULL AND expires_at>? "
            "ORDER BY created_at DESC LIMIT 1",
            aid,
            version,
            now(),
        )
        if s:
            until = s["expires_at"] >= self.NEVER_EXPIRES - 1
            return {
                "state": "shared",
                "url": self.share_url(s["id"]),
                "expires_at": None if until else iso(s["expires_at"]),
                "lifetime": "until revoked" if until else iso(s["expires_at"]),
                "note": "public link; unshare revokes immediately",
            }
        return {"state": "not_shared"}

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

    def inspect(self, principal: dict[str, Any], artifact_id: str, version: int | None = None) -> dict[str, Any]:
        self.require(principal, "read")
        a = self.get_artifact(principal, artifact_id)
        vs = self.db.all("SELECT * FROM versions WHERE artifact_id=? ORDER BY version", a["id"])
        history = [
            {
                "version": x["version"],
                "base_version": x["base_version"],
                "status": x["status"],
                "verbatim_request": x["verbatim_request"],
                "created_by": x["created_by"],
                "created_at": iso(x["created_at"]),
                "model": x["model"],
                "sha256": x["sha256"],
            }
            for x in vs
        ]
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
            out["share"] = self._share_state(a["id"], int(target))
        return out

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
        ext = EXTENSION.get(a["format"] or "html", "html")
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
        self, principal: dict[str, Any], artifact_id: str, version: int | None = None, ttl_days: int | None = None
    ) -> dict[str, Any]:
        """Publish a public link for one exact version. create never does this. unshare revokes at once."""
        self.require(principal, "share")
        a = self.get_artifact(principal, artifact_id)
        v = self._done_version(a, version)
        s = self.db.one(
            "SELECT * FROM shares WHERE artifact_id=? AND version=? AND revoked_at IS NULL AND expires_at>? "
            "ORDER BY expires_at DESC LIMIT 1",
            a["id"],
            v["version"],
            now() + 86400,
        )
        if s:
            sid, exp, reused = s["id"], s["expires_at"], True
        else:
            sid = shortid(16)
            t = now()
            exp = self._share_exp(t, ttl_days if ttl_days is not None else CFG.share_ttl_days)
            self.db.exec(
                "INSERT INTO shares VALUES (?,?,?,?,?,?,?,NULL)",
                sid,
                a["id"],
                v["version"],
                v["sha256"],
                principal["name"],
                t,
                exp,
            )
            reused = False
            self.audit(principal, "share_public", artifact_id=a["id"], version=v["version"], share_id=sid)
        until = exp >= self.NEVER_EXPIRES - 1
        return {
            "state": "shared",
            "scope": "public internet link (anyone with the link)",
            "artifact_id": a["id"],
            "version": v["version"],
            "url": self.share_url(sid),
            "expires_at": None if until else iso(exp),
            "lifetime": "until revoked" if until else iso(exp),
            "reused": reused,
            "revoke": "unshare(artifact_id) takes effect immediately",
        }

    def unshare(self, principal: dict[str, Any], artifact_id: str, version: int | None = None) -> dict[str, Any]:
        self.require(principal, "share")
        a = self.get_artifact(principal, artifact_id)
        t = now()
        with self.db.tx() as c:
            if version:
                n = c.execute(
                    "UPDATE shares SET revoked_at=? WHERE artifact_id=? AND version=? AND revoked_at IS NULL",
                    (t, a["id"], int(version)),
                ).rowcount
            else:
                n = c.execute(
                    "UPDATE shares SET revoked_at=? WHERE artifact_id=? AND revoked_at IS NULL", (t, a["id"])
                ).rowcount
        self.audit(principal, "unshare", artifact_id=a["id"], version=version, revoked=n)
        return {"artifact_id": a["id"], "revoked_links": n, "state": "not_shared"}

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
        a = self.db.one("SELECT * FROM artifacts WHERE id=?", aid)
        v = (
            self.db.one("SELECT * FROM versions WHERE artifact_id=? AND version=? AND status='done'", aid, version)
            if a
            else None
        )
        if not a or not v:
            raise AMError("not found")
        names = {f["name"]: f for f in jloads(v["files_json"], [])}
        fname = fname or v["primary_file"]
        if fname not in names:
            raise AMError("not found")
        data, ct = self.store.get(Store.prefix(a["workspace"], aid, version) + fname)
        return data, names[fname]["content_type"] or ct, fname

    def share_lookup(self, sid: str) -> dict[str, Any] | None:
        return self.db.one("SELECT * FROM shares WHERE id=? AND revoked_at IS NULL AND expires_at>?", sid, now())

    # ---------------- audit ----------------
    def audit(self, principal: dict[str, Any], action: str, **fields: Any) -> None:
        self._audit(principal["name"], action, **fields)

    def audit_by_client(self, client_id: str, action: str, **fields: Any) -> None:
        tok = self.db.one("SELECT name FROM tokens WHERE id=?", client_id)
        self._audit(tok["name"] if tok else client_id, action, **fields)

    def _audit(self, who: str, action: str, **fields: Any) -> None:
        rec = {"ts": iso(now()), "actor": who, "action": action, **fields}
        try:
            CFG.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with open(CFG.audit_path, "a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        except OSError:
            pass
