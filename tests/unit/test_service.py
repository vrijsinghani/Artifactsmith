"""Service create/edit/access and signed tokens."""

from __future__ import annotations

import asyncio

import pytest

from artifactsmith.builder import BuildError, BuildResult, NeedsInput
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


def test_create_defaults_slug_and_kind(svc):
    p = _principal()
    created = svc.create(p, display_name="Pilot Store Brief", verbatim_request="show the code")
    assert created["slug"] == "pilot-store-brief"
    assert created["status"] == "queued"
    with pytest.raises(AMError, match="display_name or slug"):
        svc.create(p, verbatim_request="x")


def test_fast_styles_apply_only_when_mode_is_omitted(svc, monkeypatch):
    monkeypatch.setattr(CFG, "fast_styles", frozenset({"house"}))
    monkeypatch.setattr(CFG, "build_mode", "full")
    p = _principal()
    house = svc.create(p, display_name="House Page", verbatim_request="show the code", style="house")
    assert house["mode"] == "fast"
    omitted = svc.create(p, display_name="Default Style", verbatim_request="show the code")
    assert omitted["style"] == "house" and omitted["mode"] == "fast"
    bold = svc.create(p, display_name="Bold Page", verbatim_request="show the code", style="bold")
    assert bold["mode"] == "full"
    explicit = svc.create(p, display_name="Full House", verbatim_request="show the code", style="house", mode="full")
    assert explicit["mode"] == "full"
    pdf = svc.create(p, display_name="House Pdf", verbatim_request="show the code", style="house", format="pdf")
    assert pdf["mode"] == "full"
    monkeypatch.setattr(CFG, "fast_styles", frozenset())
    plain = svc.create(p, display_name="House Again", verbatim_request="show the code", style="house")
    assert plain["mode"] == "full"


_HTML_PAGE = "<!DOCTYPE html><html><head><title>T</title></head><body><p>Ready</p></body></html>"
_AS_IS = (
    "Publish the attached HTML file exactly as-is as the page. "
    "It is a finished, self-contained page: do not add, remove, reword or restyle anything; "
    "keep every element, class, data-label attribute, style rule and link unchanged."
)


def test_finished_html_stays_full_when_mode_is_omitted(svc, monkeypatch):
    monkeypatch.setattr(CFG, "fast_styles", frozenset({"house"}))
    monkeypatch.setattr(CFG, "build_mode", "full")
    p = _principal()
    html = svc.create(
        p,
        display_name="Finished Page",
        verbatim_request="summarize the notes",
        style="house",
        source_content=_HTML_PAGE,
    )
    assert html["mode"] == "full"
    notes = svc.create(
        p,
        display_name="Notes Page",
        verbatim_request="summarize the notes",
        style="house",
        source_content="# Pilot\n\nThe code is HARBOR-17.\n",
    )
    assert notes["mode"] == "fast"
    explicit = svc.create(
        p,
        display_name="Explicit Fast",
        verbatim_request="summarize the notes",
        style="house",
        mode="fast",
        source_content=_HTML_PAGE,
    )
    assert explicit["mode"] == "fast"
    wording = svc.create(
        p,
        display_name="As Is Wording",
        verbatim_request=_AS_IS,
        style="house",
        source_content="# notes only\n",
    )
    assert wording["mode"] == "full"
    numbers = svc.create(
        p,
        display_name="Unchanged Numbers",
        verbatim_request="keep the numbers unchanged",
        style="house",
        source_content="# Pilot\n\n412 clicks\n",
    )
    assert numbers["mode"] == "fast"
    nearby = svc.create(
        p,
        display_name="Html Mention",
        verbatim_request="Build an HTML page from these notes. Keep the store code unchanged.",
        style="house",
        source_content="# Pilot\n\nThe code is HARBOR-17.\n",
    )
    assert nearby["mode"] == "fast"
    do_not_add = svc.create(
        p,
        display_name="Do Not Add",
        verbatim_request="Do not add a chart. The accent is the HTML color #c45.",
        style="house",
        source_content="# Pilot\n\nnotes\n",
    )
    assert do_not_add["mode"] == "fast"
    keep_page = svc.create(
        p,
        display_name="Keep Html Page",
        verbatim_request="Keep the HTML page unchanged",
        style="house",
        source_content="# notes only\n",
    )
    assert keep_page["mode"] == "full"
    fragment = svc.create(
        p,
        display_name="Html Fragment",
        verbatim_request="turn these notes into a page",
        style="house",
        source_content="<p>hello</p>",
    )
    assert fragment["mode"] == "fast"
    from_file = svc.create(
        p,
        display_name="File Html",
        verbatim_request="summarize the notes",
        style="house",
        source_files=[{"name": "page.html", "content": _HTML_PAGE}],
    )
    assert from_file["mode"] == "full"
    monkeypatch.setattr(CFG, "fast_styles", frozenset())
    monkeypatch.setattr(CFG, "build_mode", "fast")
    via_default = svc.create(
        p,
        display_name="Default Fast Html",
        verbatim_request="summarize the notes",
        style="bold",
        source_content=_HTML_PAGE,
    )
    assert via_default["mode"] == "full"
    via_notes = svc.create(
        p,
        display_name="Default Fast Notes",
        verbatim_request="summarize the notes",
        style="bold",
        source_content="# Pilot\n\nnotes\n",
    )
    assert via_notes["mode"] == "fast"


