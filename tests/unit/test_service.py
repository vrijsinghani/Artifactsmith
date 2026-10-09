"""Service create/edit/access and signed tokens."""

from __future__ import annotations

import asyncio

import pytest

from artifactsmith.builder import BuildResult, NeedsInput
from artifactsmith.config import CFG
from artifactsmith.service import ALL_PERMS, AMError, Service, iso, now, rid, shortid, sign, unsign


class FakeStore:
    bucket = "artifacts"

    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}

    @staticmethod
    def prefix(workspace, artifact_id, version=None):
        from artifactsmith.store import Store

        return Store.prefix(workspace, artifact_id, version)

    def put(self, key, data, content_type):
        self.objects[key] = (data, content_type)
        return "vid"

    def get(self, key):
        return self.objects[key]

    def purge_prefix(self, prefix):
        keys = [k for k in list(self.objects) if k.startswith(prefix)]
        for k in keys:
            del self.objects[k]
        return len(keys)


def _principal(name="agent", workspace="alpha", perms=None, pid="tok_1"):
    return {"id": pid, "name": name, "workspace": workspace, "perms": set(perms or ALL_PERMS)}


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setattr("artifactsmith.service.Store", FakeStore)
    return Service()


def test_helpers_and_sign():
    assert rid("art_").startswith("art_")
    assert len(shortid(8)) == 8
    assert iso(None) is None
    assert iso(now()).endswith("+00:00")
    tok = sign({"a": "x"}, ttl_s=60)
    assert unsign(tok)["a"] == "x"
    assert unsign("bad.token") is None
    assert unsign(sign({"a": "x"}, ttl_s=-10)) is None


def test_require_and_access(svc):
    p = _principal(perms={"read"})
    with pytest.raises(AMError, match="not permitted"):
        svc.require(p, "create")
    with pytest.raises(AMError, match="different workspace"):
        svc.check_access(p, "beta")
    with pytest.raises(AMError, match="artifact_id is required"):
        svc.get_artifact(p)
    with pytest.raises(AMError, match="not found"):
        svc.get_artifact(p, "art_missing")


def test_model_and_source_validation(svc):
    with pytest.raises(AMError, match="plain model"):
        svc._model("bad model!")
    assert svc._model(None) == CFG.default_model
    with pytest.raises(AMError, match="object"):
        svc._source(None, ["not-a-dict"])
    with pytest.raises(AMError, match="at most 20"):
        svc._source(None, [{"name": "f", "content": "x"}] * 21)
    with pytest.raises(AMError, match="string"):
        svc._source(123, None)  # type: ignore[arg-type]
    assert svc._source("  ", None) is None
    big = "x" * (200 * 1024 + 1)
    with pytest.raises(AMError, match="cap"):
        svc._source(big, None)
    bundle = svc._source("hello", [{"name": "a.txt", "content": "b"}])
    assert bundle and bundle["bytes"] > 0


def test_create_validations(svc):
    p = _principal()
    with pytest.raises(AMError, match="invalid workspace"):
        svc.create(p, slug="ok", display_name="n", kind="web_static", verbatim_request="x", workspace="NO")
    with pytest.raises(AMError, match="kind must"):
        svc.create(p, slug="ok", display_name="n", kind="nope", verbatim_request="x")
    with pytest.raises(AMError, match="web_static"):
        svc.create(p, slug="ok", display_name="n", kind="file", verbatim_request="x")
    with pytest.raises(AMError, match="format must"):
        svc.create(p, slug="ok", display_name="n", kind="web_static", verbatim_request="x", format="rtf")
    with pytest.raises(AMError, match="slug"):
        svc.create(p, slug="X", display_name="n", kind="web_static", verbatim_request="x")
    with pytest.raises(AMError, match="verbatim_request"):
        svc.create(p, slug="ok-slug", display_name="n", kind="web_static", verbatim_request="  ")
    with pytest.raises(AMError, match="capabilities"):
        svc.create(
            p,
            slug="ok-slug",
            display_name="n",
            kind="web_static",
            verbatim_request="x",
            capabilities="nope",  # type: ignore[arg-type]
        )
    with pytest.raises(AMError, match="not available"):
        svc.create(
            p,
            slug="ok-slug",
            display_name="n",
            kind="web_static",
            verbatim_request="x",
            capabilities={"web": True},
        )


