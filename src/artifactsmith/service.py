"""Core operations shared by the MCP tools, the HTTP routes and the CLI."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import datetime, timezone

from . import builder
from .config import CFG
from .db import DB, jloads
from .store import Store

ALL_PERMS = {"create", "read", "edit", "export", "share", "delete"}

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
WS_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
SOURCE_CAP_BYTES = 200 * 1024
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
KINDS = {"web_static", "file"}


class AMError(Exception):
    """User-facing error (the message is safe to return to callers)."""


def now() -> float:
    return time.time()


def iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


def rid(prefix: str, n: int = 10) -> str:
    return prefix + base64.b32encode(secrets.token_bytes(n)).decode().lower().rstrip("=")[:n + 4]


def shortid(n: int = 12) -> str:
    return "".join(secrets.choice("abcdefghijkmnpqrstuvwxyz23456789") for _ in range(n))


# ---------- signed short-lived tokens for private preview / download ----------
def sign(payload: dict, ttl_s: int) -> str:
    payload = dict(payload, exp=int(now() + ttl_s))
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    mac = hmac.new(CFG.signing_key(), body.encode(), hashlib.sha256).digest()[:16]
    return body + "." + base64.urlsafe_b64encode(mac).decode().rstrip("=")


def unsign(token: str) -> dict | None:
    try:
        body, mac = token.split(".", 1)
        want = base64.urlsafe_b64encode(
            hmac.new(CFG.signing_key(), body.encode(), hashlib.sha256).digest()[:16]).decode().rstrip("=")
        if not hmac.compare_digest(want, mac):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if payload.get("exp", 0) < now():
            return None
        return payload
    except Exception:
        return None


class Service:
    # Far-future stamp used when AM_SHARE_TTL_DAYS=0 ("until revoked"). Still a real timestamp so queries work.
    NEVER_EXPIRES = 4102444800.0  # 2100-01-01 UTC

    def __init__(self):
        self.db = DB(CFG.db_path)
        self.store = Store()
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.progress: dict[str, str] = {}
        self._workers: list[asyncio.Task] = []

    # ---------------- access ----------------
    @staticmethod
    def require(principal: dict, perm: str) -> None:
        if perm not in principal["perms"]:
            raise AMError(f"token '{principal['name']}' is not permitted to {perm}")

    @staticmethod
    def check_access(principal: dict, workspace: str) -> None:
        """A token reaches only its own workspace. Knowing an id is not access."""
        if workspace != principal["workspace"]:
            raise AMError("forbidden: the artifact belongs to a different workspace")

    def get_artifact(self, principal: dict, artifact_id: str | None = None, slug: str | None = None,
                     workspace: str | None = None) -> dict:
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

    def _idem_get(self, principal: dict, key: str | None) -> dict | None:
        if not key:
            return None
        r = self.db.one("SELECT result_json FROM idempotency WHERE client=? AND key=?", principal["id"], key)
        return json.loads(r["result_json"]) if r else None

    def _quota(self, principal: dict) -> None:
        n = self.db.one("SELECT COUNT(*) n FROM jobs WHERE client=? AND created_at>?", principal["id"],
                        now() - 3600)["n"]
        if n >= CFG.builds_per_hour:
            raise AMError(f"quota: {CFG.builds_per_hour} builds/hour reached")

    @staticmethod
    def _model(model: str | None) -> str:
        m = (model or "").strip() or CFG.default_model
        if not MODEL_RE.match(m):
            raise AMError("model must be a plain model name")
        return m

    @staticmethod
    def _source(source_content: str | None, source_files: list | None) -> dict | None:
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

    def _put_source(self, workspace: str, aid: str, version: int, bundle: dict | None) -> str | None:
        if not bundle:
            return None
        key = Store.prefix(workspace, aid, version) + "_input/source.json"
        self.store.put(key, json.dumps(bundle).encode(), "application/json")
        return key

    # ---------------- create ----------------
    def create(self, principal: dict, *, slug: str, display_name: str, kind: str, verbatim_request: str,
               format: str | None = None, workspace: str | None = None, model: str | None = None,
               source_content: str | None = None, source_files: list | None = None,
               capabilities: dict | None = None, idempotency_key: str | None = None) -> dict:
        self.require(principal, "create")
        prior = self._idem_get(principal, idempotency_key)
        if prior:
            return dict(prior, idempotent_replay=True)
        ws = workspace or principal["workspace"]
        if not WS_RE.match(ws or ""):
            raise AMError("invalid workspace name")
        self.check_access(principal, ws)
        if kind not in KINDS:
            raise AMError(f"kind must be one of {sorted(KINDS)}")
        if kind == "file":
            raise AMError("only kind=web_static (a single self-contained HTML page) is available in this build")
        format = "html"
        if not SLUG_RE.match(slug or ""):
            raise AMError("slug must be 2-63 chars of a-z, 0-9 and '-'")
        model = self._model(model)
        if not (verbatim_request or "").strip():
            raise AMError("verbatim_request is required")
        caps = capabilities or {}
        if not isinstance(caps, dict):
            raise AMError("capabilities must be an object")
        if caps.get("web") or caps.get("connectors"):
            raise AMError("capabilities web/connectors are not available yet")
        bundle = self._source(source_content, source_files)
        self._quota(principal)
        aid, jid, t = rid("art_"), rid("job_"), now()
        prev = self.db.one("SELECT * FROM artifacts WHERE workspace=? AND slug=?", ws, slug)
        newv = 1
        if prev:
            st = {r["status"] for r in self.db.all("SELECT status FROM versions WHERE artifact_id=?", prev["id"])}
            if st & {"done", "queued", "building"}:
                raise AMError(f"slug '{slug}' already exists in workspace '{ws}'; use edit")
            aid = prev["id"]
            newv = self.db.one("SELECT COALESCE(MAX(version),0) m FROM versions WHERE artifact_id=?", aid)["m"] + 1
        skey = self._put_source(ws, aid, newv, bundle)
        result = {"artifact_id": aid, "slug": slug, "workspace": ws, "version": newv, "job_id": jid,
                  "model": model, "status": "queued", "source_bytes": bundle["bytes"] if bundle else 0}
        with self.db.tx() as c:
            if prev:
                c.execute("UPDATE artifacts SET display_name=?, kind=?, format=?, updated_at=? WHERE id=?",
                          (display_name or slug, kind, format, t, aid))
            else:
                if c.execute("SELECT 1 FROM artifacts WHERE workspace=? AND slug=?", (ws, slug)).fetchone():
                    raise AMError(f"slug '{slug}' already exists in workspace '{ws}'; use edit")
                c.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?,?)",
                          (aid, ws, slug, display_name or slug, kind, format, principal["name"], t, t))
            c.execute("INSERT INTO versions (artifact_id,version,base_version,verbatim_request,status,job_id,model,"
                      "source_key,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (aid, newv, None, verbatim_request, "queued", jid, model, skey, principal["name"], t))
            c.execute("INSERT INTO jobs (id,artifact_id,version,client,status,created_at) VALUES (?,?,?,?,?,?)",
                      (jid, aid, newv, principal["id"], "queued", t))
            if idempotency_key:
                c.execute("INSERT INTO idempotency VALUES (?,?,?,?)",
                          (principal["id"], idempotency_key, json.dumps(result), t))
        self.audit(principal, "create", artifact_id=aid, slug=slug, kind=kind, model=model)
        self.queue.put_nowait(jid)
        return result

    # ---------------- edit ----------------
    def edit(self, principal: dict, *, base_version: int, verbatim_request: str, artifact_id: str,
             workspace: str | None = None, model: str | None = None, source_content: str | None = None,
             source_files: list | None = None, idempotency_key: str | None = None) -> dict:
        self.require(principal, "edit")
        prior = self._idem_get(principal, idempotency_key)
        if prior:
            return dict(prior, idempotent_replay=True)
        a = self.get_artifact(principal, artifact_id)
        if not (verbatim_request or "").strip():
            raise AMError("verbatim_request is required")
        model = self._model(model)
        bundle = self._source(source_content, source_files)
        self._quota(principal)
        jid, t = rid("job_"), now()
        with self.db.tx() as c:
            latest = c.execute("SELECT version,status FROM versions WHERE artifact_id=? "
                               "AND status NOT IN ('failed','needs_input') ORDER BY version DESC LIMIT 1",
                               (a["id"],)).fetchone()
            if not latest:
                raise AMError("artifact has no successful version to edit")
            if int(base_version) != latest["version"]:
                raise AMError(f"conflict: base_version {base_version} is stale; latest is v{latest['version']}. "
                              "Inspect it and edit from that version.")
            if latest["status"] != "done":
                raise AMError(f"conflict: v{latest['version']} is still {latest['status']}; wait for it, then edit")
            newv = c.execute("SELECT MAX(version) m FROM versions WHERE artifact_id=?", (a["id"],)).fetchone()["m"] + 1
            skey = self._put_source(a["workspace"], a["id"], newv, bundle) if bundle else c.execute(
                "SELECT source_key FROM versions WHERE artifact_id=? AND version=?",
                (a["id"], int(base_version))).fetchone()["source_key"]
            c.execute("INSERT INTO versions (artifact_id,version,base_version,verbatim_request,status,job_id,model,"
                      "source_key,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (a["id"], newv, int(base_version), verbatim_request, "queued", jid, model, skey,
                       principal["name"], t))
            c.execute("INSERT INTO jobs (id,artifact_id,version,client,status,created_at) VALUES (?,?,?,?,?,?)",
                      (jid, a["id"], newv, principal["id"], "queued", t))
            c.execute("UPDATE artifacts SET updated_at=? WHERE id=?", (t, a["id"]))
            result = {"artifact_id": a["id"], "slug": a["slug"], "workspace": a["workspace"], "version": newv,
                      "base_version": int(base_version), "job_id": jid, "model": model, "status": "queued"}
            if idempotency_key:
                c.execute("INSERT INTO idempotency VALUES (?,?,?,?)",
                          (principal["id"], idempotency_key, json.dumps(result), t))
        self.audit(principal, "edit", artifact_id=a["id"], version=newv, base_version=base_version, model=model)
        self.queue.put_nowait(jid)
        return result

    # ---------------- worker ----------------
    def start_workers(self) -> None:
        for j in self.db.all("SELECT id FROM jobs WHERE status IN ('queued','building') ORDER BY created_at"):
            self.db.exec("UPDATE jobs SET status='queued', progress='re-queued after restart' WHERE id=?", j["id"])
            self.queue.put_nowait(j["id"])
        for _ in range(CFG.max_concurrent_builds):
            self._workers.append(asyncio.create_task(self._worker()))

    async def _worker(self) -> None:
        while True:
            jid = await self.queue.get()
            try:
                await self._run_job(jid)
            except Exception as e:  # noqa: BLE001
                self._fail(jid, f"internal error: {type(e).__name__}: {e}")
            finally:
                self.queue.task_done()

    def _set_job(self, jid, status, error=None, progress_msg=None) -> None:
        j = self.db.one("SELECT * FROM jobs WHERE id=?", jid)
        if not j:
            return
        with self.db.tx() as c:
            c.execute("UPDATE jobs SET status=?, error=?, progress=?, finished_at=? WHERE id=?",
                      (status, error, progress_msg, now(), jid))
            c.execute("UPDATE versions SET status=? WHERE artifact_id=? AND version=?",
                      (status, j["artifact_id"], j["version"]))
        self.progress.pop(jid, None)

    def _fail(self, jid, err) -> None:
        j = self.db.one("SELECT * FROM jobs WHERE id=?", jid)
        if j:
            self._set_job(jid, "failed", error=err[:2000])
            self.audit_by_client(j["client"], "build_failed", artifact_id=j["artifact_id"], version=j["version"],
                                 error=err[:300])

    def _needs_input(self, jid, missing) -> None:
        j = self.db.one("SELECT * FROM jobs WHERE id=?", jid)
        if j:
            self._set_job(jid, "needs_input", error=missing[:2000], progress_msg="needs input")
            self.audit_by_client(j["client"], "build_needs_input", artifact_id=j["artifact_id"],
                                 version=j["version"], missing=missing[:300])

    def _source_name(self, a, v) -> str:
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
        with self.db.tx() as c:
            c.execute("UPDATE jobs SET status='building', started_at=? WHERE id=?", (now(), jid))
            c.execute("UPDATE versions SET status='building' WHERE artifact_id=? AND version=?", (a["id"], v["version"]))

        def progress(msg: str) -> None:
            self.progress[jid] = msg
            self.db.exec("UPDATE jobs SET progress=? WHERE id=?", msg, jid)

        base_source = None
        if v["base_version"]:
            bv = self.db.one("SELECT * FROM versions WHERE artifact_id=? AND version=?", a["id"], v["base_version"])
            key = Store.prefix(a["workspace"], a["id"], bv["version"]) + self._source_name(a, bv)
            data, _ = await asyncio.to_thread(self.store.get, key)
            base_source = data.decode("utf-8", errors="replace")
        history = [r["verbatim_request"] for r in self.db.all(
            "SELECT verbatim_request FROM versions WHERE artifact_id=? AND version<? AND status='done' ORDER BY version",
            a["id"], v["version"])]
        source = None
        if v.get("source_key"):
            raw, _ = await asyncio.to_thread(self.store.get, v["source_key"])
            source = json.loads(raw)
        try:
            res = await asyncio.wait_for(builder.run_build(
                kind=a["kind"], slug=a["slug"], display_name=a["display_name"], verbatim=v["verbatim_request"],
                model=v["model"] or CFG.default_model, base_source=base_source, base_version=v["base_version"],
                history=history, progress=progress, source=source, render_timeout=CFG.render_timeout_s),
                timeout=CFG.build_timeout_s)
        except asyncio.TimeoutError:
            self._fail(jid, f"build timed out after {CFG.build_timeout_s}s")
            return
        except builder.NeedsInput as e:
            self._needs_input(jid, str(e))
            return
        except Exception as e:  # noqa: BLE001
            self._fail(jid, f"{type(e).__name__}: {e}")
            return
        total = sum(len(b) for b in res.files.values())
        if total > CFG.max_output_bytes:
            self._fail(jid, f"output {total} bytes exceeds cap")
            return
        progress("uploading to object store")
        prefix = Store.prefix(a["workspace"], a["id"], v["version"])
        file_meta = []
        for name, data in res.files.items():
            ct = builder.MIME.get(name.rsplit(".", 1)[-1], "application/octet-stream")
            vid = await asyncio.to_thread(self.store.put, prefix + name, data, ct)
            file_meta.append({"name": name, "sha256": builder.sha256(data), "size": len(data),
                              "content_type": ct, "s3_version_id": vid})
        primary_sha = builder.sha256(res.files[res.primary])
        manifest = {"artifact_id": a["id"], "workspace": a["workspace"], "slug": a["slug"],
                    "display_name": a["display_name"], "kind": a["kind"], "format": a["format"],
                    "version": v["version"], "base_version": v["base_version"],
                    "verbatim_request": v["verbatim_request"], "builder_model": res.model,
                    "primary_file": res.primary, "sha256": primary_sha, "files": file_meta,
                    "assumptions": res.assumptions, "summary": res.summary, "created_by": v["created_by"],
                    "created_at": iso(v["created_at"]), "built_at": iso(now())}
        await asyncio.to_thread(self.store.put, prefix + "manifest.json",
                                json.dumps(manifest, indent=2).encode(), "application/json")
        with self.db.tx() as c:
            c.execute("UPDATE versions SET status='done', model=?, sha256=?, primary_file=?, files_json=?, "
                      "assumptions_json=?, summary=?, size=? WHERE artifact_id=? AND version=?",
                      (res.model, primary_sha, res.primary, json.dumps(file_meta), json.dumps(res.assumptions),
                       res.summary, total, a["id"], v["version"]))
            c.execute("UPDATE jobs SET status='done', progress='done', finished_at=? WHERE id=?", (now(), jid))
            c.execute("UPDATE artifacts SET updated_at=? WHERE id=?", (now(), a["id"]))
        self.progress.pop(jid, None)
        self.audit_by_client(j["client"], "build_done", artifact_id=a["id"], version=v["version"], sha256=primary_sha)

    # ---------------- read paths ----------------
    def preview_link(self, a: dict, version: int, ttl_s: int = 86400) -> str:
        r = self.db.one("SELECT id FROM short_links WHERE artifact_id=? AND version=? AND expires_at>? "
                        "ORDER BY expires_at DESC LIMIT 1", a["id"], version, now() + 3600)
        if r:
            sid = r["id"]
        else:
            sid = shortid(10)
            self.db.exec("INSERT INTO short_links VALUES (?,?,?,?,?)", sid, a["id"], version, "preview", now() + ttl_s)
            self.db.exec("DELETE FROM short_links WHERE expires_at<?", now())
        return f"{CFG.preview_url}/p/{sid}"

    def short_lookup(self, sid: str) -> dict | None:
        return self.db.one("SELECT * FROM short_links WHERE id=? AND expires_at>?", sid, now())

    def download_link(self, a: dict, version: int, fname: str, as_name: str, ttl_s: int = 900) -> str:
        return f"{CFG.preview_url}/dl/{sign({'a': a['id'], 'v': version, 'f': fname, 'n': as_name}, ttl_s)}"

    def card(self, a: dict, v: dict) -> dict:
        j = self.db.one("SELECT * FROM jobs WHERE id=?", v["job_id"]) or {}
        card = {"title": a["display_name"], "artifact_id": a["id"], "slug": a["slug"], "workspace": a["workspace"],
                "kind": a["kind"], "format": a["format"], "version": v["version"], "base_version": v["base_version"],
                "status": v["status"], "progress": self.progress.get(v["job_id"]) or j.get("progress"),
                "job_id": v["job_id"], "created_by": v["created_by"], "created_at": iso(v["created_at"])}
        if v["status"] == "done":
            card.update({"preview_url": self.preview_link(a, v["version"]), "preview_expires_in": "24h",
                         "size_bytes": v["size"], "builder": v["model"], "sha256": v["sha256"],
                         "primary_file": v["primary_file"], "assumptions": jloads(v["assumptions_json"], []),
                         "summary": v["summary"]})
        if v["status"] == "failed":
            card["error"] = j.get("error")
        if v["status"] == "needs_input":
            card["missing"] = j.get("error")
            card["next"] = ("supply the missing data as source_content, then call create again with the same slug "
                            "(if no version was ever built) or edit from the latest done version")
        if v.get("source_key"):
            card["has_source"] = True
        return card

    async def status(self, principal: dict, artifact_id: str | None = None, job_id: str | None = None,
                     wait: int = 0) -> dict:
        self.require(principal, "read")
        if job_id:
            j = self.db.one("SELECT * FROM jobs WHERE id=?", job_id)
            if not j:
                raise AMError("job not found")
            a = self.get_artifact(principal, j["artifact_id"])
            version = j["version"]
        else:
            a = self.get_artifact(principal, artifact_id)
            version = self.db.one("SELECT MAX(version) m FROM versions WHERE artifact_id=?", a["id"])["m"]
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

    def _share_state(self, aid: str, version: int) -> dict:
        s = self.db.one("SELECT * FROM shares WHERE artifact_id=? AND version=? AND revoked_at IS NULL AND expires_at>? "
                        "ORDER BY created_at DESC LIMIT 1", aid, version, now())
        if s:
            until = s["expires_at"] >= self.NEVER_EXPIRES - 1
            return {"state": "shared", "url": self.share_url(s["id"]),
                    "expires_at": None if until else iso(s["expires_at"]),
                    "lifetime": "until revoked" if until else iso(s["expires_at"]),
                    "note": "public link; unshare revokes immediately"}
        return {"state": "not_shared"}

    def list(self, principal: dict, workspace: str | None = None, kind: str | None = None,
             query: str | None = None, limit: int = 50) -> dict:
        self.require(principal, "read")
        ws = workspace or principal["workspace"]
        self.check_access(principal, ws)
        sql = "SELECT * FROM artifacts WHERE workspace=?"
        args: list = [ws]
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
            items.append({"artifact_id": a["id"], "slug": a["slug"], "title": a["display_name"], "kind": a["kind"],
                          "format": a["format"], "latest_version": vs[-1]["version"] if vs else None,
                          "latest_status": vs[-1]["status"] if vs else None,
                          "latest_done_version": done[-1] if done else None,
                          "created_by": a["created_by"], "updated_at": iso(a["updated_at"])})
        return {"workspace": ws, "count": len(items), "artifacts": items}

    def inspect(self, principal: dict, artifact_id: str, version: int | None = None) -> dict:
        self.require(principal, "read")
        a = self.get_artifact(principal, artifact_id)
        vs = self.db.all("SELECT * FROM versions WHERE artifact_id=? ORDER BY version", a["id"])
        history = [{"version": x["version"], "base_version": x["base_version"], "status": x["status"],
                    "verbatim_request": x["verbatim_request"], "created_by": x["created_by"],
                    "created_at": iso(x["created_at"]), "model": x["model"], "sha256": x["sha256"]} for x in vs]
        target = version or next((x["version"] for x in reversed(vs) if x["status"] == "done"), None)
        out = {"artifact_id": a["id"], "slug": a["slug"], "workspace": a["workspace"], "title": a["display_name"],
               "kind": a["kind"], "format": a["format"], "created_by": a["created_by"], "history": history}
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
                    out["manifest_error"] = str(e)[:200]
                out["card"] = self.card(a, v)
            out["share"] = self._share_state(a["id"], int(target))
        return out

    def _done_version(self, a: dict, version: int | None) -> dict:
        if version:
            v = self.db.one("SELECT * FROM versions WHERE artifact_id=? AND version=?", a["id"], int(version))
        else:
            v = self.db.one("SELECT * FROM versions WHERE artifact_id=? AND status='done' ORDER BY version DESC LIMIT 1",
                            a["id"])
        if not v:
            raise AMError("version not found")
        if v["status"] != "done":
            raise AMError(f"v{v['version']} is {v['status']}, not done")
        return v

    def export(self, principal: dict, artifact_id: str, version: int | None = None) -> dict:
        self.require(principal, "export")
        a = self.get_artifact(principal, artifact_id)
        v = self._done_version(a, version)
        primary = v["primary_file"]
        as_name = f"{a['slug']}-v{v['version']:03d}.html"
        self.audit(principal, "export", artifact_id=a["id"], version=v["version"])
        return {"artifact_id": a["id"], "version": v["version"], "file": as_name, "sha256": v["sha256"],
                "self_contained": True, "download_url": self.download_link(a, v["version"], primary, as_name),
                "expires_in": "15m"}

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

    def share(self, principal: dict, artifact_id: str, version: int | None = None, ttl_days: int | None = None) -> dict:
        """Publish a public link for one exact version. create never does this. unshare revokes at once."""
        self.require(principal, "share")
        a = self.get_artifact(principal, artifact_id)
        v = self._done_version(a, version)
        s = self.db.one("SELECT * FROM shares WHERE artifact_id=? AND version=? AND revoked_at IS NULL AND expires_at>? "
                        "ORDER BY expires_at DESC LIMIT 1", a["id"], v["version"], now() + 86400)
        if s:
            sid, exp, reused = s["id"], s["expires_at"], True
        else:
            sid = shortid(16)
            t = now()
            exp = self._share_exp(t, ttl_days if ttl_days is not None else CFG.share_ttl_days)
            self.db.exec("INSERT INTO shares VALUES (?,?,?,?,?,?,?,NULL)",
                         sid, a["id"], v["version"], v["sha256"], principal["name"], t, exp)
            reused = False
            self.audit(principal, "share_public", artifact_id=a["id"], version=v["version"], share_id=sid)
        until = exp >= self.NEVER_EXPIRES - 1
        return {"state": "shared", "scope": "public internet link (anyone with the link)", "artifact_id": a["id"],
                "version": v["version"], "url": self.share_url(sid), "expires_at": None if until else iso(exp),
                "lifetime": "until revoked" if until else iso(exp), "reused": reused,
                "revoke": "unshare(artifact_id) takes effect immediately"}

    def unshare(self, principal: dict, artifact_id: str, version: int | None = None) -> dict:
        self.require(principal, "share")
        a = self.get_artifact(principal, artifact_id)
        t = now()
        with self.db.tx() as c:
            if version:
                n = c.execute("UPDATE shares SET revoked_at=? WHERE artifact_id=? AND version=? AND revoked_at IS NULL",
                              (t, a["id"], int(version))).rowcount
            else:
                n = c.execute("UPDATE shares SET revoked_at=? WHERE artifact_id=? AND revoked_at IS NULL",
                              (t, a["id"])).rowcount
        self.audit(principal, "unshare", artifact_id=a["id"], version=version, revoked=n)
        return {"artifact_id": a["id"], "revoked_links": n, "state": "not_shared"}

    # ---------------- delete ----------------
    def delete(self, principal: dict, artifact_id: str, confirm_token: str | None = None) -> dict:
        self.require(principal, "delete")
        a = self.get_artifact(principal, artifact_id)
        if not confirm_token:
            tok = secrets.token_urlsafe(12)
            self.db.exec("INSERT INTO delete_tokens VALUES (?,?,?,?)", tok, a["id"], principal["id"], now() + 600)
            nv = self.db.one("SELECT COUNT(*) n FROM versions WHERE artifact_id=?", a["id"])["n"]
            return {"step": "confirm", "artifact_id": a["id"], "title": a["display_name"], "versions": nv,
                    "confirm_token": tok, "expires_in": "10m",
                    "warning": "Permanent: purges every stored version, all share links and the DB rows. "
                               "Call delete again with this confirm_token to proceed."}
        t = self.db.one("SELECT * FROM delete_tokens WHERE token=?", confirm_token)
        if not t or t["artifact_id"] != a["id"] or t["client"] != principal["id"] or t["expires_at"] < now():
            raise AMError("invalid or expired confirm_token")
        busy = self.db.one("SELECT 1 FROM jobs WHERE artifact_id=? AND status IN ('queued','building')", a["id"])
        if busy:
            raise AMError("a build is still running; wait for it to finish, then delete")
        purged = self.store.purge_prefix(Store.prefix(a["workspace"], a["id"]))
        with self.db.tx() as c:
            c.execute("UPDATE shares SET revoked_at=? WHERE artifact_id=? AND revoked_at IS NULL", (now(), a["id"]))
            for tbl in ("versions", "jobs", "shares", "delete_tokens", "short_links"):
                c.execute(f"DELETE FROM {tbl} WHERE artifact_id=?", (a["id"],))
            c.execute("DELETE FROM artifacts WHERE id=?", (a["id"],))
        self.audit(principal, "delete", artifact_id=a["id"], slug=a["slug"], purged_objects=purged)
        return {"deleted": True, "artifact_id": a["id"], "purged_object_versions": purged}

    # ---------------- object access for HTTP routes ----------------
    def read_file(self, aid: str, version: int, fname: str | None) -> tuple[bytes, str, str]:
        a = self.db.one("SELECT * FROM artifacts WHERE id=?", aid)
        v = self.db.one("SELECT * FROM versions WHERE artifact_id=? AND version=? AND status='done'", aid, version) if a else None
        if not v:
            raise AMError("not found")
        names = {f["name"]: f for f in jloads(v["files_json"], [])}
        fname = fname or v["primary_file"]
        if fname not in names:
            raise AMError("not found")
        data, ct = self.store.get(Store.prefix(a["workspace"], aid, version) + fname)
        return data, names[fname]["content_type"] or ct, fname

    def share_lookup(self, sid: str) -> dict | None:
        return self.db.one("SELECT * FROM shares WHERE id=? AND revoked_at IS NULL AND expires_at>?", sid, now())

    # ---------------- audit ----------------
    def audit(self, principal: dict, action: str, **fields) -> None:
        self._audit(principal["name"], action, **fields)

    def audit_by_client(self, client_id: str, action: str, **fields) -> None:
        tok = self.db.one("SELECT name FROM tokens WHERE id=?", client_id)
        self._audit(tok["name"] if tok else client_id, action, **fields)

    def _audit(self, who: str, action: str, **fields) -> None:
        rec = {"ts": iso(now()), "actor": who, "action": action, **fields}
        try:
            CFG.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with open(CFG.audit_path, "a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        except OSError:
            pass