def test_display_name_strips_ooxml_controls(svc):
    p = _principal()
    created = svc.create(
        p,
        slug="ctrl-title",
        display_name="Pi\x0blot\x0c Brief",
        verbatim_request="x",
    )
    art = svc.get_artifact(p, created["artifact_id"])
    assert "\x0b" not in art["display_name"]
    assert "\x0c" not in art["display_name"]
    assert art["display_name"] == "Pilot Brief"


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
    # Same client key with a different request is a conflict, not a second build.
    with pytest.raises(AMError, match="idempotency_key was already used"):
        svc.create(
            p,
            slug="other-slug",
            display_name="Other",
            kind="web_static",
            verbatim_request="again",
            idempotency_key="k1",
        )
    with pytest.raises(AMError, match="already exists"):
        svc.create(p, slug="pilot-store", display_name="Pilot", kind="web_static", verbatim_request="again")


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


@pytest.mark.asyncio
async def test_edit_failure_names_the_check_without_source(svc, monkeypatch):
    async def ok(**kw):
        return BuildResult(
            files={"index.html": b"<html><body>ok</body></html>"},
            primary="index.html",
            assumptions=[],
            summary="built",
            model="gpt-test",
        )

    monkeypatch.setattr("artifactsmith.service.builder.run_build", ok)
    p = _principal()
    created = svc.create(p, slug="checked", display_name="Checked", kind="web_static", verbatim_request="x")
    await svc._run_job(created["job_id"])

    token = "sk-" + ("a" * 24)
    userinfo = "userinfo-value"
    ghp = "ghp_" + ("a" * 36)
    host_label = "host-label-value"
    cases = [
        (
            BuildError("builder output failed checks twice: possible secret (generic_bearer)"),
            "possible secret (generic_bearer)",
            [token],
        ),
        (
            BuildError(
                "builder output failed checks twice: blocked or authority-confused HTML href: "
                f"https://user:{userinfo}@evil.example/path"
            ),
            "blocked or authority-confused HTML href",
            [userinfo],
        ),
        (
            BuildError(f"builder output failed checks twice: private or local link host: {ghp}"),
            "private or local link host",
            [ghp],
        ),
        (
            BuildError(f"builder output failed checks twice: private or local link host: {host_label}"),
            "private or local link host",
            [host_label],
        ),
        (BuildError("page too large"), "build failed: BuildError", ["page too large"]),
        (RuntimeError("renderer exploded"), "build failed: RuntimeError", ["renderer exploded"]),
    ]
    for exc, expected, absent in cases:

        async def boom(exc=exc, **kw):
            raise exc

        monkeypatch.setattr("artifactsmith.service.builder.run_build", boom)
        edited = svc.edit(
            p,
            artifact_id=created["artifact_id"],
            base_version=1,
            verbatim_request=f"try {type(exc).__name__} {expected}",
        )
        await svc._run_job(edited["job_id"])
        error = svc.db.one("SELECT error FROM jobs WHERE id=?", edited["job_id"])["error"] or ""
        assert expected in error
        for secret in absent:
            assert secret not in error


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