def test_create_idempotent_and_duplicate_slug(svc):
    p = _principal()
    first = svc.create(
        p,
        slug="pilot-store",
        display_name="Pilot",
        kind="web_static",
        verbatim_request="show the code",
        source_content="HARBOR-17",
        idempotency_key="k1",
    )
    assert first["status"] == "queued" and first["version"] == 1
    replay = svc.create(
        p,
        slug="pilot-store",
        display_name="Pilot",
        kind="web_static",
        verbatim_request="show the code",
        source_content="HARBOR-17",
        idempotency_key="k1",
    )
    assert replay["idempotent_replay"] is True
    assert replay["artifact_id"] == first["artifact_id"]
    # Same client key with a different request fingerprint does not replay.
    with pytest.raises(AMError, match="already exists"):
        svc.create(
            p,
            slug="pilot-store",
            display_name="Pilot",
            kind="web_static",
            verbatim_request="again",
            idempotency_key="k1",
        )


def test_quota(svc, monkeypatch):
    monkeypatch.setattr(CFG, "builds_per_hour", 1)
    p = _principal()
    svc.create(p, slug="one", display_name="One", kind="web_static", verbatim_request="x")
    with pytest.raises(AMError, match="quota"):
        svc.create(p, slug="two", display_name="Two", kind="web_static", verbatim_request="x")


def test_edit_stale_and_missing(svc):
    p = _principal()
    created = svc.create(p, slug="pilot", display_name="P", kind="web_static", verbatim_request="x")
    with pytest.raises(AMError, match="still queued"):
        svc.edit(p, base_version=1, verbatim_request="change", artifact_id=created["artifact_id"])
    svc.db.exec(
        "UPDATE versions SET status='failed' WHERE artifact_id=? AND version=1",
        created["artifact_id"],
    )
    with pytest.raises(AMError, match="no successful version"):
        svc.edit(p, base_version=1, verbatim_request="change", artifact_id=created["artifact_id"])
    svc.db.exec(
        "UPDATE versions SET status='done' WHERE artifact_id=? AND version=1",
        created["artifact_id"],
    )
    with pytest.raises(AMError, match="stale"):
        svc.edit(p, base_version=0, verbatim_request="change", artifact_id=created["artifact_id"])
    with pytest.raises(AMError, match="verbatim_request"):
        svc.edit(p, base_version=1, verbatim_request=" ", artifact_id=created["artifact_id"])
    out = svc.edit(
        p,
        base_version=1,
        verbatim_request="change it",
        artifact_id=created["artifact_id"],
        idempotency_key="ek",
    )
    assert out["version"] == 2 and out["base_version"] == 1
    again = svc.edit(
        p,
        base_version=1,
        verbatim_request="change it",
        artifact_id=created["artifact_id"],
        idempotency_key="ek",
    )
    assert again["idempotent_replay"] is True and again["job_id"] == out["job_id"]


def _mark_done(svc: Service, artifact_id: str, version: int, primary="index.html") -> None:
    svc.db.exec(
        "UPDATE versions SET status='done', primary_file=?, files_json=?, sha256=?, size=? "
        "WHERE artifact_id=? AND version=?",
        primary,
        '[{"name":"index.html","content_type":"text/html"}]',
        "abc",
        4,
        artifact_id,
        version,
    )
    svc.store.put(
        FakeStore.prefix("alpha", artifact_id, version) + "index.html",
        b"<html>ok</html>",
        "text/html",
    )
    svc.store.put(
        FakeStore.prefix("alpha", artifact_id, version) + "manifest.json",
        b'{"ok": true}',
        "application/json",
    )


def test_list_inspect_export_share(svc):
    p = _principal()
    created = svc.create(p, slug="brief", display_name="Brief", kind="web_static", verbatim_request="x")
    aid = created["artifact_id"]
    _mark_done(svc, aid, 1)
    listed = svc.list(p, query="brief")
    assert listed["count"] == 1
    insp = svc.inspect(p, aid)
    assert insp["history"][0]["status"] == "done"
    assert insp.get("manifest") == {"ok": True}
    exported = svc.export(p, aid)
    assert exported["file"].endswith(".html")
    assert "/dl/" in exported["download_url"]
    shared = svc.share(p, aid)
    assert shared["state"] == "shared"
    reused = svc.share(p, aid)
    assert reused["reused"] is True
    assert svc._share_state(aid, 1)["state"] == "shared"
    gone = svc.unshare(p, aid)
    assert gone["revoked_links"] == 1
    assert svc._share_state(aid, 1)["state"] == "not_shared"


