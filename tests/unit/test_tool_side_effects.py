"""status and inspect write a preview link. export's download token does not."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from tests.unit.test_publish import FakeStore

from artifactsmith import server
from artifactsmith.config import CFG
from artifactsmith.service import ALL_PERMS, Service, unsign

_TABLES = ("artifacts", "versions", "jobs", "idempotency", "shares", "delete_tokens", "short_links")
_HTML = "<html><body><p>Harbor note</p></body></html>"


def _counts(svc: Service) -> dict[str, int]:
    return {name: int(svc.db.must(f"SELECT COUNT(*) n FROM {name}")["n"]) for name in _TABLES}


def _diff(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {name: after[name] - before[name] for name in before if after[name] != before[name]}


@pytest.fixture
def svc(monkeypatch: pytest.MonkeyPatch) -> Service:
    monkeypatch.setattr("artifactsmith.service.Store", FakeStore)
    return Service()


@pytest.fixture
def principal(monkeypatch: pytest.MonkeyPatch, svc: Service) -> dict[str, Any]:
    who = {"id": "tok_1", "name": "agent", "workspace": "alpha", "perms": set(ALL_PERMS)}
    monkeypatch.setattr(server, "SVC", svc)
    monkeypatch.setattr(server, "_principal", lambda _ctx: who)
    return who


def _puts(svc: Service) -> dict[str, int]:
    store = svc.store
    seen = {"n": 0}
    orig = store.put

    def _put(key: str, data: bytes, content_type: str) -> str:
        seen["n"] += 1
        return orig(key, data, content_type)

    store.put = _put  # type: ignore[method-assign]
    return seen


async def _publish(svc: Service, principal: dict[str, Any], slug: str) -> dict[str, Any]:
    created = svc.publish(principal, display_name="Harbor", slug=slug, html=_HTML)
    await svc._run_job(str(created["job_id"]))
    return created


@pytest.mark.asyncio
async def test_done_status_wait_inserts_preview_and_reuses_it(svc: Service, principal: dict[str, Any]) -> None:
    created = await _publish(svc, principal, "harbor-status")
    aid = str(created["artifact_id"])
    puts = _puts(svc)
    keys = set(svc.store.objects)
    svc.db.exec("INSERT INTO short_links VALUES (?,?,?,?,?)", "expired-link", "art_other", 1, "preview", 1)
    before = _counts(svc)

    started = time.monotonic()
    card = await server.status(None, job_id=str(created["job_id"]), wait=90)  # type: ignore[arg-type]
    assert time.monotonic() - started < 5
    assert card["status"] == "done"
    assert puts["n"] == 0
    assert set(svc.store.objects) == keys
    # The expired row is removed and one live preview row replaces it.
    assert _counts(svc) == before
    assert svc.db.one("SELECT id FROM short_links WHERE id=?", "expired-link") is None
    sid = str(card["preview_url"]).rsplit("/", 1)[-1]
    live = svc.short_lookup(sid)
    assert live is not None and live["artifact_id"] == aid

    svc.db.exec("INSERT INTO short_links VALUES (?,?,?,?,?)", "still-expired", "art_other", 1, "preview", 1)
    again = _counts(svc)
    second = await server.status(None, artifact_id=aid, wait=90)  # type: ignore[arg-type]
    assert second["preview_url"] == card["preview_url"]
    assert _counts(svc) == again
    assert svc.db.one("SELECT id FROM short_links WHERE id=?", "still-expired") is not None
    assert puts["n"] == 0


@pytest.mark.asyncio
async def test_waiting_on_an_unfinished_job_does_not_write(
    svc: Service, principal: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    created = svc.publish(principal, display_name="Harbor", slug="harbor-queued", html=_HTML)
    aid = str(created["artifact_id"])
    puts = _puts(svc)
    keys = set(svc.store.objects)
    before = _counts(svc)
    clock = {"t": time.time()}
    slept = {"n": 0}

    def _now() -> float:
        return clock["t"]

    async def _sleep(seconds: float) -> None:
        slept["n"] += 1
        clock["t"] += seconds

    monkeypatch.setattr("artifactsmith.service.now", _now)
    monkeypatch.setattr("artifactsmith.service.asyncio.sleep", _sleep)
    card = await server.status(None, job_id=str(created["job_id"]), wait=90)  # type: ignore[arg-type]

    assert slept["n"] >= 1
    assert card["status"] == "queued"
    assert "preview_url" not in card
    body = server.inspect(None, aid, version=1, include_content=True)  # type: ignore[arg-type]
    assert "content" not in body
    listed = server.list_artifacts(None)  # type: ignore[arg-type]
    assert listed["count"] == 1
    assert _counts(svc) == before
    assert puts["n"] == 0
    assert set(svc.store.objects) == keys


@pytest.mark.asyncio
async def test_inspect_include_content_reads_text_and_inserts_preview(svc: Service, principal: dict[str, Any]) -> None:
    created = await _publish(svc, principal, "harbor-inspect")
    aid = str(created["artifact_id"])
    puts = _puts(svc)
    keys = set(svc.store.objects)
    before = _counts(svc)

    body = server.inspect(None, aid, include_content=True)  # type: ignore[arg-type]
    assert "error" not in body
    assert "Harbor note" in body["content"]["text"]
    assert body["content"]["size_bytes"] == len(body["content"]["text"].encode())
    assert puts["n"] == 0
    assert set(svc.store.objects) == keys
    assert _diff(before, _counts(svc)) == {"short_links": 1}
    sid = str(body["card"]["preview_url"]).rsplit("/", 1)[-1]
    assert svc.short_lookup(sid) is not None


@pytest.mark.asyncio
async def test_export_mints_hmac_without_a_stored_grant(svc: Service, principal: dict[str, Any]) -> None:
    created = await _publish(svc, principal, "harbor-export")
    aid = str(created["artifact_id"])
    puts = _puts(svc)
    keys = set(svc.store.objects)
    before = _counts(svc)
    audit_before = _audit_lines()
    started = time.time()

    body = server.export(None, aid)  # type: ignore[arg-type]

    payload = unsign(str(body["download_url"]).rsplit("/", 1)[-1])
    assert payload is not None
    assert payload["a"] == aid
    assert int(payload["v"]) == 1
    assert 890 <= int(payload["exp"]) - started <= 901
    assert body["expires_in"] == "15m"
    assert _counts(svc) == before
    assert puts["n"] == 0
    assert set(svc.store.objects) == keys
    added = [json.loads(line) for line in _audit_lines()[len(audit_before) :]]
    assert len(added) == 1
    assert added[0]["action"] == "export"
    assert added[0]["artifact_id"] == aid
    assert added[0]["version"] == 1


def _audit_lines() -> list[str]:
    path = CFG.audit_path
    if not path.exists():
        return []
    return path.read_text().splitlines()