@pytest.mark.asyncio
async def test_start_workers_requeues_mid_build(svc, monkeypatch):
    """A restart during a build requeues the job so claim can finish it."""
    from artifactsmith.service import now

    p = _principal()
    created = svc.create(p, slug="live-build", display_name="L", kind="web_static", verbatim_request="x")
    jid = created["job_id"]
    svc.db.exec(
        "UPDATE jobs SET status='building', started_at=?, progress='in flight' WHERE id=?",
        now(),
        jid,
    )
    monkeypatch.setattr(CFG, "max_concurrent_builds", 1)

    claimed: list[str] = []

    async def claim_once():
        jid2 = await svc.queue.get()
        claimed.append(jid2)
        # Atomic claim path used by _run_job.
        with svc.db.tx() as c:
            n = c.execute(
                "UPDATE jobs SET status='building', started_at=? WHERE id=? AND status='queued'",
                (now(), jid2),
            ).rowcount
            assert n == 1
        svc.queue.task_done()

    monkeypatch.setattr(svc, "_worker", claim_once)
    while not svc.queue.empty():
        svc.queue.get_nowait()
        svc.queue.task_done()
    svc.start_workers()
    await asyncio.wait_for(svc._workers[0], timeout=2)
    job = svc.db.one("SELECT status, progress FROM jobs WHERE id=?", jid)
    assert claimed == [jid]
    assert job["status"] == "building"
    assert "re-queued" in (job["progress"] or "")


def _seed_done(svc, slug, *, primary, files, fmt="html", body=b"<p>harbor</p>"):
    p = _principal()
    created = svc.create(p, slug=slug, display_name="I", kind="web_static", verbatim_request="x", format=fmt)
    aid = created["artifact_id"]
    svc.db.exec(
        "UPDATE versions SET status='done', primary_file=?, files_json=?, sha256='abc', size=? "
        "WHERE artifact_id=? AND version=1",
        primary,
        files,
        len(body),
        aid,
    )
    if fmt != "html":
        svc.db.exec("UPDATE artifacts SET format=? WHERE id=?", fmt, aid)
        svc.db.exec("UPDATE versions SET format=? WHERE artifact_id=?", fmt, aid)
    key = svc.store.prefix("alpha", aid, 1) + primary
    svc.store.put(key, body, "application/octet-stream")
    return p, aid


def test_include_content_reads_text_and_stays_off_by_default(svc):
    body = b"<p>harbor</p>"
    p, aid = _seed_done(svc, "readback", primary="index.html", files='[{"name":"index.html"}]', body=body)
    plain = svc.inspect(p, aid)
    assert "content" not in plain
    assert "content_error" not in plain
    opted = svc.inspect(p, aid, include_content=True)
    assert opted["content"]["file"] == "index.html"
    assert opted["content"]["text"] == "<p>harbor</p>"
    assert opted["content"]["size_bytes"] == len(body)


def test_include_content_other_workspace_does_not_read(svc, monkeypatch):
    p, aid = _seed_done(svc, "secret-page", primary="index.html", files='[{"name":"index.html"}]')

    def boom(_key):
        raise AssertionError("content read crossed workspaces")

    monkeypatch.setattr(svc.store, "get", boom)
    beta = _principal(name="beta", workspace="beta", pid="tok_2")
    with pytest.raises(AMError, match="forbidden"):
        svc.inspect(beta, aid, include_content=True)


def test_include_content_cap_is_characters(svc, monkeypatch):
    monkeypatch.setattr("artifactsmith.renderers.safety.CONTENT_MAX_CHARS", 2)
    p, aid = _seed_done(svc, "capped", primary="index.html", files='[{"name":"index.html"}]', body="页页".encode())
    at_cap = svc.inspect(p, aid, include_content=True)
    assert at_cap["content"]["text"] == "页页"
    assert at_cap["content"]["size_bytes"] == len("页页".encode())
    svc.store.put(svc.store.prefix("alpha", aid, 1) + "index.html", "页页页".encode(), "text/html")
    over = svc.inspect(p, aid, include_content=True)
    assert "text" not in over["content"]
    assert over["content"]["size_bytes"] == len("页页页".encode())
    exact = "ab"
    svc.store.put(svc.store.prefix("alpha", aid, 1) + "index.html", exact.encode(), "text/html")
    assert svc.inspect(p, aid, include_content=True)["content"]["text"] == exact


