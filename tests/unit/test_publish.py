"""Caller-published pages skip the model and use the same output checks."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from artifactsmith.builder import BuildError, BuildResult, publish_page, sanitize_html
from artifactsmith.config import CFG
from artifactsmith.db import DB
from artifactsmith.renderers.base import RenderOutput
from artifactsmith.renderers.safety import METADATA_MAX_CHARS
from artifactsmith.service import ALL_PERMS, AMError, Service


class FakeStore:
    bucket = "artifacts"

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    @staticmethod
    def prefix(workspace: str, artifact_id: str, version: int | None = None) -> str:
        from artifactsmith.store import Store

        return Store.prefix(workspace, artifact_id, version)

    def put(self, key: str, data: bytes, content_type: str) -> str:
        self.objects[key] = (data, content_type)
        return "vid"

    def get(self, key: str) -> tuple[bytes, str]:
        return self.objects[key]

    def purge_prefix(self, prefix: str) -> int:
        keys = [k for k in list(self.objects) if k.startswith(prefix)]
        for key in keys:
            del self.objects[key]
        return len(keys)


def _principal(perms: set[str] | None = None) -> dict[str, object]:
    return {"id": "tok_1", "name": "agent", "workspace": "alpha", "perms": set(perms or ALL_PERMS)}


RAW = """<html>
<head><title>Watch</title></head>
<body>
<h1>Watch</h1>
<p>The pilot store code is HARBOR-17.</p>
<p>See <a href="https://example.com/docs">the note</a>.</p>
<!-- drop me -->
<script>alert(1)</script>
</body>
</html>
"""


@pytest.fixture
def svc(monkeypatch: pytest.MonkeyPatch) -> Service:
    monkeypatch.setattr("artifactsmith.service.Store", FakeStore)
    return Service()


@pytest.fixture
def no_model(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    called = {"n": 0}

    async def _call(*_a: object, **_k: object) -> str:
        called["n"] += 1
        raise AssertionError("model should not be called")

    monkeypatch.setattr("artifactsmith.llm.call", _call)
    return called


def _secret() -> str:
    return "sk-" + ("b" * 24)


async def _job_error(svc: Service, created: dict[str, object]) -> str:
    await svc._run_job(str(created["job_id"]))
    row = svc.db.one("SELECT error, status FROM jobs WHERE id=?", created["job_id"])
    assert row is not None
    assert row["status"] == "failed"
    version = svc.db.one(
        "SELECT publish_json FROM versions WHERE artifact_id=? AND version=?",
        created["artifact_id"],
        created["version"],
    )
    assert version is not None
    assert version["publish_json"] is None
    return str(row["error"] or "")


@pytest.mark.asyncio
async def test_publish_html_matches_sanitizer_and_shares(svc: Service, no_model: dict[str, int]) -> None:
    principal = _principal()
    created = svc.publish(principal, display_name="Watch", html=RAW, summary="Harbor watch")
    assert created["status"] == "queued"
    assert created["model"] == "published"
    assert "unstyled" in str(created["warning"])
    await svc._run_job(str(created["job_id"]))
    assert no_model["n"] == 0
    stored, _, name = svc.read_file(str(created["artifact_id"]), 1, None)
    assert name == "index.html"
    assert stored.decode() == sanitize_html(RAW)
    assert b"HARBOR-17" in stored
    assert b"<script" not in stored.lower()
    assert b"drop me" not in stored
    version = svc.db.one(
        "SELECT publish_json, model, summary FROM versions WHERE artifact_id=?", created["artifact_id"]
    )
    assert version is not None
    assert version["publish_json"] is None
    assert version["model"] == "published"
    assert version["summary"] == "Harbor watch"
    status = await svc.status(principal, job_id=str(created["job_id"]))
    assert status["status"] == "done"
    assert status["builder"] == "published"
    assert str(status["preview_url"]).startswith("http")
    shared = svc.share(principal, str(created["artifact_id"]))
    assert shared["state"] == "shared"
    assert shared["version"] == 1
    edited = svc.edit(
        principal,
        artifact_id=str(created["artifact_id"]),
        base_version=1,
        verbatim_request="change the title",
    )
    follow = svc.db.one(
        "SELECT publish_json, model FROM versions WHERE artifact_id=? AND version=?",
        created["artifact_id"],
        edited["version"],
    )
    assert follow is not None
    assert follow["publish_json"] is None
    assert follow["model"] != "published"


@pytest.mark.asyncio
async def test_publish_new_version(svc: Service, no_model: dict[str, int]) -> None:
    principal = _principal()
    first = svc.publish(
        principal, display_name="Watch", slug="watch", html="<html><body><p>version one</p></body></html>"
    )
    await svc._run_job(str(first["job_id"]))
    second = svc.publish(
        principal,
        artifact_id=str(first["artifact_id"]),
        base_version=1,
        html="<html><body><p>version two</p></body></html>",
    )
    assert second["version"] == 2
    assert second["base_version"] == 1
    await svc._run_job(str(second["job_id"]))
    assert no_model["n"] == 0
    new, _, _ = svc.read_file(str(first["artifact_id"]), 2, None)
    old, _, _ = svc.read_file(str(first["artifact_id"]), 1, None)
    assert b"version two" in new
    assert b"version one" in old
    assert b"version two" not in old
    with pytest.raises(AMError, match="stale"):
        svc.publish(
            principal,
            artifact_id=str(first["artifact_id"]),
            base_version=1,
            html="<html><body><p>version three</p></body></html>",
        )


@pytest.mark.asyncio
async def test_publish_pdf_export(svc: Service, no_model: dict[str, int]) -> None:
    principal = _principal()
    created = svc.publish(
        principal,
        display_name="Harbor",
        slug="harbor-pdf",
        format="pdf",
        markdown="# Harbor\n\nThe code is HARBOR-17.\n",
    )
    await svc._run_job(str(created["job_id"]))
    assert no_model["n"] == 0
    pdf, _, name = svc.read_file(str(created["artifact_id"]), 1, None)
    assert name == "document.pdf"
    assert pdf.startswith(b"%PDF")
    source, _, source_name = svc.read_file(str(created["artifact_id"]), 1, "content.md")
    assert source_name == "content.md"
    assert b"HARBOR-17" in source
    exported = svc.export(principal, str(created["artifact_id"]))
    assert str(exported["file"]).endswith(".pdf")
    assert exported["version"] == 1
    assert str(exported["download_url"]).startswith("http")


@pytest.mark.asyncio
async def test_publish_fails_secret_private_link_sanitizer_and_sweep(
    svc: Service, no_model: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    principal = _principal()
    secret = svc.publish(
        principal,
        display_name="Secret",
        slug="secret-page",
        html=f"<html><body><p>{_secret()}</p></body></html>",
    )
    assert "possible secret" in await _job_error(svc, secret)

    titled = svc.publish(
        principal, display_name=_secret(), slug="secret-title", html="<html><body><p>ok</p></body></html>"
    )
    assert "possible secret" in await _job_error(svc, titled)

    private = svc.publish(
        principal,
        display_name="Local",
        slug="local-page",
        html="<html><body><p>See http://127.0.0.1/admin</p></body></html>",
    )
    assert "private or local link host: 127.0.0.1" in await _job_error(svc, private)

    monkeypatch.setattr(CFG, "allowed_link_domains", ["example.com"])
    blocked = svc.publish(
        principal,
        display_name="Off list",
        slug="off-list",
        html='<html><body><p><a href="https://other.test/x">x</a></p></body></html>',
    )
    assert "link host not on allow-list: other.test" in await _job_error(svc, blocked)
    monkeypatch.setattr(CFG, "allowed_link_domains", [])

    monkeypatch.setattr("artifactsmith.renderers.html_sanitize.MAX_HTML_BYTES", 8)
    huge = svc.publish(
        principal, display_name="Huge", slug="huge-page", html="<html><body><p>too big</p></body></html>"
    )
    assert await _job_error(svc, huge) == "page too large"
    monkeypatch.setattr("artifactsmith.renderers.html_sanitize.MAX_HTML_BYTES", 2_000_000)

    from artifactsmith.renderers.subprocess_render import render_bounded

    def _inject_script(**kwargs: object) -> RenderOutput:
        out = render_bounded(**kwargs)  # type: ignore[arg-type]
        files = dict(out.files)
        files[out.primary] = files[out.primary] + b"<script>x</script>"
        return RenderOutput(files=files, primary=out.primary)

    monkeypatch.setattr("artifactsmith.builder.render_bounded", _inject_script)
    swept = svc.publish(
        principal, display_name="Sweep", slug="sweep-page", html="<html><body><p>clean</p></body></html>"
    )
    assert await _job_error(svc, swept) == "script survived rendering"
    assert no_model["n"] == 0


@pytest.mark.asyncio
async def test_publish_rejects_render_output_cap(
    svc: Service, no_model: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(CFG, "max_output_bytes", 10)
    created = svc.publish(
        _principal(),
        display_name="Cap",
        slug="cap",
        html="<html><body><p>this page is larger than ten bytes</p></body></html>",
    )
    assert "exceeds cap" in await _job_error(svc, created)
    assert no_model["n"] == 0


def test_publish_requires_version_pair(svc: Service) -> None:
    with pytest.raises(AMError, match="artifact_id and base_version are both required"):
        svc.publish(_principal(), display_name="Half", html="<html><body><p>x</p></body></html>", base_version=1)


def test_publish_rejects_overlong_metadata_before_store(svc: Service, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("artifactsmith.renderers.safety.METADATA_MAX_CHARS", 20)
    principal = _principal()
    html = "<html><body><p>ok</p></body></html>"
    with pytest.raises(AMError, match="metadata exceeds 20 characters"):
        svc.publish(principal, display_name="Meta", slug="meta-s", html=html, summary="s" * 21)
    with pytest.raises(AMError, match="metadata exceeds 20 characters"):
        svc.publish(
            principal,
            display_name="Meta",
            slug="meta-a",
            html=html,
            assumptions=["ok", "a" * 21],
        )
    assert svc.db.one("SELECT id FROM artifacts") is None
    assert svc.db.one("SELECT id FROM jobs") is None
    padded = svc.publish(principal, display_name="Meta", slug="meta-pad", html=html, summary=" " * 30)
    assert padded["status"] == "queued"
    created = svc.publish(
        principal,
        display_name="Meta",
        slug="meta-ok",
        html=html,
        summary="s" * 20,
        assumptions=["a" * 20],
    )
    row = svc.db.one("SELECT publish_json FROM versions WHERE artifact_id=?", created["artifact_id"])
    assert row is not None
    assert "s" * 20 in str(row["publish_json"])
    assert "a" * 20 in str(row["publish_json"])


def test_publish_rejects_oversize_content(svc: Service, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("artifactsmith.renderers.safety.CONTENT_MAX_CHARS", 20)
    monkeypatch.setattr("artifactsmith.publish_common.CONTENT_MAX_CHARS", 20)
    principal = _principal()
    with pytest.raises(AMError, match="content exceeds 20 characters"):
        svc.publish(principal, display_name="Big", html="x" * 21)
    assert svc.db.one("SELECT id FROM artifacts") is None


@pytest.mark.asyncio
async def test_publish_page_rejects_oversize(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("artifactsmith.renderers.safety.CONTENT_MAX_CHARS", 20)
    with pytest.raises(BuildError, match="content exceeds 20 characters"):
        await publish_page(fmt="html", display_name="Big", html="x" * 21)


def test_publish_permission_quota_and_input(svc: Service, monkeypatch: pytest.MonkeyPatch) -> None:
    denied = _principal(perms={"create", "read"})
    with pytest.raises(AMError, match="not permitted to publish"):
        svc.publish(denied, display_name="No", html="<html><body><p>x</p></body></html>")
    principal = _principal()
    with pytest.raises(AMError, match="html is required"):
        svc.publish(principal, display_name="No")
    with pytest.raises(AMError, match="markdown is required when format is pdf"):
        svc.publish(principal, display_name="No", format="pdf")
    monkeypatch.setattr(CFG, "builds_per_hour", 1)
    svc.publish(principal, display_name="One", slug="one", html="<html><body><p>one</p></body></html>")
    with pytest.raises(AMError, match="quota"):
        svc.publish(principal, display_name="Two", slug="two", html="<html><body><p>two</p></body></html>")


@pytest.mark.asyncio
async def test_publish_companion_markdown_secret(svc: Service, no_model: dict[str, int]) -> None:
    created = svc.publish(
        _principal(),
        display_name="Companion",
        slug="companion",
        html="<html><body><p>clean</p></body></html>",
        markdown=f"leak {_secret()}",
    )
    assert "possible secret" in await _job_error(svc, created)
    assert no_model["n"] == 0


@pytest.mark.asyncio
async def test_deleted_artifact_clears_stored_page(
    svc: Service, no_model: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = "PAGE-BODY-SHOULD-NOT-REMAIN"
    created = svc.publish(
        _principal(),
        display_name="Gone",
        slug="gone-page",
        html=f"<html><body><p>{marker}</p></body></html>",
    )
    stored = svc.db.one(
        "SELECT publish_json FROM versions WHERE artifact_id=? AND version=?",
        created["artifact_id"],
        created["version"],
    )
    assert stored is not None
    assert marker in str(stored["publish_json"])
    original = svc.db.tx

    @contextmanager
    def tx_marks_deleting() -> Iterator[sqlite3.Connection]:
        with original() as conn:
            conn.execute(
                "UPDATE artifacts SET deleting_at=? WHERE id=?",
                (time.time(), created["artifact_id"]),
            )
            yield conn

    monkeypatch.setattr(svc.db, "tx", tx_marks_deleting)
    await svc._run_job(str(created["job_id"]))
    job = svc.db.one("SELECT status, error FROM jobs WHERE id=?", created["job_id"])
    assert job is not None
    assert job["status"] == "failed"
    assert job["error"] == "artifact is being deleted"
    assert marker not in str(job["error"])
    version = svc.db.one(
        "SELECT publish_json, status FROM versions WHERE artifact_id=? AND version=?",
        created["artifact_id"],
        created["version"],
    )
    assert version is not None
    assert version["publish_json"] is None
    assert no_model["n"] == 0


def test_publish_idempotent_replay(svc: Service) -> None:
    principal = _principal()
    html = "<html><body><p>same</p></body></html>"
    first = svc.publish(principal, display_name="Same", slug="same", html=html, idempotency_key="k1")
    replay = svc.publish(principal, display_name="Same", slug="same", html=html, idempotency_key="k1")
    assert replay["idempotent_replay"] is True
    assert replay["job_id"] == first["job_id"]
    assert replay["warning"] == first["warning"]
    assert "unstyled" in str(first["warning"])
    with pytest.raises(AMError, match="idempotency_key was already used"):
        svc.publish(
            principal,
            display_name="Same",
            slug="same",
            html="<html><body><p>other</p></body></html>",
            idempotency_key="k1",
        )


@pytest.mark.asyncio
async def test_publish_version_changes_format_and_keeps_slug(svc: Service, no_model: dict[str, int]) -> None:
    principal = _principal()
    first = svc.publish(
        principal,
        display_name="Watch",
        slug="watch-fmt",
        html="<html><body><p>version one</p></body></html>",
    )
    await svc._run_job(str(first["job_id"]))
    aid = str(first["artifact_id"])
    second = svc.publish(
        principal,
        artifact_id=aid,
        base_version=1,
        format="markdown",
        markdown="# Two\n\nversion two\n",
    )
    assert second["version"] == 2
    assert svc.get_artifact(principal, aid)["format"] == "html"
    await svc._run_job(str(second["job_id"]))
    assert no_model["n"] == 0
    art = svc.get_artifact(principal, aid)
    assert art["id"] == aid
    assert art["slug"] == "watch-fmt"
    assert art["format"] == "markdown"
    new, _, name = svc.read_file(aid, 2, None)
    old, _, old_name = svc.read_file(aid, 1, None)
    assert name == "document.md"
    assert b"version two" in new
    assert old_name == "index.html"
    assert b"version one" in old
    v1 = svc.db.one("SELECT format FROM versions WHERE artifact_id=? AND version=?", aid, 1)
    assert v1 is not None
    assert v1["format"] == "html"
    assert str(svc.export(principal, aid, 1)["file"]).endswith(".html")
    assert str(svc.export(principal, aid)["file"]).endswith(".md")


@pytest.mark.asyncio
async def test_status_wait_reports_the_new_format(svc: Service, no_model: dict[str, int]) -> None:
    principal = _principal()
    first = svc.publish(
        principal,
        display_name="Watch",
        slug="watch-status-fmt",
        html="<html><body><p>version one</p></body></html>",
    )
    await svc._run_job(str(first["job_id"]))
    aid = str(first["artifact_id"])
    second = svc.publish(
        principal,
        artifact_id=aid,
        base_version=1,
        format="markdown",
        markdown="# Two\n\nversion two\n",
    )
    gate = asyncio.Event()
    real_get = svc.get_artifact

    def gated(who: dict[str, object], artifact_id: str) -> dict[str, object]:
        row = real_get(who, artifact_id)
        gate.set()
        return row

    svc.get_artifact = gated  # type: ignore[method-assign]

    async def finish() -> None:
        await gate.wait()
        await svc._run_job(str(second["job_id"]))

    task = asyncio.create_task(finish())
    try:
        st = await svc.status(principal, job_id=str(second["job_id"]), wait=90)
    finally:
        svc.get_artifact = real_get  # type: ignore[method-assign]
        await task
    assert no_model["n"] == 0
    assert st["status"] == "done"
    assert st["format"] == "markdown"
    assert st["primary_file"] == "document.md"


@pytest.mark.asyncio
async def test_publish_markdown_version_can_become_html(svc: Service, no_model: dict[str, int]) -> None:
    principal = _principal()
    first = svc.publish(
        principal,
        display_name="Notes",
        slug="notes-fmt",
        format="markdown",
        markdown="# One\n\nversion one\n",
    )
    await svc._run_job(str(first["job_id"]))
    aid = str(first["artifact_id"])
    second = svc.publish(
        principal,
        artifact_id=aid,
        base_version=1,
        format="html",
        html="<html><body><p>version two</p></body></html>",
    )
    await svc._run_job(str(second["job_id"]))
    assert no_model["n"] == 0
    art = svc.get_artifact(principal, aid)
    assert art["slug"] == "notes-fmt"
    assert art["format"] == "html"
    new, _, name = svc.read_file(aid, 2, None)
    old, _, old_name = svc.read_file(aid, 1, None)
    assert name == "index.html"
    assert b"version two" in new
    assert old_name == "document.md"
    assert b"version one" in old
    v1 = svc.db.one("SELECT format FROM versions WHERE artifact_id=? AND version=?", aid, 1)
    assert v1 is not None
    assert v1["format"] == "markdown"


def test_publish_rejects_unknown_format(svc: Service) -> None:
    principal = _principal()
    created = svc.publish(
        principal,
        display_name="Watch",
        slug="watch-bad-fmt",
        html="<html><body><p>version one</p></body></html>",
    )
    with pytest.raises(AMError, match="format must be one of"):
        svc.publish(
            principal,
            artifact_id=str(created["artifact_id"]),
            base_version=1,
            format="rtf",
            markdown="# no\n",
        )
    count = svc.db.one("SELECT COUNT(*) AS n FROM versions WHERE artifact_id=?", created["artifact_id"])
    assert count is not None
    assert count["n"] == 1


@pytest.mark.asyncio
async def test_failed_format_change_keeps_artifact_format(svc: Service, no_model: dict[str, int]) -> None:
    principal = _principal()
    first = svc.publish(
        principal,
        display_name="Watch",
        slug="watch-fail-fmt",
        html="<html><body><p>version one</p></body></html>",
    )
    await svc._run_job(str(first["job_id"]))
    aid = str(first["artifact_id"])
    bad = svc.publish(
        principal,
        artifact_id=aid,
        base_version=1,
        format="markdown",
        markdown="# Secret\n\n" + _secret() + "\n",
    )
    assert "possible secret" in await _job_error(svc, bad)
    assert no_model["n"] == 0
    assert svc.get_artifact(principal, aid)["format"] == "html"


_BOLD = "linear-gradient(135deg,#1b1464,#5b2dbe)"
_HOUSE = "border-bottom:4px solid"
_STYLED = "<html><head><style>body{color:#111;font:16px sans-serif}</style></head><body><p>Hi</p></body></html>"
_BARE = "<html><body><p>Hi</p></body></html>"


@pytest.mark.asyncio
async def test_publish_markdown_style_renders_on_open(svc: Service, no_model: dict[str, int]) -> None:
    from starlette.testclient import TestClient

    import artifactsmith.server as server
    from artifactsmith.markdown_view import markdown_share_html

    principal = _principal()
    bold = svc.publish(
        principal,
        display_name="Pilot",
        slug="pilot-bold",
        format="markdown",
        style="bold",
        markdown="# Pilot\n\nThe code is HARBOR-17.\n",
    )
    assert bold["style"] == "bold"
    assert "warning" not in bold
    await svc._run_job(str(bold["job_id"]))
    data, _ct, name, _title, style = svc.read_primary_view(str(bold["artifact_id"]), 1)
    assert name == "document.md"
    assert style == "bold"
    assert b"HARBOR-17" in data
    assert b"<html" not in data.lower()
    shared = svc.share(principal, str(bold["artifact_id"]))
    sid = str(shared["url"]).rstrip("/").rsplit("/", 1)[-1]
    server.SVC = svc
    try:
        client = TestClient(server.preview_app)
        page = client.get(f"/s/{sid}/")
        downloaded = client.get(f"/s/{sid}/?raw=1")
    finally:
        server.SVC = None
    assert page.status_code == 200
    assert _BOLD in page.text
    assert page.headers["content-type"].startswith("text/html")
    assert downloaded.content == data

    house = svc.publish(
        principal,
        display_name="Harbor",
        slug="pilot-house",
        format="markdown",
        markdown="# Harbor\n\nThe code is HARBOR-17.\n",
    )
    assert house["style"] == "house"
    await svc._run_job(str(house["job_id"]))
    stored, _ct, house_name, house_title, house_style = svc.read_primary_view(str(house["artifact_id"]), 1)
    assert house_name == "document.md"
    assert house_style == "house"
    opened = markdown_share_html(stored, title=house_title, style=house_style).decode()
    assert _HOUSE in opened
    assert _BOLD not in opened
    assert no_model["n"] == 0


@pytest.mark.asyncio
async def test_publish_warns_when_html_has_little_css(svc: Service) -> None:
    principal = _principal()
    bare = svc.publish(principal, display_name="Bare", slug="bare-html", html=_BARE)
    assert "warning" in bare
    assert "has no CSS" in str(bare["warning"])
    assert "format=markdown" in str(bare["warning"])
    little = svc.publish(
        principal,
        display_name="Little",
        slug="little-html",
        html="<html><head><style>b{color:red}</style></head><body><p>Hi</p></body></html>",
    )
    assert "has little CSS" in str(little["warning"])
    assert "has no CSS" not in str(little["warning"])
    styled = svc.publish(principal, display_name="Styled", slug="styled-html", html=_STYLED)
    assert "warning" not in styled
    markdown = svc.publish(
        principal,
        display_name="Notes",
        slug="notes-md",
        format="markdown",
        markdown="# Notes\n",
    )
    assert "warning" not in markdown
    await svc._run_job(str(bare["job_id"]))
    await svc._run_job(str(styled["job_id"]))
    again = svc.publish(
        principal,
        artifact_id=str(bare["artifact_id"]),
        base_version=1,
        html=_BARE,
    )
    assert "warning" in again
    kept = svc.publish(
        principal,
        artifact_id=str(styled["artifact_id"]),
        base_version=1,
        html=_STYLED,
    )
    assert "warning" not in kept


def _version_format(svc: Service, artifact_id: str, version: int) -> str:
    row = svc.db.one(
        "SELECT format FROM versions WHERE artifact_id=? AND version=?",
        artifact_id,
        version,
    )
    assert row is not None
    return str(row["format"])


@pytest.mark.asyncio
async def test_publish_infers_markdown_when_format_is_omitted(svc: Service, no_model: dict[str, int]) -> None:
    from artifactsmith.markdown_view import markdown_share_html

    principal = _principal()
    created = svc.publish(
        principal,
        display_name="Inferred",
        slug="inferred-md",
        markdown="# Harbor\n\nThe code is HARBOR-17.\n",
    )
    assert created["style"] == "house"
    assert "warning" not in created
    assert _version_format(svc, str(created["artifact_id"]), 1) == "markdown"
    await svc._run_job(str(created["job_id"]))
    stored, _ct, name, title, style = svc.read_primary_view(str(created["artifact_id"]), 1)
    assert name == "document.md"
    assert style == "house"
    assert b"<html" not in stored.lower()
    opened = markdown_share_html(stored, title=title, style=style).decode()
    assert _HOUSE in opened
    assert _BOLD not in opened
    assert svc.get_artifact(principal, str(created["artifact_id"]))["format"] == "markdown"
    assert no_model["n"] == 0


def test_publish_infers_html_when_only_html_is_given(svc: Service) -> None:
    created = svc.publish(_principal(), display_name="Bare only", slug="bare-only", html=_BARE)
    assert _version_format(svc, str(created["artifact_id"]), 1) == "html"
    assert "warning" in created


@pytest.mark.asyncio
async def test_publish_keeps_html_primary_when_both_bodies_are_given(svc: Service) -> None:
    created = svc.publish(
        _principal(),
        display_name="Both",
        slug="both-bodies",
        html=_BARE,
        markdown="# companion only\n",
    )
    assert _version_format(svc, str(created["artifact_id"]), 1) == "html"
    assert "warning" in created
    await svc._run_job(str(created["job_id"]))
    _data, _ct, name, _title, _style = svc.read_primary_view(str(created["artifact_id"]), 1)
    assert name == "index.html"


@pytest.mark.asyncio
async def test_publish_version_keeps_format_when_both_bodies_are_given(svc: Service) -> None:
    principal = _principal()
    first = svc.publish(
        principal,
        display_name="Stay",
        slug="stay-md",
        format="markdown",
        markdown="# one\n",
    )
    await svc._run_job(str(first["job_id"]))
    second = svc.publish(
        principal,
        artifact_id=str(first["artifact_id"]),
        base_version=1,
        html=_BARE,
        markdown="# two\n",
    )
    assert _version_format(svc, str(first["artifact_id"]), 2) == "markdown"
    await svc._run_job(str(second["job_id"]))
    assert svc.get_artifact(principal, str(first["artifact_id"]))["format"] == "markdown"


@pytest.mark.asyncio
async def test_publish_version_infers_markdown_from_markdown_only(svc: Service) -> None:
    principal = _principal()
    first = svc.publish(principal, display_name="Was html", slug="was-html", html=_STYLED)
    await svc._run_job(str(first["job_id"]))
    second = svc.publish(
        principal,
        artifact_id=str(first["artifact_id"]),
        base_version=1,
        markdown="# now markdown\n",
    )
    assert "warning" not in second
    assert _version_format(svc, str(first["artifact_id"]), 2) == "markdown"
    await svc._run_job(str(second["job_id"]))
    _data, _ct, name, _title, _style = svc.read_primary_view(str(first["artifact_id"]), 2)
    assert name == "document.md"
    assert svc.get_artifact(principal, str(first["artifact_id"]))["format"] == "markdown"


@pytest.mark.asyncio
async def test_publish_version_infers_html_from_html_only(svc: Service) -> None:
    principal = _principal()
    first = svc.publish(
        principal,
        display_name="Was md",
        slug="was-md",
        format="markdown",
        markdown="# one\n",
    )
    await svc._run_job(str(first["job_id"]))
    second = svc.publish(
        principal,
        artifact_id=str(first["artifact_id"]),
        base_version=1,
        html=_BARE,
    )
    assert "warning" in second
    assert _version_format(svc, str(first["artifact_id"]), 2) == "html"


def test_publish_explicit_format_still_requires_its_body(svc: Service) -> None:
    principal = _principal()
    with pytest.raises(AMError, match="html is required"):
        svc.publish(principal, display_name="Forced", markdown="# only", format="html")
    with pytest.raises(AMError, match="markdown is required when format is pdf"):
        svc.publish(principal, display_name="Forced", format="pdf")


@pytest.mark.asyncio
async def test_publish_version_keeps_pdf_when_only_markdown_is_given(svc: Service) -> None:
    principal = _principal()
    first = svc.publish(
        principal,
        display_name="Sheet",
        slug="keep-pdf",
        format="pdf",
        markdown="# one\n",
    )
    await svc._run_job(str(first["job_id"]))
    second = svc.publish(
        principal,
        artifact_id=str(first["artifact_id"]),
        base_version=1,
        markdown="# two\n",
    )
    assert _version_format(svc, str(first["artifact_id"]), 2) == "pdf"
    await svc._run_job(str(second["job_id"]))
    assert svc.get_artifact(principal, str(first["artifact_id"]))["format"] == "pdf"


def test_inferred_format_keeps_docx_and_xlsx() -> None:
    from artifactsmith.publish_common import inferred_publish_format

    for fmt in ("docx", "xlsx"):
        assert inferred_publish_format(None, None, "# two\n", current=fmt) == fmt
    assert inferred_publish_format(None, None, "# two\n", current="html") == "markdown"
    assert inferred_publish_format("markdown", None, "# two\n", current="pdf") == "markdown"


def test_publish_whitespace_html_counts_as_markdown_only(svc: Service) -> None:
    created = svc.publish(
        _principal(),
        display_name="Spaces",
        slug="spaces-md",
        html="  \n\t",
        markdown="# real\n",
    )
    assert _version_format(svc, str(created["artifact_id"]), 1) == "markdown"
    assert "warning" not in created


@pytest.mark.asyncio
async def test_publish_html_keeps_title_and_lists_safety_changes(svc: Service, no_model: dict[str, int]) -> None:
    principal = _principal()
    html = (
        "<html><head><title>My Report</title></head><body>"
        "<p>See https://example.com/doc</p>"
        '<img src="https://example.com/a.png" alt="pic">'
        '<a href="https://example.com/x">x</a>'
        "</body></html>"
    )
    created = svc.publish(principal, display_name="My Report", html=html, summary="kept")
    assert "safety_changes" not in created
    await svc._run_job(str(created["job_id"]))
    assert no_model["n"] == 0
    stored, _, _name = svc.read_file(str(created["artifact_id"]), 1, None)
    text = stored.decode()
    assert "<title>My Report</title>" in text
    assert "&#32;" not in text
    assert 'href="https://example.com/doc"' not in text
    assert "https://example.com/doc" in text
    status = await svc.status(principal, job_id=str(created["job_id"]))
    changes = status["safety_changes"]
    assert "added_csp" in changes
    assert "linkified_url" not in changes
    assert "rewrote_image" in changes
    assert "hardened_anchor" in changes
    inspected = svc.inspect(principal, str(created["artifact_id"]))
    assert inspected["safety_changes"] == changes
    assert inspected["title"] == "My Report"
    assert "content" not in inspected


@pytest.mark.asyncio
async def test_publish_escapes_markup_in_the_document_title(svc: Service, no_model: dict[str, int]) -> None:
    principal = _principal()
    created = svc.publish(
        principal,
        display_name="A < B",
        html="<html><head><title>A < B</title></head><body><p>ok</p></body></html>",
    )
    await svc._run_job(str(created["job_id"]))
    assert no_model["n"] == 0
    stored, _, _name = svc.read_file(str(created["artifact_id"]), 1, None)
    assert "<title>A &lt; B</title>" in stored.decode()
    status = await svc.status(principal, job_id=str(created["job_id"]))
    assert "escaped_title" in status["safety_changes"]


@pytest.mark.asyncio
async def test_publish_markdown_lists_heading_and_link(svc: Service, no_model: dict[str, int]) -> None:
    principal = _principal()
    created = svc.publish(
        principal,
        display_name="Harbor",
        format="markdown",
        markdown="See https://example.com/doc for the note.\n",
    )
    await svc._run_job(str(created["job_id"]))
    assert no_model["n"] == 0
    stored, _, name = svc.read_file(str(created["artifact_id"]), 1, None)
    assert name == "document.md"
    text = stored.decode()
    assert text.startswith("# Harbor\n")
    assert "](https://example.com/doc)" in text
    status = await svc.status(principal, job_id=str(created["job_id"]))
    assert "added_heading" in status["safety_changes"]
    assert "linkified_url" in status["safety_changes"]


@pytest.mark.asyncio
async def test_publish_markdown_lists_parenthetical_links_and_stripped_scripts(
    svc: Service, no_model: dict[str, int]
) -> None:
    principal = _principal()
    linked = svc.publish(
        principal,
        display_name="Harbor",
        format="markdown",
        markdown="See https://example.com/a(b) now.\n",
    )
    await svc._run_job(str(linked["job_id"]))
    assert no_model["n"] == 0
    text = svc.read_file(str(linked["artifact_id"]), 1, None)[0].decode()
    assert "](<https://example.com/a(b)>" in text
    link_status = await svc.status(principal, job_id=str(linked["job_id"]))
    assert "linkified_url" in link_status["safety_changes"]
    assert "added_heading" in link_status["safety_changes"]
    scripted = svc.publish(
        principal,
        display_name="T",
        slug="script-md",
        format="markdown",
        markdown="Hello <script>alert(1)</script>\n",
    )
    await svc._run_job(str(scripted["job_id"]))
    stored = svc.read_file(str(scripted["artifact_id"]), 1, None)[0].decode()
    assert "<script>" not in stored
    assert "Hello" in stored
    script_status = await svc.status(principal, job_id=str(scripted["job_id"]))
    assert "stripped_markup" in script_status["safety_changes"]
    assert "added_heading" in script_status["safety_changes"]


def test_publish_note_is_on_inspect_and_rejected_before_a_row(svc: Service, monkeypatch: pytest.MonkeyPatch) -> None:
    principal = _principal()
    html = "<html><body><p>ok</p></body></html>"
    created = svc.publish(principal, display_name="Harbor", html=html, note="checked the harbor table")
    inspected = svc.inspect(principal, str(created["artifact_id"]))
    assert inspected["history"][0]["verbatim_request"] == "Published a finished page."
    assert inspected["history"][0]["note"] == "checked the harbor table"
    omitted = svc.publish(principal, display_name="Plain", slug="plain-note", html=html)
    plain = svc.inspect(principal, str(omitted["artifact_id"]))
    assert "note" not in plain["history"][0]
    assert plain["history"][0]["verbatim_request"] == "Published a finished page."
    with pytest.raises(AMError, match="exceeds"):
        svc.publish(principal, display_name="Long", html=html, note="x" * (METADATA_MAX_CHARS + 1))
    with pytest.raises(AMError, match="possible secret"):
        svc.publish(principal, display_name="Secret", html=html, note="sk-" + ("a" * 24))
    with pytest.raises(AMError, match="private or local link host"):
        svc.publish(principal, display_name="Local", html=html, note="see http://127.0.0.1/admin")
    monkeypatch.setattr(CFG, "allowed_link_domains", ["example.com"])
    with pytest.raises(AMError, match="not on allow-list"):
        svc.publish(principal, display_name="Offlist", html=html, note="see https://evil.test/x")
    rows = svc.db.one("SELECT COUNT(*) AS n FROM versions")
    assert rows is not None
    assert rows["n"] == 2


def test_publish_note_is_part_of_the_idempotency_fingerprint(svc: Service) -> None:
    principal = _principal()
    html = "<html><body><p>same</p></body></html>"
    first = svc.publish(principal, display_name="Same", slug="noted", html=html, note="one", idempotency_key="kn")
    with pytest.raises(AMError, match="idempotency_key was already used"):
        svc.publish(principal, display_name="Same", slug="noted", html=html, note="two", idempotency_key="kn")
    replay = svc.publish(principal, display_name="Same", slug="noted", html=html, note="one", idempotency_key="kn")
    assert replay["job_id"] == first["job_id"]
    plain = svc.publish(principal, display_name="Plain", slug="plain-fp", html=html, idempotency_key="kp")
    blank = svc.publish(principal, display_name="Plain", slug="plain-fp", html=html, note="   ", idempotency_key="kp")
    assert blank["idempotent_replay"] is True
    assert blank["job_id"] == plain["job_id"]


@pytest.mark.asyncio
async def test_publish_note_stays_out_of_the_next_edit(svc: Service, monkeypatch: pytest.MonkeyPatch) -> None:
    principal = _principal()
    created = svc.publish(
        principal,
        display_name="Harbor",
        html="<html><body><p>ok</p></body></html>",
        note="checked the harbor table",
    )
    await svc._run_job(str(created["job_id"]))
    seen: dict[str, list[str]] = {}

    async def fake_build(**kw: object) -> BuildResult:
        history = kw.get("history")
        seen["history"] = list(history) if isinstance(history, list) else []
        return BuildResult(
            files={"index.html": b"<html><body><p>next</p></body></html>"},
            primary="index.html",
            assumptions=[],
            summary="next",
            model="m",
        )

    monkeypatch.setattr("artifactsmith.service.builder.run_build", fake_build)
    edited = svc.edit(
        principal,
        artifact_id=str(created["artifact_id"]),
        base_version=1,
        verbatim_request="tweak the page",
    )
    await svc._run_job(str(edited["job_id"]))
    assert seen["history"] == ["Published a finished page."]
    assert "checked the harbor table" not in seen["history"]


def test_note_column_migrates(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE versions (artifact_id TEXT, version INTEGER, verbatim_request TEXT NOT NULL, "
        "status TEXT, job_id TEXT, created_by TEXT, created_at REAL)"
    )
    conn.execute(
        "INSERT INTO versions (artifact_id, version, verbatim_request, status, job_id, created_by, created_at) "
        "VALUES ('art_old', 1, 'Published a finished page.', 'done', 'job_old', 'agent', 0)"
    )
    conn.commit()
    conn.close()
    db = DB(path)
    cols = {str(row[1]) for row in db.conn.execute("PRAGMA table_info(versions)").fetchall()}
    assert "note" in cols
    row = db.one("SELECT verbatim_request, note FROM versions WHERE artifact_id=?", "art_old")
    assert row is not None
    assert row["verbatim_request"] == "Published a finished page."
    assert row["note"] is None
