"""Hosted trial: model lock, spend hold, one slot, and sign-up."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from html import escape, unescape
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient
from tests.unit.test_service import FakeStore, _mark_done, _principal

import artifactsmith.server as server
from artifactsmith import llm, trial
from artifactsmith.builder import BuildResult
from artifactsmith.config import CFG
from artifactsmith.service import AMError, Service
from artifactsmith.store import Store

MODEL = "anthropic/claude-haiku-5-5"


def _trial(pid: str = "tok_trial", workspace: str = "alpha") -> dict:
    return {
        "id": pid,
        "name": "trial",
        "workspace": workspace,
        "perms": set(trial.TRIAL_PERMS + ["publish"]),
        "trial": True,
    }


def _token(
    svc: Service, pid: str = "tok_trial", *, kind: str = "trial", workspace: str = "alpha", created: float | None = None
) -> None:
    svc.db.exec(
        "INSERT INTO tokens (id,token_hash,name,workspace,perms,created_at,revoked_at,kind) "
        "VALUES (?,?,?,?,?,?,NULL,?)",
        pid,
        "hash-" + pid,
        kind,
        workspace,
        "[]",
        time.time() if created is None else created,
        kind,
    )


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setattr("artifactsmith.service.Store", FakeStore)
    return Service()


@pytest.fixture
def trial_on(monkeypatch):
    monkeypatch.setattr(CFG, "trial_enabled", True)
    monkeypatch.setattr(CFG, "trial_model", MODEL)
    monkeypatch.setattr(CFG, "trial_max_output_tokens", 16_000)
    monkeypatch.setattr(CFG, "trial_build_limit", 20)
    monkeypatch.setattr(CFG, "trial_builds_per_hour", 10)
    monkeypatch.setattr(CFG, "trial_daily_usd", "10")
    monkeypatch.setattr(CFG, "trial_price_in", "0.10")
    monkeypatch.setattr(CFG, "trial_price_out", "0.50")
    monkeypatch.setattr(CFG, "builds_per_hour", 20)


def _create(svc: Service, principal: dict, slug: str, **kw):
    return svc.create(principal, slug=slug, display_name=slug, verbatim_request="show the page", **kw)


def test_gmail_dots_and_tags_are_one_address():
    assert trial.normalize_email("J.o.E+Tag@GoogleMail.com") == "joe@gmail.com"
    assert trial.normalize_email("joe@gmail.com") == "joe@gmail.com"
    assert trial.email_hash("joe@gmail.com") == trial.email_hash(trial.normalize_email("j.o.e+x@gmail.com"))
    assert trial.normalize_email("a.b+c@example.com") == "a.b+c@example.com"
    with pytest.raises(ValueError):
        trial.normalize_email("bad\n@example.com")
    with pytest.raises(ValueError):
        trial.normalize_email("not-an-email")


def test_prices_and_worst_case_hold():
    assert trial.micros_for(1, 0) == 1
    assert trial.worst_case_micros() == 84_000
    assert trial.cap_micros() == 10_000_000


def test_prices_come_from_env(monkeypatch):
    monkeypatch.setattr(CFG, "trial_price_in", "1")
    monkeypatch.setattr(CFG, "trial_price_out", "2")
    assert trial.micros_for(3, 4) == 11


def test_one_address_mints_once(svc):
    digest = trial.email_hash(trial.normalize_email("j.o.e+tag@gmail.com"))
    first = trial.mint_trial(svc.db, digest)
    assert first is not None
    raw, workspace = first
    assert raw.startswith("asmb_") and workspace.startswith("t")
    again = trial.email_hash(trial.normalize_email("joe@gmail.com"))
    assert trial.mint_trial(svc.db, again) is None
    row = svc.db.one("SELECT kind FROM tokens WHERE workspace=?", workspace)
    assert row["kind"] == "trial"
    assert "publish" not in svc.db.one("SELECT perms FROM tokens WHERE workspace=?", workspace)["perms"]


def test_disposable_domain_is_rejected():
    assert trial.is_disposable(trial.normalize_email("a@mailinator.com"))
    assert not trial.is_disposable(trial.normalize_email("a@example.com"))


def test_trial_model_ignores_the_caller(svc, trial_on):
    created = _create(svc, _trial(), "locked", model="gpt-4o")
    assert created["model"] == MODEL
    assert created["builds_remaining"] == 19
    stored = svc.db.one("SELECT model FROM versions WHERE artifact_id=?", created["artifact_id"])
    assert stored["model"] == MODEL
    svc.db.exec("UPDATE versions SET status='done' WHERE job_id=?", created["job_id"])
    edited = svc.edit(
        _trial(),
        base_version=1,
        verbatim_request="change the page",
        artifact_id=created["artifact_id"],
        model="gpt-4o-mini",
    )
    assert edited["model"] == MODEL
    assert edited["builds_remaining"] == 18


def test_trial_cannot_publish(svc, trial_on):
    with pytest.raises(AMError, match="trial tokens cannot publish"):
        svc.publish(_trial(), html="<p>x</p>", slug="page", display_name="Page")
    try:
        svc.publish(_principal(), html="<p>x</p>", slug="page", display_name="Page")
    except AMError as exc:
        assert "cannot publish" not in str(exc)


def test_lifetime_quota_and_hourly_limit_stay_separate(svc, trial_on, monkeypatch):
    monkeypatch.setattr(CFG, "trial_build_limit", 1)
    first = _create(svc, _trial(), "only")
    assert first["builds_remaining"] == 0
    with pytest.raises(AMError, match="Self-host ArtifactSmith") as caught:
        _create(svc, _trial(), "second")
    assert "github.com/vrijsinghani/artifactsmith#readme" in str(caught.value)
    assert "127.0.0.1" not in str(caught.value)
    internal = _create(svc, _principal(), "internal")
    assert "builds_remaining" not in internal

    monkeypatch.setattr(CFG, "trial_build_limit", 20)
    monkeypatch.setattr(CFG, "trial_builds_per_hour", 1)
    monkeypatch.setattr(CFG, "builds_per_hour", 2)
    other = _trial(pid="tok_hour")
    _create(svc, other, "hour-1")
    with pytest.raises(AMError, match="quota: 1 builds/hour reached"):
        _create(svc, other, "hour-2")
    _create(svc, _principal(pid="tok_fast"), "fast-1")
    _create(svc, _principal(pid="tok_fast"), "fast-2")
    with pytest.raises(AMError, match="quota: 2 builds/hour reached"):
        _create(svc, _principal(pid="tok_fast"), "fast-3")


def test_kill_switch_blocks_trial_only(svc, monkeypatch):
    monkeypatch.setattr(CFG, "trial_enabled", False)
    with pytest.raises(AMError, match="trial is closed"):
        _create(svc, _trial(), "closed")
    assert _create(svc, _principal(), "open")["status"] == "queued"


def test_daily_cap_holds_worst_case_then_settles(svc, trial_on, monkeypatch):
    monkeypatch.setattr(CFG, "trial_daily_usd", "0.10")
    _token(svc)
    first = _create(svc, _trial(), "held", model="gpt-4o")
    held = svc.db.one("SELECT reserved_micros, actual_micros FROM trial_reservations WHERE job_id=?", first["job_id"])
    assert held["reserved_micros"] == 84_000
    assert held["actual_micros"] is None
    with pytest.raises(AMError, match="trial capacity reached for today"):
        _create(svc, _trial(), "over")
    assert svc.db.one("SELECT COUNT(*) AS n FROM jobs WHERE id!=?", first["job_id"])["n"] == 0
    internal = _create(svc, _principal(), "still-ok")
    assert internal["status"] == "queued"
    assert svc.db.one("SELECT 1 AS n FROM trial_reservations WHERE job_id=?", internal["job_id"]) is None

    trial.settle(svc.db, first["job_id"], [(10_000_000, 10_000_000, True)])
    clamped = svc.db.one("SELECT actual_micros FROM trial_reservations WHERE job_id=?", first["job_id"])
    assert clamped["actual_micros"] == 84_000
    usage = svc.db.one("SELECT model, input_tokens FROM trial_usage WHERE job_id=?", first["job_id"])
    assert usage["model"] == MODEL
    assert usage["input_tokens"] == 10_000_000

    trial.settle(svc.db, first["job_id"], [(1, 1, True)])
    assert (
        svc.db.one("SELECT actual_micros FROM trial_reservations WHERE job_id=?", first["job_id"])["actual_micros"]
        == 84_000
    )


def test_unknown_usage_keeps_the_hold(svc, trial_on, monkeypatch):
    monkeypatch.setattr(CFG, "trial_daily_usd", "0.10")
    _create(svc, _trial(), "one")
    job = svc.db.one("SELECT id FROM jobs")
    trial.settle(svc.db, job["id"], [(0, 0, False)])
    row = svc.db.one("SELECT reserved_micros, actual_micros FROM trial_reservations WHERE job_id=?", job["id"])
    assert row["actual_micros"] == row["reserved_micros"]
    assert svc.db.one("SELECT COUNT(*) AS n FROM trial_usage")["n"] == 0
    with pytest.raises(AMError, match="resets at 00:00 UTC"):
        _create(svc, _trial(pid="tok_b"), "two")


def test_settled_cost_frees_room_for_another_trial(svc, trial_on, monkeypatch):
    monkeypatch.setattr(CFG, "trial_daily_usd", "0.10")
    first = _create(svc, _trial(), "one")
    with pytest.raises(AMError, match="trial capacity reached"):
        _create(svc, _trial(), "two")
    trial.settle(svc.db, first["job_id"], [(100, 20, True)])
    actual = trial.micros_for(100, 20)
    assert (
        svc.db.one("SELECT actual_micros FROM trial_reservations WHERE job_id=?", first["job_id"])["actual_micros"]
        == actual
    )
    assert actual < 84_000
    second = _create(svc, _trial(), "two")
    assert second["builds_remaining"] == 18


def test_settle_rolls_back_with_the_open_transaction(svc, trial_on):
    try:
        with svc.db.tx() as conn:
            trial.reserve(conn, "job_join", time.time(), "tok_join")
            trial.settle(svc.db, "job_join", [(1, 1, True)])
            raise RuntimeError("roll")
    except RuntimeError:
        pass
    assert svc.db.one("SELECT job_id FROM trial_reservations WHERE job_id=?", "job_join") is None
    assert svc.db.one("SELECT COUNT(*) AS n FROM trial_usage")["n"] == 0


def test_spend_cap_resets_on_the_utc_day(svc, trial_on, monkeypatch):
    monkeypatch.setattr(CFG, "trial_daily_usd", "0.10")
    stamp = 1_700_000_000.0
    with svc.db.tx() as conn:
        trial.reserve(conn, "job_a", stamp, "tok_a")
    with pytest.raises(trial.TrialLimit, match="00:00 UTC"):
        with svc.db.tx() as conn:
            trial.reserve(conn, "job_b", stamp, "tok_b")
    with svc.db.tx() as conn:
        trial.reserve(conn, "job_c", stamp + 86400, "tok_c")


def test_empty_settle_releases_a_hold(svc, trial_on, monkeypatch):
    monkeypatch.setattr(CFG, "trial_daily_usd", "0.10")
    first = _create(svc, _trial(), "one")
    trial.settle(svc.db, first["job_id"], [])
    assert (
        svc.db.one("SELECT actual_micros FROM trial_reservations WHERE job_id=?", first["job_id"])["actual_micros"] == 0
    )
    assert _create(svc, _trial(), "two")["status"] == "queued"


@pytest.mark.asyncio
async def test_run_locks_model_records_usage_and_releases_a_deleted_hold(svc, trial_on, monkeypatch):
    _token(svc)
    seen: dict[str, object] = {}

    async def fake_build(**kw):
        seen["model"] = kw["model"]
        sink = llm._usage.get()
        assert sink is not None
        sink.append((15, 4, True))
        return BuildResult(
            files={"index.html": b"<html><body>ok</body></html>"},
            primary="index.html",
            assumptions=["none"],
            summary="built",
            model=str(kw["model"]),
        )

    real_cap = llm.set_output_cap

    def spy(limit):
        seen["cap"] = limit
        return real_cap(limit)

    monkeypatch.setattr("artifactsmith.service.builder.run_build", fake_build)
    monkeypatch.setattr("artifactsmith.service.llm.set_output_cap", spy)
    created = _create(svc, _trial(), "run", model="gpt-4o")
    svc.db.exec("UPDATE versions SET model=? WHERE job_id=?", "gpt-4o", created["job_id"])
    await svc._run_job(created["job_id"])
    assert seen["model"] == MODEL
    assert seen["cap"] == 16_000
    usage = svc.db.one("SELECT input_tokens, output_tokens, model FROM trial_usage WHERE job_id=?", created["job_id"])
    assert usage == {"input_tokens": 15, "output_tokens": 4, "model": MODEL}
    assert svc.db.one("SELECT actual_micros FROM trial_reservations WHERE job_id=?", created["job_id"])[
        "actual_micros"
    ] == trial.micros_for(15, 4)

    deleted = _create(svc, _trial(), "gone")
    svc.db.exec("UPDATE artifacts SET deleting_at=? WHERE id=?", time.time(), deleted["artifact_id"])
    await svc._run_job(deleted["job_id"])
    assert (
        svc.db.one("SELECT actual_micros FROM trial_reservations WHERE job_id=?", deleted["job_id"])["actual_micros"]
        == 0
    )


@pytest.mark.asyncio
async def test_kill_switch_fails_a_queued_trial_and_releases_the_hold(svc, trial_on, monkeypatch):
    _token(svc)
    created = _create(svc, _trial(), "queued")
    monkeypatch.setattr(CFG, "trial_enabled", False)
    await svc._run_job(created["job_id"])
    job = svc.db.one("SELECT status, error FROM jobs WHERE id=?", created["job_id"])
    assert job["status"] == "failed"
    assert job["error"] == "trial is closed"
    assert (
        svc.db.one("SELECT actual_micros FROM trial_reservations WHERE job_id=?", created["job_id"])["actual_micros"]
        == 0
    )


@pytest.mark.asyncio
async def test_status_includes_builds_remaining(svc, trial_on):
    created = _create(svc, _trial(), "stat", model="other-model")
    out = await svc.status(_trial(), job_id=created["job_id"], wait=0)
    assert out["builds_remaining"] == 19
    internal = _create(svc, _principal(), "plain")
    plain = await svc.status(_principal(), job_id=internal["job_id"], wait=0)
    assert "builds_remaining" not in plain


@pytest.mark.asyncio
async def test_trial_uses_one_slot_and_internal_still_runs(svc, trial_on, monkeypatch):
    monkeypatch.setattr(CFG, "max_concurrent_builds", 2)
    _token(svc)
    entered: list[str] = []
    gate = asyncio.Event()

    async def hold(jid: str) -> None:
        entered.append(jid)
        await gate.wait()

    monkeypatch.setattr(svc, "_run_job", hold)
    svc.start_workers()
    try:
        first = _create(svc, _trial(), "t1")
        second = _create(svc, _trial(), "t2")
        internal = _create(svc, _principal(), "in")
        for _ in range(100):
            if len(entered) >= 2:
                break
            await asyncio.sleep(0.01)
        assert internal["job_id"] in entered
        assert len([job for job in entered if job != internal["job_id"]]) == 1
        assert len(entered) == 2
        await asyncio.sleep(0.05)
        assert len(entered) == 2
        waiting = second["job_id"] if second["job_id"] not in entered else first["job_id"]
        gate.set()
        for _ in range(100):
            if waiting in entered:
                break
            await asyncio.sleep(0.01)
        assert waiting in entered
    finally:
        gate.set()
        await svc.shutdown()


def test_trial_share_expires_within_30_days(svc, trial_on, monkeypatch):
    monkeypatch.setattr(CFG, "share_ttl_days", 0)
    _mark_done(svc, _create(svc, _trial(), "shared")["artifact_id"], 1)
    aid = svc.db.one("SELECT id FROM artifacts WHERE slug=?", "shared")["id"]
    shared = svc.share(_trial(), aid)
    assert shared["lifetime"] != "until revoked"
    exp = svc.db.one("SELECT expires_at FROM shares WHERE artifact_id=?", aid)["expires_at"]
    assert abs(exp - (time.time() + 30 * 86400)) < 30

    _mark_done(svc, _create(svc, _principal(), "ever")["artifact_id"], 1)
    internal_aid = svc.db.one("SELECT id FROM artifacts WHERE slug=?", "ever")["id"]
    internal = svc.share(_principal(), internal_aid)
    assert internal["lifetime"] == "until revoked"

    monkeypatch.setattr(CFG, "share_ttl_days", 30)
    _mark_done(svc, _create(svc, _trial(), "short")["artifact_id"], 1)
    short_aid = svc.db.one("SELECT id FROM artifacts WHERE slug=?", "short")["id"]
    svc.share(_trial(), short_aid, ttl_days=1)
    short = svc.db.one("SELECT expires_at FROM shares WHERE artifact_id=?", short_aid)["expires_at"]
    assert abs(short - (time.time() + 86400)) < 30


def test_trial_chrome_adds_noindex_and_abuse_link(svc, trial_on, monkeypatch):
    monkeypatch.setattr(CFG, "trial_abuse_email", "abuse@example.com")
    _token(svc, workspace="trialws")
    created = _create(svc, _trial(workspace="trialws"), "page")
    server.SVC = svc
    try:
        html = b"<html><head><title>Hi</title></head><body><p>Hi</p></body></html>"
        stamped = server._trial_chrome(created["artifact_id"], html)
        assert b'content="noindex"' in stamped
        assert b"mailto:abuse@example.com" in stamped
        assert b"Report abuse" in stamped
        assert html != stamped
        again = server._trial_chrome(created["artifact_id"], stamped)
        assert again.count(b"noindex") == 1
        plain = _create(svc, _principal(), "staff")
        assert server._trial_chrome(plain["artifact_id"], html) == html
    finally:
        server.SVC = None
    monkeypatch.setattr(CFG, "trial_abuse_email", "not an email")
    bare = trial.stamp_trial_chrome(b"<html><body>x</body></html>", trial.abuse_email())
    assert b"mailto:" not in bare
    assert b"Trial page." in bare
    hostile = (
        b"<html><head><style>footer{display:none!important}body *{display:none!important}</style></head>"
        b"<body>x</body></html>"
    )
    resistant = trial.stamp_trial_chrome(hostile, "abuse@example.com")
    assert b'id="am-trial-report"' in resistant
    assert b"display:block!important" in resistant
    assert b"visibility:visible!important" in resistant
    indexed = b'<html><head><meta name="robots" content="index,follow"></head><body>x</body></html>'
    indexed_stamped = trial.stamp_trial_chrome(indexed, "")
    assert indexed_stamped.lower().count(b"noindex") == 1
    assert b"index,follow" in indexed_stamped
    swapped = b'<html><head><meta content="noindex,nofollow" name="robots"></head><body>x</body></html>'
    assert trial.stamp_trial_chrome(swapped, "").lower().count(b"noindex") == 1


def test_idle_trial_workspace_is_deleted_after_30_days(svc, monkeypatch):
    monkeypatch.setattr(CFG, "trial_retention_days", 30)
    old = time.time() - 31 * 86400
    _token(svc, "tok_old", workspace="oldws", created=old)
    svc.db.exec(
        "INSERT INTO artifacts (id,workspace,slug,display_name,kind,format,created_by,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        "art_old",
        "oldws",
        "old",
        "Old",
        "web_static",
        "html",
        "trial",
        old,
        old,
    )
    svc.db.exec(
        "INSERT INTO versions (artifact_id,version,verbatim_request,status,job_id,created_by,created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        "art_old",
        1,
        "x",
        "done",
        "job_old",
        "trial",
        old,
    )
    svc.db.exec(
        "INSERT INTO jobs (id,artifact_id,version,client,status,created_at) VALUES (?,?,?,?,?,?)",
        "job_old",
        "art_old",
        1,
        "tok_old",
        "done",
        old,
    )
    svc.store.put(Store.prefix("oldws", "art_old") + "index.html", b"<html></html>", "text/html")
    svc.db.exec(
        "INSERT INTO web_sessions (id_hash,token_id,csrf,created_at,expires_at) VALUES (?,?,?,?,?)",
        "sess",
        "tok_old",
        "csrf",
        old,
        old + 10,
    )
    _token(svc, "tok_busy", workspace="busyws", created=old)
    svc.db.exec(
        "INSERT INTO artifacts (id,workspace,slug,display_name,kind,format,created_by,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        "art_busy",
        "busyws",
        "busy",
        "Busy",
        "web_static",
        "html",
        "trial",
        old,
        old,
    )
    svc.db.exec(
        "INSERT INTO jobs (id,artifact_id,version,client,status,created_at) VALUES (?,?,?,?,?,?)",
        "job_busy",
        "art_busy",
        1,
        "tok_busy",
        "building",
        old,
    )
    _token(svc, "tok_new", workspace="newws", created=old)
    svc.db.exec(
        "INSERT INTO artifacts (id,workspace,slug,display_name,kind,format,created_by,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        "art_new",
        "newws",
        "new",
        "New",
        "web_static",
        "html",
        "trial",
        old,
        old,
    )
    svc.db.exec(
        "INSERT INTO jobs (id,artifact_id,version,client,status,created_at) VALUES (?,?,?,?,?,?)",
        "job_new",
        "art_new",
        1,
        "tok_new",
        "done",
        time.time(),
    )
    removed = trial.purge_idle_trials(svc.db, svc.store)
    assert removed == 1
    assert svc.db.one("SELECT revoked_at FROM tokens WHERE id=?", "tok_old")["revoked_at"] is not None
    assert svc.db.one("SELECT id FROM artifacts WHERE id=?", "art_old") is None
    assert svc.db.one("SELECT artifact_id FROM versions WHERE artifact_id=?", "art_old") is None
    assert svc.db.one("SELECT id_hash FROM web_sessions WHERE token_id=?", "tok_old") is None
    assert Store.prefix("oldws", "art_old") + "index.html" not in svc.store.objects
    assert svc.db.one("SELECT revoked_at FROM tokens WHERE id=?", "tok_busy")["revoked_at"] is None
    assert svc.db.one("SELECT revoked_at FROM tokens WHERE id=?", "tok_new")["revoked_at"] is None


def test_delete_does_not_reset_trial_build_limits(svc, trial_on, monkeypatch):
    monkeypatch.setattr(CFG, "trial_build_limit", 1)
    _token(svc)
    principal = _trial()
    created = _create(svc, principal, "only")
    with pytest.raises(AMError, match="used up"):
        _create(svc, principal, "second")
    step = svc.delete(principal, created["artifact_id"])
    done = svc.delete(principal, created["artifact_id"], confirm_token=step["confirm_token"])
    assert done["deleted"] is True
    assert svc.db.one("SELECT id FROM jobs") is None
    assert svc.db.one("SELECT COUNT(*) AS n FROM trial_reservations")["n"] == 1
    with pytest.raises(AMError, match="used up"):
        _create(svc, principal, "third")

    monkeypatch.setattr(CFG, "trial_build_limit", 20)
    monkeypatch.setattr(CFG, "trial_builds_per_hour", 1)
    other = _trial(pid="tok_hour", workspace="hourws")
    _token(svc, "tok_hour", workspace="hourws")
    made = _create(svc, other, "hour-1")
    confirm = svc.delete(other, made["artifact_id"])
    svc.delete(other, made["artifact_id"], confirm_token=confirm["confirm_token"])
    with pytest.raises(AMError, match="builds/hour"):
        _create(svc, other, "hour-2")


def test_signup_ip_limit(svc, monkeypatch):
    monkeypatch.setattr(CFG, "trial_signups_per_ip", 2)
    monkeypatch.setattr(CFG, "trial_signup_window_s", 86400)
    assert not trial.signup_blocked(svc.db, "203.0.113.4")
    trial.note_signup(svc.db, "203.0.113.4")
    trial.note_signup(svc.db, "203.0.113.4")
    assert trial.signup_blocked(svc.db, "203.0.113.4")
    assert not trial.signup_blocked(svc.db, "203.0.113.5")


def test_send_magic_link_fail_closed(monkeypatch, caplog):
    monkeypatch.setattr(CFG, "smtp_host", "")
    monkeypatch.setattr(CFG, "smtp_from", "")
    with caplog.at_level(logging.ERROR):
        with pytest.raises(trial.TrialMailError, match="not available"):
            trial.send_magic_link("a@example.com", "https://trial.example/trial/verify?code=one")
    assert "AM_SMTP_HOST" in caplog.text
    assert "a@example.com" not in caplog.text

    monkeypatch.setattr(CFG, "smtp_host", "127.0.0.1")
    monkeypatch.setattr(CFG, "smtp_port", 1)
    monkeypatch.setattr(CFG, "smtp_from", "trial@example.com")
    monkeypatch.setattr(CFG, "smtp_tls", False)
    caplog.clear()
    with caplog.at_level(logging.ERROR):
        with pytest.raises(trial.TrialMailError, match="not available"):
            trial.send_magic_link("a@example.com", "https://trial.example/trial/verify?code=two")
    assert "127.0.0.1" not in caplog.text
    assert "trial email failed:" in caplog.text


def test_sign_in_mail_has_date_and_message_id(monkeypatch):
    from email.message import EmailMessage

    sent: list[EmailMessage] = []

    class _SMTP:
        def __init__(self, host: str, port: int, timeout: float = 20) -> None:
            self.host = host

        def __enter__(self) -> _SMTP:
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

        def send_message(self, message: EmailMessage) -> None:
            sent.append(message)

    monkeypatch.setattr(CFG, "smtp_host", "smtp.example")
    monkeypatch.setattr(CFG, "smtp_port", 25)
    monkeypatch.setattr(CFG, "smtp_from", "trial@example.com")
    monkeypatch.setattr(CFG, "smtp_user", "")
    monkeypatch.setattr(CFG, "smtp_tls", False)
    monkeypatch.setattr(trial.smtplib, "SMTP", _SMTP)
    trial.send_magic_link("a@example.com", "https://trial.example/trial/verify?code=magic-code-xyz")
    message = sent[0]
    assert message["Date"]
    message_id = str(message["Message-ID"])
    assert message_id.startswith("<") and message_id.endswith("@example.com>")
    assert "magic-code-xyz" not in message_id
    assert "a@example.com" not in message_id

    monkeypatch.setattr(CFG, "smtp_from", "ArtifactSmith Trial <trial@mail.example>")
    trial.send_magic_link("a@example.com", "https://trial.example/trial/verify?code=magic-code-xyz")
    assert str(sent[1]["Message-ID"]).endswith("@mail.example>")

    monkeypatch.setattr(CFG, "smtp_from", "not-an-address")
    trial.send_magic_link("a@example.com", "https://trial.example/trial/verify?code=magic-code-xyz")
    assert str(sent[2]["Message-ID"]).endswith("@localhost>")


@pytest.mark.asyncio
async def test_retention_sweep_repeats_from_the_background_loop(svc, monkeypatch):
    calls = {"n": 0}

    def sweep(db: object, store: object, now: float | None = None) -> int:
        calls["n"] += 1
        return 0

    monkeypatch.setattr(trial, "purge_idle_trials", sweep)
    monkeypatch.setattr(Service, "_retention_interval_s", lambda self: 0.01)
    svc.start_workers()
    try:
        for _ in range(50):
            if calls["n"] >= 2:
                break
            await asyncio.sleep(0.02)
        assert calls["n"] >= 2
    finally:
        await svc.shutdown()


@pytest.mark.asyncio
async def test_failed_model_call_settles_the_tokens_it_spent(svc, trial_on, monkeypatch):
    _token(svc)

    async def fake_build(**kw: object) -> BuildResult:
        sink = llm._usage.get()
        assert sink is not None
        sink.append((100, 50, True))
        raise llm.LLMError("empty model output")

    monkeypatch.setattr("artifactsmith.service.builder.run_build", fake_build)
    created = _create(svc, _trial(), "spent")
    await svc._run_job(created["job_id"])
    assert svc.db.one("SELECT actual_micros FROM trial_reservations WHERE job_id=?", created["job_id"])[
        "actual_micros"
    ] == trial.micros_for(100, 50)


def test_old_reservations_gain_token_columns(tmp_path):
    import sqlite3

    from artifactsmith.db import DB

    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE trial_reservations ("
        "job_id TEXT PRIMARY KEY, day TEXT NOT NULL, reserved_micros INTEGER NOT NULL, "
        "actual_micros INTEGER, settled_at REAL)"
    )
    conn.commit()
    conn.close()
    db = DB(path)
    cols = {str(row[1]) for row in db.conn.execute("PRAGMA table_info(trial_reservations)")}
    assert {"token_id", "created_at"} <= cols
    db.close()


@pytest.fixture
def trial_http(svc, monkeypatch):
    monkeypatch.setattr(CFG, "trial_enabled", True)
    monkeypatch.setattr(CFG, "cookie_secure", True)
    monkeypatch.setattr(CFG, "allowed_hosts", ["127.0.0.1:8780", "localhost:8780"])
    monkeypatch.setattr(CFG, "api_url", "https://trial.example")
    monkeypatch.setattr(CFG, "smtp_host", "smtp.example")
    monkeypatch.setattr(CFG, "smtp_from", "trial@example.com")
    box: dict[str, str] = {}

    def fake_send(to: str, url: str) -> None:
        box["to"] = to
        box["url"] = url

    monkeypatch.setattr("artifactsmith.web.trial_pages.send_magic_link", fake_send)
    server.SVC = svc
    client = TestClient(server.ApiApp(), base_url="https://127.0.0.1:8780")
    yield client, box
    server.SVC = None


def _csrf(client: TestClient) -> str:
    page = client.get("/trial")
    assert page.status_code == 200
    token = client.cookies.get("am_trial_csrf")
    assert token
    return str(token)


def _verify(client: TestClient, url: str):
    parsed = urlparse(url)
    code = parse_qs(parsed.query)["code"][0]
    opened = client.get(f"{parsed.path}?{parsed.query}")
    if opened.status_code != 200:
        return opened
    csrf = client.cookies.get("am_trial_csrf")
    assert csrf
    return client.post("/trial/verify", data={"csrf": csrf, "code": code})


def test_trial_page_advertises_a_public_card(trial_http):
    client, _box = trial_http
    page = client.get("/trial")
    assert page.status_code == 200
    text = page.text
    assert 'property="og:title" content="ArtifactSmith"' in text
    assert 'property="og:description" content="Agent output in, real page or PDF out. 20 free builds."' in text
    assert 'property="og:url" content="https://trial.example/trial"' in text
    assert 'property="og:image" content="https://trial.example/static/og-trial.png"' in text
    assert 'name="twitter:card" content="summary_large_image"' in text
    assert 'name="twitter:title" content="ArtifactSmith"' in text
    assert 'name="twitter:description" content="Agent output in, real page or PDF out. 20 free builds."' in text
    assert 'name="twitter:image" content="https://trial.example/static/og-trial.png"' in text
    assert "content=noindex" in text
    assert "127.0.0.1" not in text
    assert "localhost" not in text
    assert "192.168." not in text


@pytest.mark.parametrize("origin", ["http://127.0.0.1:8780", "https://192.168.0.8", "https://localhost"])
def test_trial_page_omits_preview_tags_for_a_private_origin(trial_http, monkeypatch, origin):
    monkeypatch.setattr(CFG, "api_url", origin)
    client, _box = trial_http
    page = client.get("/trial")
    assert page.status_code == 200
    assert "og:url" not in page.text
    assert "og:image" not in page.text
    assert "content=noindex" in page.text


def test_my_pages_login_has_no_trial_card(svc, monkeypatch):
    monkeypatch.setattr(CFG, "allowed_hosts", ["127.0.0.1:8780", "localhost:8780"])
    monkeypatch.setattr(CFG, "api_url", "https://trial.example")
    server.SVC = svc
    try:
        client = TestClient(server.ApiApp(), base_url="https://127.0.0.1:8780")
        page = client.get("/app/login")
    finally:
        server.SVC = None
    assert page.status_code == 200
    assert "og:image" not in page.text
    assert "summary_large_image" not in page.text


def test_trial_card_is_a_public_png(trial_http):
    client, _box = trial_http
    image = client.get("/static/og-trial.png")
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/png")
    assert "www-authenticate" not in image.headers
    assert image.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert image.content[12:16] == b"IHDR"
    width = int.from_bytes(image.content[16:20], "big")
    height = int.from_bytes(image.content[20:24], "big")
    assert (width, height) == (1200, 630)
    assert "public" in image.headers["cache-control"]


def test_trial_card_rejects_a_foreign_host(svc, monkeypatch):
    monkeypatch.setattr(CFG, "allowed_hosts", ["127.0.0.1:8780"])
    server.SVC = svc
    try:
        bad = TestClient(server.ApiApp(), base_url="http://evil.example")
        denied = bad.get("/static/og-trial.png")
    finally:
        server.SVC = None
    assert denied.status_code == 400


def test_other_static_paths_are_not_public(trial_http):
    client, _box = trial_http
    other = client.get("/static/other.png")
    assert other.status_code == 401
    assert "www-authenticate" in other.headers


def test_magic_link_shows_the_token_once(trial_http, svc):
    client, box = trial_http
    csrf = _csrf(client)
    posted = client.post("/trial", data={"csrf": csrf, "email": "Person@Example.com"})
    assert posted.status_code == 200
    assert "sign-up link is on its way" in posted.text
    assert "Person@" not in posted.text
    assert "smtp.example" not in posted.text
    assert "127.0.0.1" not in posted.text
    assert box["to"] == "person@example.com"
    page = _verify(client, box["url"])
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    found = re.search(r'value="(asmb_[^"]+)"', page.text)
    assert found
    raw = found.group(1)
    pres = re.findall(r"<pre>(.*?)</pre>", page.text, re.S)
    titles = ("Claude Desktop", "Claude Code", "Cursor", "Generic MCP client")
    assert [f"<h2>{title}</h2>" in page.text for title in titles] == [True, True, True, True]
    assert len(pres) == 4
    desktop, code, cursor, _generic = pres
    desktop_cfg = json.loads(unescape(desktop))["mcpServers"]["artifactsmith"]
    assert desktop_cfg["args"][-2:] == ["--header", "Authorization:${AUTH_HEADER}"]
    assert desktop_cfg["env"]["AUTH_HEADER"] == "Bearer YOUR_TOKEN"
    assert all("Bearer " not in arg for arg in desktop_cfg["args"])
    code_cfg = json.loads(unescape(code))["mcpServers"]["artifactsmith"]
    assert code_cfg["type"] == "http"
    assert code_cfg["headers"]["Authorization"] == "Bearer YOUR_TOKEN"
    assert "mcp-remote" not in code
    cursor_cfg = json.loads(unescape(cursor))["mcpServers"]["artifactsmith"]
    assert cursor_cfg["url"].endswith("/mcp")
    assert cursor_cfg["headers"]["Authorization"] == "Bearer YOUR_TOKEN"
    assert "args" not in cursor_cfg
    assert "mcp-remote" not in cursor
    assert "Add custom connector, with URL https://trial.example/mcp and an Authorization request header." in page.text
    for pre in pres:
        assert "YOUR_TOKEN" in pre
        assert raw not in pre
        assert "https://trial.example/mcp" in pre
    assert f"{int(CFG.trial_build_limit)} free builds" in client.get("/trial").text
    assert "pre-wrap" in client.get("/trial").text
    again = _verify(client, box["url"])
    assert again.status_code == 400
    assert "already used" in again.text
    assert svc.db.one("SELECT verified FROM trial_signups")["verified"] == 1


def test_mcp_remote_args_do_not_contain_a_spaced_bearer():
    from artifactsmith.web.trial_pages import _client_snippets

    html = _client_snippets("https://artifactsmith.neuralami.ai")
    saw_remote = False
    for block in re.findall(r"<pre>(.*?)</pre>", html, re.S):
        text = unescape(block)
        if "mcp-remote" not in text:
            continue
        saw_remote = True
        for entry in json.loads(text)["mcpServers"].values():
            for arg in entry["args"]:
                assert "Bearer " not in arg
    assert saw_remote


def test_unverified_signup_shows_the_token_and_keeps_the_limits(trial_http, svc, monkeypatch):
    client, box = trial_http
    monkeypatch.setattr(CFG, "trial_email_verify", False)
    monkeypatch.setattr(CFG, "smtp_host", "")
    monkeypatch.setattr(CFG, "smtp_from", "")
    monkeypatch.setattr(CFG, "trial_signups_per_ip", 3)
    page = client.get("/trial")
    assert "Show my token" in page.text
    assert "Email me a link" not in page.text
    csrf = client.cookies.get("am_trial_csrf")
    assert csrf
    denied = client.post("/trial", data={"csrf": "nope", "email": "a@example.com"})
    assert denied.status_code == 403
    disposable = client.post("/trial", data={"csrf": csrf, "email": "a@mailinator.com"})
    assert disposable.status_code == 400
    assert "regular email" in disposable.text
    posted = client.post("/trial", data={"csrf": csrf, "email": "Person+tag@Gmail.com"})
    assert posted.status_code == 200
    assert posted.headers["cache-control"] == "no-store"
    assert "Your trial token" in posted.text
    assert "sign-up link is on its way" not in posted.text
    assert box == {}
    assert svc.db.one("SELECT COUNT(*) AS n FROM trial_links")["n"] == 0
    found = re.search(r'value="(asmb_[^"]+)"', posted.text)
    assert found
    row = svc.db.one("SELECT verified, kind FROM trial_signups JOIN tokens ON tokens.id=trial_signups.token_id")
    assert row["verified"] == 0
    assert row["kind"] == "trial"
    again = client.post("/trial", data={"csrf": csrf, "email": "person@googlemail.com"})
    assert "already has a trial" in again.text
    assert "asmb_" not in again.text
    assert svc.db.one("SELECT COUNT(*) AS n FROM trial_signups")["n"] == 1
    blocked = client.post("/trial", data={"csrf": csrf, "email": "other@example.com"})
    assert blocked.status_code == 429
    monkeypatch.setattr(CFG, "trial_enabled", False)
    closed = client.post("/trial", data={"csrf": csrf, "email": "more@example.com"})
    assert "closed" in closed.text
    assert svc.db.one("SELECT COUNT(*) AS n FROM tokens WHERE kind='trial'")["n"] == 1


def test_old_signups_count_as_verified(tmp_path):
    import sqlite3

    from artifactsmith.db import DB

    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE trial_signups (email_hash TEXT PRIMARY KEY, token_id TEXT NOT NULL, created_at REAL NOT NULL)"
    )
    conn.execute("INSERT INTO trial_signups (email_hash, token_id, created_at) VALUES ('abc', 'tok_old', 1)")
    conn.commit()
    conn.close()
    db = DB(path)
    row = db.one("SELECT verified FROM trial_signups WHERE email_hash=?", "abc")
    assert row["verified"] == 1
    db.close()


def test_get_verify_does_not_consume_the_link(trial_http, svc):
    client, box = trial_http
    csrf = _csrf(client)
    assert client.post("/trial", data={"csrf": csrf, "email": "scan@example.com"}).status_code == 200
    parsed = urlparse(box["url"])
    opened = client.get(f"{parsed.path}?{parsed.query}")
    assert opened.status_code == 200
    assert "Show my token" in opened.text
    assert "asmb_" not in opened.text
    assert svc.db.one("SELECT used_at FROM trial_links")["used_at"] is None
    blocked = client.post("/trial/verify", data={"csrf": "nope", "code": parse_qs(parsed.query)["code"][0]})
    assert blocked.status_code == 403
    assert svc.db.one("SELECT used_at FROM trial_links")["used_at"] is None
    shown = _verify(client, box["url"])
    assert "Your trial token" in shown.text
    assert svc.db.one("SELECT used_at FROM trial_links")["used_at"] is not None


def test_opaque_origin_needs_a_same_site_fetch(trial_http):
    client, _box = trial_http
    page = client.get("/trial")
    assert page.headers["referrer-policy"] == "same-origin"
    csrf = client.cookies.get("am_trial_csrf")
    assert csrf
    opaque = client.post(
        "/trial",
        data={"csrf": csrf, "email": "a@example.com"},
        headers={"origin": "null", "sec-fetch-site": "same-origin"},
    )
    assert opaque.status_code == 200
    assert "form expired" not in opaque.text.lower()
    rejected = client.post(
        "/trial",
        data={"csrf": csrf, "email": "b@example.com"},
        headers={"origin": "null"},
    )
    assert rejected.status_code == 403
    assert "form expired" in rejected.text.lower()
    foreign = client.post(
        "/trial",
        data={"csrf": csrf, "email": "c@example.com"},
        headers={"origin": "https://evil.example"},
    )
    assert foreign.status_code == 403
    login = client.get("/app/login")
    assert login.headers["referrer-policy"] == "same-origin"

    def login_post(headers: dict[str, str]):
        token = client.cookies.get("am_csrf")
        assert token
        return client.post("/app/login", data={"csrf": token, "token": "not-a-token"}, headers=headers)

    signed = login_post({"origin": "null", "sec-fetch-site": "same-origin"})
    assert signed.status_code == 401
    assert "form expired" not in signed.text.lower()
    expired = login_post({"origin": "null"})
    assert expired.status_code == 403
    assert "form expired" in expired.text.lower()
    matched = login_post({"origin": "https://127.0.0.1:8780"})
    assert matched.status_code == 401


def test_gmail_alias_cannot_mint_a_second_token(trial_http, svc):
    client, box = trial_http
    csrf = _csrf(client)
    assert client.post("/trial", data={"csrf": csrf, "email": "j.o.e+tag@googlemail.com"}).status_code == 200
    first = _verify(client, box["url"])
    assert "Your trial token" in first.text
    csrf = _csrf(client)
    assert client.post("/trial", data={"csrf": csrf, "email": "joe@gmail.com"}).status_code == 200
    second = _verify(client, box["url"])
    assert "already has a trial" in second.text
    assert "asmb_" not in second.text
    assert svc.db.one("SELECT COUNT(*) AS n FROM trial_signups")["n"] == 1


def test_disposable_email_and_ip_limit_and_bad_csrf(trial_http, monkeypatch):
    client, box = trial_http
    monkeypatch.setattr(CFG, "trial_signups_per_ip", 2)
    denied = client.post("/trial", data={"csrf": "nope", "email": "a@example.com"})
    assert denied.status_code == 403
    csrf = _csrf(client)
    invalid = client.post("/trial", data={"csrf": csrf, "email": "not-an-email"})
    assert invalid.status_code == 400
    assert "valid email" in invalid.text
    disposable = client.post("/trial", data={"csrf": client.cookies["am_trial_csrf"], "email": "a@mailinator.com"})
    assert disposable.status_code == 400
    assert "regular email" in disposable.text
    assert "url" not in box
    first = client.post("/trial", data={"csrf": client.cookies["am_trial_csrf"], "email": "one@example.com"})
    assert first.status_code == 200
    second = client.post("/trial", data={"csrf": client.cookies["am_trial_csrf"], "email": "two@example.com"})
    assert second.status_code == 429
    assert "Too many sign-ups" in second.text


@pytest.mark.asyncio
async def test_lifespan_reaches_the_mcp_app():
    app = server.ApiApp()
    seen: list[str] = []

    async def fake_api(scope, receive, send):
        seen.append(scope["type"])

    app.api = fake_api

    async def receive():
        return {"type": "lifespan.startup"}

    async def send(message):
        return None

    await app({"type": "lifespan"}, receive, send)
    assert seen == ["lifespan"]


def test_signup_closed_and_mail_failure_stay_generic(trial_http, monkeypatch):
    client, _box = trial_http
    monkeypatch.setattr(CFG, "trial_enabled", False)
    assert "closed" in client.get("/trial").text
    assert "closed" in client.post("/trial", data={"email": "a@example.com", "csrf": "x"}).text

    monkeypatch.setattr(CFG, "trial_enabled", True)

    def boom(to: str, url: str) -> None:
        raise trial.TrialMailError(trial.MAIL_UNAVAILABLE)

    monkeypatch.setattr("artifactsmith.web.trial_pages.send_magic_link", boom)
    csrf = _csrf(client)
    failed = client.post("/trial", data={"csrf": csrf, "email": "a@example.com"})
    assert failed.status_code == 503
    assert "sign-up email is not available" in failed.text
    assert "smtp" not in failed.text.lower()


def test_daily_signup_ceiling_stops_minting_and_keeps_the_link(trial_http, svc, monkeypatch):
    client, box = trial_http
    monkeypatch.setattr(CFG, "trial_signups_per_day", 1)
    svc.db.exec(
        "INSERT INTO trial_signups (email_hash, token_id, created_at, verified) VALUES (?,?,?,1)",
        "yesterday",
        "tok_y",
        time.time() - 90000,
    )
    assert "Email me a link" in client.get("/trial").text
    csrf = _csrf(client)
    posted = client.post("/trial", data={"csrf": csrf, "email": "today@example.com"})
    assert posted.status_code == 200
    assert "sign-up link is on its way" in posted.text
    assert svc.db.one("SELECT COUNT(*) AS n FROM trial_signups")["n"] == 1
    svc.db.exec(
        "INSERT INTO trial_signups (email_hash, token_id, created_at, verified) VALUES (?,?,?,1)",
        "todayhash",
        "tok_today",
        time.time(),
    )
    full = client.get("/trial")
    assert escape(trial.SPOTS_TAKEN) in full.text
    assert "Email me a link" not in full.text
    attempts = svc.db.one("SELECT COUNT(*) AS n FROM trial_signup_ips")["n"]
    blocked = client.post("/trial", data={"csrf": "nope", "email": "other@example.com"})
    assert blocked.status_code == 200
    assert escape(trial.SPOTS_TAKEN) in blocked.text
    assert svc.db.one("SELECT COUNT(*) AS n FROM trial_signups")["n"] == 2
    assert svc.db.one("SELECT COUNT(*) AS n FROM trial_signup_ips")["n"] == attempts
    parsed = urlparse(box["url"])
    code = parse_qs(parsed.query)["code"][0]
    opened = client.get(f"{parsed.path}?{parsed.query}")
    assert escape(trial.SPOTS_TAKEN) in opened.text
    assert svc.db.one("SELECT used_at FROM trial_links")["used_at"] is None
    kept = client.post("/trial/verify", data={"csrf": "x", "code": code})
    assert escape(trial.SPOTS_TAKEN) in kept.text
    assert svc.db.one("SELECT used_at FROM trial_links")["used_at"] is None
    svc.db.exec("DELETE FROM trial_signups WHERE email_hash=?", "todayhash")
    monkeypatch.setattr(CFG, "trial_email_verify", False)
    csrf = _csrf(client)
    minted = client.post("/trial", data={"csrf": csrf, "email": "mint@example.com"})
    assert "Your trial token" in minted.text
    again = client.get("/trial")
    assert escape(trial.SPOTS_TAKEN) in again.text
    second = client.post("/trial", data={"csrf": csrf, "email": "mint2@example.com"})
    assert escape(trial.SPOTS_TAKEN) in second.text
    assert "asmb_" not in second.text
    assert svc.db.one("SELECT COUNT(*) AS n FROM tokens WHERE kind='trial'")["n"] == 1
    monkeypatch.setattr(CFG, "trial_enabled", False)
    closed = client.get("/trial")
    assert "closed" in closed.text
    assert escape(trial.SPOTS_TAKEN) not in closed.text


def test_ipv6_signups_share_a_48(trial_http, svc, monkeypatch):
    import ipaddress

    _client, _box = trial_http
    monkeypatch.setattr(CFG, "trial_signups_per_ip", 1)
    monkeypatch.setattr(CFG, "trusted_proxies", (ipaddress.ip_network("192.168.30.160/32"),))
    proxy = TestClient(server.ApiApp(), base_url="https://127.0.0.1:8780", client=("192.168.30.160", 443))

    def post(addr: str, email: str):
        proxy.get("/trial", headers={"X-Real-IP": addr})
        csrf = proxy.cookies.get("am_trial_csrf")
        assert csrf
        return proxy.post("/trial", data={"csrf": csrf, "email": email}, headers={"X-Real-IP": addr})

    first = post("2001:db8:1:2:aaaa::1", "one@example.com")
    assert first.status_code == 200
    assert "sign-up link is on its way" in first.text
    same_48 = post("2001:db8:1:3::1", "two@example.com")
    assert same_48.status_code == 429
    other_48 = post("2001:db8:2::1", "three@example.com")
    assert other_48.status_code == 200
    stored = str(svc.db.all("SELECT ip FROM trial_signup_ips"))
    assert "2001:db8:1::/48" in stored
    assert "2001:db8:2::/48" in stored
    assert "aaaa" not in stored
    assert "/64" not in stored


@pytest.mark.asyncio
async def test_trial_tool_list_omits_publish():
    from mcp.server.lowlevel.server import request_ctx
    from mcp.shared.context import RequestContext

    class _Request:
        def __init__(self, principal: object) -> None:
            self.scope = {"am_principal": principal}

    async def names(request: object | None) -> list[str]:
        token = request_ctx.set(
            RequestContext(request_id="1", meta=None, session=None, lifespan_context=None, request=request)
        )
        try:
            return [tool.name for tool in await server.mcp.list_tools()]
        finally:
            request_ctx.reset(token)

    assert "publish" in [tool.name for tool in await server.mcp.list_tools()]
    hidden = await names(_Request({"trial": True}))
    assert "publish" not in hidden
    assert "create" in hidden
    assert "publish" in await names(_Request({"trial": False}))
    assert "publish" in await names(_Request({}))
    assert "publish" in await names(None)