def test_delete_two_step(svc):
    p = _principal()
    created = svc.create(p, slug="gone", display_name="Gone", kind="web_static", verbatim_request="x")
    svc.db.exec("UPDATE jobs SET status='done' WHERE id=?", created["job_id"])
    first = svc.delete(p, created["artifact_id"])
    assert first["step"] == "confirm"
    with pytest.raises(AMError, match="invalid or expired"):
        svc.delete(p, created["artifact_id"], confirm_token="nope")
    done = svc.delete(p, created["artifact_id"], confirm_token=first["confirm_token"])
    assert done["deleted"] is True
    with pytest.raises(AMError, match="not found"):
        svc.get_artifact(p, created["artifact_id"])


def test_delete_blocks_edit_and_cancels_queued(svc):
    p = _principal()
    created = svc.create(p, slug="race", display_name="Race", kind="web_static", verbatim_request="x")
    aid = created["artifact_id"]
    _mark_done(svc, aid, 1)
    svc.db.exec("UPDATE jobs SET status='done' WHERE id=?", created["job_id"])
    # Queue a second edit, then start delete: queued job must be cancelled and edit refused.
    edited = svc.edit(p, base_version=1, verbatim_request="more", artifact_id=aid)
    assert edited["status"] == "queued"
    first = svc.delete(p, aid)
    done = svc.delete(p, aid, confirm_token=first["confirm_token"])
    assert done["deleted"] is True
    with pytest.raises(AMError, match="not found"):
        svc.edit(p, base_version=1, verbatim_request="nope", artifact_id=aid)


@pytest.mark.asyncio
async def test_enqueue_from_worker_thread(svc):
    """Sync MCP tools run in threads; enqueue must wake the owning loop safely."""
    loop = asyncio.get_running_loop()
    svc._loop = loop
    seen: list[str] = []

    async def drain_one() -> None:
        jid = await asyncio.wait_for(svc.queue.get(), timeout=2)
        seen.append(jid)
        svc.queue.task_done()

    waiter = asyncio.create_task(drain_one())
    await asyncio.sleep(0.05)

    def from_thread() -> None:
        svc._enqueue("job_from_thread")

    await asyncio.to_thread(from_thread)
    await waiter
    assert seen == ["job_from_thread"]


def test_revoke_previews(svc):
    p = _principal()
    created = svc.create(p, slug="prev", display_name="Prev", kind="web_static", verbatim_request="x")
    aid = created["artifact_id"]
    _mark_done(svc, aid, 1)
    a = svc.db.one("SELECT * FROM artifacts WHERE id=?", aid)
    assert a is not None
    url = svc.preview_link(a, 1)
    tok = url.rsplit("/", 1)[-1]
    assert svc.short_lookup(tok) is not None
    out = svc.revoke_previews(p, aid)
    assert out["revoked_previews"] >= 1
    assert svc.short_lookup(tok) is None


def test_read_file_and_card(svc):
    p = _principal()
    created = svc.create(p, slug="page", display_name="Page", kind="web_static", verbatim_request="x")
    aid = created["artifact_id"]
    _mark_done(svc, aid, 1)
    data, ct, name = svc.read_file(aid, 1, None)
    assert name == "index.html" and data.startswith(b"<html")
    with pytest.raises(AMError, match="not found"):
        svc.read_file(aid, 1, "missing.css")
    v = svc.db.one("SELECT * FROM versions WHERE artifact_id=?", aid)
    a = svc.db.one("SELECT * FROM artifacts WHERE id=?", aid)
    card = svc.card(a, v)
    assert card["preview_url"].startswith(CFG.preview_url)
    preview = svc.preview_link(a, 1)
    assert "/p/" in preview
    assert svc.short_lookup(preview.rsplit("/", 1)[-1]) is not None


def test_share_ttl_until_revoked(svc, monkeypatch):
    monkeypatch.setattr(CFG, "share_ttl_days", 0)
    p = _principal()
    created = svc.create(p, slug="ever", display_name="Ever", kind="web_static", verbatim_request="x")
    _mark_done(svc, created["artifact_id"], 1)
    shared = svc.share(p, created["artifact_id"])
    assert shared["lifetime"] == "until revoked"
    assert svc._share_exp(now(), 9) == Service.NEVER_EXPIRES


