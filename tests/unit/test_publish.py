"""Caller-published pages skip the model and use the same output checks."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from artifactsmith.builder import BuildError, publish_page, sanitize_html
from artifactsmith.config import CFG
from artifactsmith.renderers.base import RenderOutput
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
        svc.publish(principal, display_name="No", markdown="# only markdown")
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
    with pytest.raises(AMError, match="idempotency_key was already used"):
        svc.publish(
            principal,
            display_name="Same",
            slug="same",
            html="<html><body><p>other</p></body></html>",
            idempotency_key="k1",
        )