def test_include_content_binary_uses_companion(svc):
    p, aid = _seed_done(
        svc,
        "sheet",
        primary="document.pdf",
        files='[{"name":"document.pdf"},{"name":"content.md"}]',
        fmt="pdf",
        body=b"%PDF-1.4 not text",
    )
    svc.store.put(svc.store.prefix("alpha", aid, 1) + "content.md", b"# harbor\n", "text/markdown")
    out = svc.inspect(p, aid, include_content=True)
    assert out["content"]["primary_binary"] is True
    assert out["content"]["file"] == "content.md"
    assert out["content"]["text"] == "# harbor\n"
    assert b"%PDF" not in str(out["content"]).encode()


def test_include_content_missing_companion_and_not_done(svc):
    p, aid = _seed_done(
        svc,
        "bare-pdf",
        primary="document.pdf",
        files='[{"name":"document.pdf"}]',
        fmt="pdf",
        body=b"%PDF",
    )
    missing = svc.inspect(p, aid, include_content=True)
    assert missing["content"] == {"file": "content.md", "primary_binary": True}
    assert "text" not in missing["content"]
    assert "content_error" not in missing
    queued = svc.create(p, slug="queued-page", display_name="Q", kind="web_static", verbatim_request="x")
    pending = svc.inspect(p, queued["artifact_id"], version=1, include_content=True)
    assert "content" not in pending
    assert "content_error" not in pending


def test_include_content_store_failure(svc, monkeypatch):
    p, aid = _seed_done(svc, "boom-read", primary="index.html", files='[{"name":"index.html"}]')
    real_get = svc.store.get

    def get(key):
        if key.endswith("index.html"):
            raise RuntimeError("secret bucket path /data/internal leaked")
        return real_get(key)

    monkeypatch.setattr(svc.store, "get", get)
    svc.store.put(svc.store.prefix("alpha", aid, 1) + "manifest.json", b'{"ok":true}', "application/json")
    out = svc.inspect(p, aid, include_content=True)
    assert out["content_error"] == "store_read_failed"
    assert "content" not in out
    assert "secret" not in str(out)
    assert out["manifest"]["ok"] is True


def test_inspect_omits_safety_changes_without_the_manifest_key(svc):
    p, aid = _seed_done(svc, "old-pub", primary="index.html", files='[{"name":"index.html"}]')
    svc.store.put(svc.store.prefix("alpha", aid, 1) + "manifest.json", b'{"ok": true}', "application/json")
    out = svc.inspect(p, aid)
    assert "safety_changes" not in out
    assert out["manifest"]["ok"] is True


@pytest.mark.asyncio
async def test_status_names_a_failed_publish_manifest_read(svc, monkeypatch):
    p, aid = _seed_done(svc, "pub-manifest", primary="index.html", files='[{"name":"index.html"}]')
    svc.db.exec("UPDATE versions SET model='published' WHERE artifact_id=? AND version=1", aid)
    row = svc.db.one("SELECT job_id FROM versions WHERE artifact_id=? AND version=1", aid)
    real_get = svc.store.get

    def get(key):
        if str(key).endswith("manifest.json"):
            raise RuntimeError("secret bucket path")
        return real_get(key)

    monkeypatch.setattr(svc.store, "get", get)
    out = await svc.status(p, job_id=str(row["job_id"]))
    assert out["manifest_error"] == "store_read_failed"
    assert "safety_changes" not in out
    assert "secret" not in str(out)