@pytest.mark.asyncio
async def test_status_wait_done(svc):
    p = _principal()
    created = svc.create(p, slug="wait-me", display_name="W", kind="web_static", verbatim_request="x")
    _mark_done(svc, created["artifact_id"], 1)
    svc.db.exec("UPDATE jobs SET status='done' WHERE id=?", created["job_id"])
    out = await svc.status(p, job_id=created["job_id"], wait=0)
    assert out["status"] == "done"
    with pytest.raises(AMError, match="job not found"):
        await svc.status(p, job_id="job_missing")


@pytest.mark.asyncio
async def test_run_job_done_and_needs_input(svc, monkeypatch):
    async def fake_build(**kw):
        return BuildResult(
            files={"index.html": b"<html><body>ok</body></html>"},
            primary="index.html",
            assumptions=["none"],
            summary="built",
            model="gpt-test",
        )

    monkeypatch.setattr("artifactsmith.service.builder.run_build", fake_build)
    p = _principal()
    created = svc.create(p, slug="built", display_name="Built", kind="web_static", verbatim_request="x")
    await svc._run_job(created["job_id"])
    v = svc.db.one("SELECT * FROM versions WHERE artifact_id=?", created["artifact_id"])
    assert v["status"] == "done"
    assert v["primary_file"] == "index.html"

    async def need(**kw):
        raise NeedsInput("revenue figure")

    monkeypatch.setattr("artifactsmith.service.builder.run_build", need)
    second = svc.create(p, slug="need-it", display_name="Need", kind="web_static", verbatim_request="x")
    await svc._run_job(second["job_id"])
    st = svc.db.one("SELECT * FROM jobs WHERE id=?", second["job_id"])
    assert st["status"] == "needs_input"


@pytest.mark.asyncio
async def test_run_job_fail_timeout_and_oversize(svc, monkeypatch):
    async def boom(**kw):
        raise RuntimeError("renderer exploded")

    monkeypatch.setattr("artifactsmith.service.builder.run_build", boom)
    p = _principal()
    created = svc.create(p, slug="boom", display_name="Boom", kind="web_static", verbatim_request="x")
    await svc._run_job(created["job_id"])
    assert svc.db.one("SELECT status FROM jobs WHERE id=?", created["job_id"])["status"] == "failed"

    async def hang(**kw):
        raise TimeoutError

    monkeypatch.setattr("artifactsmith.service.builder.run_build", hang)
    hung = svc.create(p, slug="hang", display_name="Hang", kind="web_static", verbatim_request="x")
    await svc._run_job(hung["job_id"])
    assert "timed out" in (svc.db.one("SELECT error FROM jobs WHERE id=?", hung["job_id"])["error"] or "")

    async def huge(**kw):
        return BuildResult(
            files={"index.html": b"x" * 100},
            primary="index.html",
            assumptions=[],
            summary="big",
            model="m",
        )

    monkeypatch.setattr(CFG, "max_output_bytes", 10)
    monkeypatch.setattr("artifactsmith.service.builder.run_build", huge)
    big = svc.create(p, slug="huge", display_name="Huge", kind="web_static", verbatim_request="x")
    await svc._run_job(big["job_id"])
    assert "exceeds cap" in (svc.db.one("SELECT error FROM jobs WHERE id=?", big["job_id"])["error"] or "")


def test_cross_workspace_forbidden(svc):
    alpha = _principal()
    created = svc.create(alpha, slug="secret", display_name="S", kind="web_static", verbatim_request="x")
    beta = _principal(name="beta", workspace="beta", pid="tok_2")
    with pytest.raises(AMError, match="forbidden"):
        svc.inspect(beta, created["artifact_id"])


@pytest.mark.asyncio
async def test_start_workers_requeues(svc, monkeypatch):
    p = _principal()
    created = svc.create(p, slug="re-q", display_name="R", kind="web_static", verbatim_request="x")
    monkeypatch.setattr(CFG, "max_concurrent_builds", 1)

    async def idle():
        await asyncio.sleep(3600)

    monkeypatch.setattr(svc, "_worker", idle)
    svc.start_workers()
    assert len(svc._workers) == 1
    job = svc.db.one("SELECT progress FROM jobs WHERE id=?", created["job_id"])
    assert "re-queued" in (job["progress"] or "")
    svc._workers[0].cancel()
    try:
        await svc._workers[0]
    except asyncio.CancelledError:
        pass
