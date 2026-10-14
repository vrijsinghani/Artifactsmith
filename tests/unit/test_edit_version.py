"""Edit versions still store a full document and the same history rows."""

from __future__ import annotations

import pytest

from artifactsmith.service import AMError, Service
from artifactsmith.store import Store

PAGE = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Stores</title></head>
<body>
<h1>Store report</h1>
<p>North is the lead store.</p>
<table>
<tr><td>North</td><td>12</td></tr>
<tr><td>South</td><td>9</td></tr>
</table>
<p>South is the backup.</p>
</body>
</html>
"""


def _patch(find: str, replace: str) -> str:
    return (
        "===ASSUMPTIONS===\n- none\n===SUMMARY===\nChanged the north count to 18.\n"
        f"===PATCH===\n<<<FIND>>>\n{find}\n<<<REPLACE>>>\n{replace}\n<<<END>>>\n===END===\n"
    )


def _full(body: str) -> str:
    return (
        "===ASSUMPTIONS===\n- none\n===SUMMARY===\nBuilt the store report.\n"
        f"===FILE: index.html===\n{body}\n===END===\n"
    )


class _FakeStore:
    bucket = "artifacts"

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    @staticmethod
    def prefix(workspace: str, artifact_id: str, version: int | None = None) -> str:
        return Store.prefix(workspace, artifact_id, version)

    def put(self, key: str, data: bytes, content_type: str) -> str:
        self.objects[key] = (data, content_type)
        return "vid"

    def get(self, key: str) -> tuple[bytes, str]:
        return self.objects[key]

    def purge_prefix(self, prefix: str) -> int:
        return 0


@pytest.mark.asyncio
async def test_edit_history_and_versions_stay_full_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("artifactsmith.service.Store", _FakeStore)
    svc = Service()

    async def fake_call(model: str, system: str, user: str, timeout: float = 300) -> str:
        if "<base_file>" not in user:
            return _full(PAGE)
        return _patch("<td>12</td>", "<td>18</td>")

    monkeypatch.setattr("artifactsmith.builder.llm.call", fake_call)
    principal = {"id": "tok_1", "name": "agent", "workspace": "alpha", "perms": {"create", "read", "edit"}}
    created = svc.create(
        principal,
        slug="stores",
        display_name="Stores",
        verbatim_request="Show the store counts.",
        format="html",
    )
    await svc._run_job(created["job_id"])
    edited = svc.edit(
        principal,
        artifact_id=created["artifact_id"],
        base_version=1,
        verbatim_request="Set the north count to 18.",
    )
    await svc._run_job(edited["job_id"])
    info = svc.inspect(principal, created["artifact_id"])
    assert [row["verbatim_request"] for row in info["history"]] == [
        "Show the store counts.",
        "Set the north count to 18.",
    ]
    assert info["history"][1]["base_version"] == 1
    assert info["history"][1]["status"] == "done"
    stored = svc.store.get(Store.prefix("alpha", created["artifact_id"], 2) + "index.html")[0].decode()
    assert "18" in stored
    assert "South is the backup." in stored
    assert stored.lstrip().lower().startswith("<!doctype html")


@pytest.mark.asyncio
async def test_edit_adopts_a_changed_page_title(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("artifactsmith.service.Store", _FakeStore)
    svc = Service()
    renamed = PAGE.replace("<title>Stores</title>", "<title>Vinnie draft</title>")

    async def fake_call(model: str, system: str, user: str, timeout: float = 300) -> str:
        if "===PATCH===" in system:
            return "not a patch"
        if "<base_file>" in user:
            return _full(renamed)
        return _full(PAGE)

    monkeypatch.setattr("artifactsmith.builder.llm.call", fake_call)
    principal = {"id": "tok_1", "name": "agent", "workspace": "alpha", "perms": {"create", "read", "edit"}}
    created = svc.create(
        principal,
        slug="stores",
        display_name="Stores",
        verbatim_request="Show the store counts.",
        format="html",
    )
    await svc._run_job(created["job_id"])
    edited = svc.edit(
        principal,
        artifact_id=created["artifact_id"],
        base_version=1,
        verbatim_request="Retitle the page for Vinnie.",
    )
    await svc._run_job(edited["job_id"])
    info = svc.inspect(principal, created["artifact_id"])
    assert info["title"] == "Vinnie draft"
    assert info["slug"] == "stores"


@pytest.mark.asyncio
async def test_edit_explicit_display_name_renames_the_card(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("artifactsmith.service.Store", _FakeStore)
    svc = Service()

    async def fake_call(model: str, system: str, user: str, timeout: float = 300) -> str:
        if "===PATCH===" in system:
            return "not a patch"
        return _full(PAGE)

    monkeypatch.setattr("artifactsmith.builder.llm.call", fake_call)
    principal = {"id": "tok_1", "name": "agent", "workspace": "alpha", "perms": {"create", "read", "edit"}}
    created = svc.create(
        principal,
        slug="stores",
        display_name="Stores",
        verbatim_request="Show the store counts.",
        format="html",
    )
    await svc._run_job(created["job_id"])
    edited = svc.edit(
        principal,
        artifact_id=created["artifact_id"],
        base_version=1,
        verbatim_request="Keep the page and rename the card.",
        display_name="For Vinnie",
    )
    await svc._run_job(edited["job_id"])
    info = svc.inspect(principal, created["artifact_id"])
    assert info["title"] == "For Vinnie"
    assert info["slug"] == "stores"


@pytest.mark.asyncio
async def test_edit_can_change_format_without_patching_the_old_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("artifactsmith.service.Store", _FakeStore)
    svc = Service()
    calls: list[str] = []

    async def fake_call(model: str, system: str, user: str, timeout: float = 300) -> str:
        calls.append(system)
        if "===PATCH===" in system:
            return _patch("<h1>Store report</h1>", "<h1>Patched</h1>")
        if "===FILE: index.html===" in system:
            return _full(PAGE)
        return (
            "===ASSUMPTIONS===\n- none\n===SUMMARY===\nMarkdown version.\n"
            "===FILE: content.md===\n# Pilot\n\nThe code is HARBOR-17.\n===END===\n"
        )

    monkeypatch.setattr("artifactsmith.builder.llm.call", fake_call)
    principal = {"id": "tok_1", "name": "agent", "workspace": "alpha", "perms": {"create", "read", "edit", "export"}}
    created = svc.create(
        principal,
        slug="stores-md",
        display_name="Stores",
        verbatim_request="Show the store counts.",
        format="html",
    )
    await svc._run_job(created["job_id"])
    with pytest.raises(AMError, match="format must be one of"):
        svc.edit(
            principal,
            artifact_id=created["artifact_id"],
            base_version=1,
            verbatim_request="Switch this page to markdown.",
            format="rtf",
        )
    edited = svc.edit(
        principal,
        artifact_id=created["artifact_id"],
        base_version=1,
        verbatim_request="Switch this page to markdown.",
        format="markdown",
    )
    await svc._run_job(edited["job_id"])
    assert all("===PATCH===" not in system for system in calls)
    art = svc.db.one("SELECT format, slug FROM artifacts WHERE id=?", created["artifact_id"])
    assert art is not None
    assert art["format"] == "markdown"
    assert art["slug"] == "stores-md"
    v1 = svc.db.one("SELECT format FROM versions WHERE artifact_id=? AND version=?", created["artifact_id"], 1)
    assert v1 is not None
    assert v1["format"] == "html"
    stored = svc.store.get(Store.prefix("alpha", created["artifact_id"], 2) + "document.md")[0]
    assert b"HARBOR-17" in stored
    assert b"<h1" not in stored.lower()
    assert str(svc.export(principal, created["artifact_id"], 1)["file"]).endswith(".html")