@pytest.mark.asyncio
async def test_status_omits_safety_changes_when_the_key_is_absent(svc):
    p, aid = _seed_done(svc, "pub-old-manifest", primary="index.html", files='[{"name":"index.html"}]')
    svc.db.exec("UPDATE versions SET model='published' WHERE artifact_id=? AND version=1", aid)
    svc.store.put(svc.store.prefix("alpha", aid, 1) + "manifest.json", b'{"ok": true}', "application/json")
    row = svc.db.one("SELECT job_id FROM versions WHERE artifact_id=? AND version=1", aid)
    out = await svc.status(p, job_id=str(row["job_id"]))
    assert "safety_changes" not in out
    assert "manifest_error" not in out


def test_inspect_scrubs_store_errors(svc, monkeypatch):
    p = _principal()
    created = svc.create(p, slug="insp", display_name="I", kind="web_static", verbatim_request="x")
    aid = created["artifact_id"]
    svc.db.exec(
        "UPDATE versions SET status='done', primary_file='index.html', files_json=?, sha256='abc', size=1 "
        "WHERE artifact_id=? AND version=1",
        '[{"name":"index.html"}]',
        aid,
    )

    class Boom:
        def get(self, key):
            raise RuntimeError("secret bucket path /data/internal leaked")

    monkeypatch.setattr(svc, "store", Boom())
    out = svc.inspect(p, aid, version=1)
    assert out["manifest_error"] == "store_read_failed"
    assert "secret" not in str(out)
    assert "/data/internal" not in str(out)


def test_create_rejects_unknown_style(svc):
    p = _principal()
    with pytest.raises(AMError, match="house, bold, editorial, playful, terminal, swiss"):
        svc.create(p, slug="bad-style", display_name="Bad", kind="web_static", verbatim_request="x", style="neon")


def test_create_records_mode(svc):
    p = _principal()
    created = svc.create(p, slug="fast-page", display_name="Fast", kind="web_static", verbatim_request="x", mode="fast")
    assert created["mode"] == "fast"
    row = svc.db.one("SELECT mode FROM versions WHERE artifact_id=?", created["artifact_id"])
    assert row and row["mode"] == "fast"
    other = svc.create(
        p, slug="md-page", display_name="Md", kind="web_static", verbatim_request="x", format="markdown", mode="fast"
    )
    assert other["mode"] == "full"
    with pytest.raises(AMError, match="mode"):
        svc.create(p, slug="bad-mode", display_name="Bad", kind="web_static", verbatim_request="x", mode="turbo")


@pytest.mark.asyncio
async def test_style_persists_across_edit_and_shows_in_status_inspect(svc, monkeypatch):
    seen: list[str] = []

    async def fake_build(**kw):
        seen.append(kw.get("style") or "")
        return BuildResult(
            files={"index.html": b"<html><body>ok</body></html>"},
            primary="index.html",
            assumptions=[],
            summary="built",
            model="gpt-test",
        )

    monkeypatch.setattr("artifactsmith.service.builder.run_build", fake_build)
    p = _principal()
    created = svc.create(p, slug="styled", display_name="Styled", kind="web_static", verbatim_request="x", style="bold")
    assert created["style"] == "bold"
    await svc._run_job(created["job_id"])
    st = await svc.status(p, job_id=created["job_id"], wait=0)
    assert st["style"] == "bold"
    insp = svc.inspect(p, created["artifact_id"])
    assert insp["style"] == "bold"
    assert insp["history"][0]["style"] == "bold"
    assert insp["manifest"]["style"] == "bold"

    edited = svc.edit(p, artifact_id=created["artifact_id"], base_version=1, verbatim_request="tweak")
    assert edited["style"] == "bold"
    await svc._run_job(edited["job_id"])
    again = svc.inspect(p, created["artifact_id"], version=2)
    assert again["style"] == "bold"
    assert again["manifest"]["style"] == "bold"

    switched = svc.edit(
        p, artifact_id=created["artifact_id"], base_version=2, verbatim_request="swiss it", style="swiss"
    )
    assert switched["style"] == "swiss"
    with pytest.raises(AMError, match="house, bold, editorial, playful, terminal, swiss"):
        svc.edit(p, artifact_id=created["artifact_id"], base_version=2, verbatim_request="nope", style="neon")
    assert seen == ["bold", "bold"]
