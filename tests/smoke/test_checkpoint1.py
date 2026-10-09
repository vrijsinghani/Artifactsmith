#!/usr/bin/env python3
"""Checkpoint 1 end-to-end smoke test. Run against the compose stack:

    docker compose --profile test up -d --build
    python tests/smoke/test_checkpoint1.py

Exits 0 when every step passes. Uses a real MCP client (mcp streamable-http) plus httpx for the
preview/share origins, and provisions tokens through the operator CLI inside the server container.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

REPO = Path(__file__).resolve().parents[2]
API = "http://127.0.0.1:8780"
PREVIEW = "http://127.0.0.1:8781"
MCP_URL = f"{API}/mcp"


class Fail(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise Fail(msg)
    print(f"  ok: {msg}")


def compose(*args, capture=True) -> str:
    r = subprocess.run(["docker", "compose", *args], cwd=REPO, capture_output=True, text=True)
    if r.returncode != 0:
        raise Fail(f"docker compose {' '.join(args)} failed: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def provision(name: str, workspace: str, perms: str) -> str:
    out = compose(
        "exec",
        "-T",
        "server",
        "artifactsmith",
        "token",
        "add",
        "--name",
        name,
        "--workspace",
        workspace,
        "--perms",
        perms,
    )
    # the CLI prints a single JSON object; take the last non-empty JSON line
    for line in reversed(out.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            obj = json.loads(line)
            check("token" in obj, f"provisioned token for {name} (workspace={workspace}, perms={perms})")
            return obj["token"]
    raise Fail(f"could not parse token add output: {out!r}")


def parse(res) -> dict:
    if res.structuredContent is not None:
        return res.structuredContent
    if res.content and getattr(res.content[0], "text", None):
        return json.loads(res.content[0].text)
    return {"error": "no content"}


async def call(session, name, args, read_timeout: int = 120):
    from datetime import timedelta

    res = await session.call_tool(name, args, read_timeout_seconds=timedelta(seconds=read_timeout))
    return parse(res)


async def wait_health(url: str, tries: int = 90):
    for _ in range(tries):
        try:
            if httpx.get(f"{url}/healthz", timeout=3).status_code == 200:
                return
        except Exception:
            pass
        await asyncio.sleep(1)
    raise Fail(f"server never became healthy at {url}")


async def phase(token, fn):
    async with streamablehttp_client(
        MCP_URL, headers={"Authorization": f"Bearer {token}"}, timeout=60, sse_read_timeout=120
    ) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return await fn(s)


async def main() -> int:
    await wait_health(API)
    print("server healthy")

    run_id = f"{int(time.time())}{os.getpid() % 1000}"
    tok_alpha = provision("alpha-agent", "alpha", "create,read,edit,export,share,delete")
    tok_read = provision("read-only", "alpha", "read")
    tok_beta = provision("beta-agent", "beta", "create,read,edit,export,share,delete")

    fact1 = "HARBOR-17"
    fact2 = "HARBOR-24"

    # ---- steps 1-3: create, private preview contains the fact, edit -> new fact, stale edit refused ----
    state: dict = {}

    async def early(s):
        print("\n[1] create (verbatim request + source with a unique fact)")
        created = await call(
            s,
            "create",
            {
                "slug": f"pilot-store-{run_id}",
                "display_name": "Pilot Store",
                "kind": "web_static",
                "verbatim_request": "Show the pilot store code.",
                "source_content": f"The pilot store code is {fact1}.",
                "idempotency_key": "ckpt1-create-1",
            },
        )
        check(
            "artifact_id" in created and created.get("status") == "queued",
            "create returns artifact_id/job_id immediately",
        )
        aid, v1 = created["artifact_id"], created["version"]

        # idempotency: same key replays the same artifact_id/version, no duplicate
        replay = await call(
            s,
            "create",
            {
                "slug": f"pilot-store-{run_id}",
                "display_name": "Pilot Store",
                "kind": "web_static",
                "verbatim_request": "Show the pilot store code.",
                "source_content": f"The pilot store code is {fact1}.",
                "idempotency_key": "ckpt1-create-1",
            },
        )
        check(
            replay.get("idempotent_replay") and replay.get("artifact_id") == aid,
            "idempotency key replays instead of creating a second artifact",
        )

        print("\n[2] status(wait) -> done; page contains the fact; still private")
        st = await call(s, "status", {"job_id": created["job_id"], "wait": 90})
        check(st.get("status") == "done", "build finished as done")
        page = httpx.get(st["preview_url"], timeout=10)
        check(page.status_code == 200 and fact1 in page.text, f"private preview contains {fact1}")
        check(
            "script-src 'none'" in page.headers.get("content-security-policy", ""),
            "CSP blocks scripts (script-src 'none')",
        )
        check(st.get("share", {}).get("state") == "not_shared", "create did NOT publish a share link")
        probe = httpx.get(f"{PREVIEW}/s/deadbeefcafe1234/", timeout=10)
        check(probe.status_code == 404, "no public /s/ link yet (guess is 404)")

        print("\n[3] edit from v1 -> new fact; still private; stale edit refused")
        ed = await call(
            s,
            "edit",
            {
                "artifact_id": aid,
                "base_version": v1,
                "verbatim_request": "Update the store code.",
                "source_content": f"The new pilot store code is {fact2}.",
            },
        )
        check("job_id" in ed, "edit queued a new version")
        st2 = await call(s, "status", {"job_id": ed["job_id"], "wait": 90})
        check(st2.get("status") == "done" and st2.get("version") == v1 + 1, "edit produced a new done version")
        page2 = httpx.get(st2["preview_url"], timeout=10)
        check(fact2 in page2.text, f"new page contains {fact2}")
        check(st2.get("share", {}).get("state") == "not_shared", "edit is still private")
        stale = await call(
            s,
            "edit",
            {
                "artifact_id": aid,
                "base_version": v1,
                "verbatim_request": "stale edit",
                "source_content": f"x {fact1}",
            },
        )
        check("error" in stale and "stale" in stale["error"], "edit refuses a stale base_version")
        state.update(aid=aid, v1=v1, v2=st2["version"])
        return state

    await phase(tok_alpha, early)
    aid = state["aid"]

    # ---- steps 4-5: share -> 200 page; unshare -> 404 ----
    async def share_phase(s):
        print("\n[4] share -> public URL returns 200 and the page")
        sh = await call(s, "share", {"artifact_id": aid})
        check(sh.get("state") == "shared" and sh.get("url"), "share returns a public URL")
        r = httpx.get(sh["url"], timeout=10)
        check(r.status_code == 200 and fact2 in r.text, "public share URL serves the page (200)")
        state["share_url"] = sh["url"]

        print("\n[5] unshare -> URL is 404")
        un = await call(s, "unshare", {"artifact_id": aid})
        check(un.get("state") == "not_shared", "unshare revokes")
        r2 = httpx.get(state["share_url"], timeout=10)
        check(r2.status_code == 404, "revoked share URL is 404")
        return True

    await phase(tok_alpha, share_phase)

    # ---- step 6: restart the server container; artifact persists, no rebuild ----
    print("\n[6] restart server container; inspect works, no rebuild")
    compose("restart", "server")
    await wait_health(API)

    async def restart_phase(s):
        before = await call(s, "inspect", {"artifact_id": aid})
        check(any(h["status"] == "done" for h in before.get("history", [])), "artifact still present after restart")
        vs_before = {h["version"] for h in before["history"]}
        # give any stray worker a moment; versions must not change (no rebuild)
        await asyncio.sleep(3)
        after = await call(s, "inspect", {"artifact_id": aid})
        vs_after = {h["version"] for h in after["history"]}
        check(vs_before == vs_after, "no rebuild happened on restart (versions unchanged)")
        return True

    await phase(tok_alpha, restart_phase)

    # ---- step 7: permissions -- cross-workspace, missing-rights, and unauthenticated are denied ----
    print("\n[7] permissions: other workspace / missing rights / unauthenticated cannot act")

    async def cross_ws(s):
        for tool, args in [
            ("inspect", {"artifact_id": aid}),
            ("export", {"artifact_id": aid}),
            ("share", {"artifact_id": aid}),
            ("edit", {"artifact_id": aid, "base_version": state["v2"], "verbatim_request": "x", "source_content": "y"}),
            ("delete", {"artifact_id": aid}),
        ]:
            r = await call(s, tool, args)
            check(
                "error" in r and "forbidden" in r["error"],
                f"token in workspace 'beta' is denied from {tool} (knowing the id is not access)",
            )

    await phase(tok_beta, cross_ws)

    async def read_only(s):
        for tool, args in [
            ("share", {"artifact_id": aid}),
            ("edit", {"artifact_id": aid, "base_version": state["v2"], "verbatim_request": "x", "source_content": "y"}),
            ("export", {"artifact_id": aid}),
            ("delete", {"artifact_id": aid}),
        ]:
            r = await call(s, tool, args)
            check("error" in r, f"read-only token is denied from {tool}")
        # but reading works
        r = await call(s, "inspect", {"artifact_id": aid})
        check("artifact_id" in r, "read-only token can read (inspect)")

    await phase(tok_read, read_only)

    # unauthenticated HTTP must be 401
    resp = httpx.post(
        MCP_URL,
        json={},
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        timeout=10,
    )
    check(resp.status_code == 401, "unauthenticated call to the MCP API is HTTP 401")

    # ---- step 8: a create that needs a missing fact -> needs_input, no done page ----
    print("\n[8] create whose needed fact is not supplied -> needs_input, no page")

    async def needs_input(s):
        created = await call(
            s,
            "create",
            {
                "slug": f"missing-fact-{run_id}",
                "display_name": "Missing Fact",
                "kind": "web_static",
                "verbatim_request": "Report the launch revenue figure. FACTS_MISSING",
                "source_content": "There was a launch last quarter.",
            },
        )
        check("job_id" in created, "needs-input create returns a job_id")
        st = await call(s, "status", {"job_id": created["job_id"], "wait": 90})
        check(st.get("status") == "needs_input", "status is needs_input")
        check("preview_url" not in st, "no page/preview written for a needs_input build")
        insp = await call(s, "inspect", {"artifact_id": created["artifact_id"]})
        check(all(h["status"] != "done" for h in insp.get("history", [])), "no done version stored")
        return True

    await phase(tok_alpha, needs_input)

    print("\nCHECKPOINT 1 PASSED")
    return 0


def _show(exc):
    import traceback

    traceback.print_exception(type(exc), exc, exc.__traceback__)
    for sub in getattr(exc, "exceptions", []):
        print("SUB-EXC:")
        _show(sub)


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Fail as e:
        print(f"\nCHECKPOINT 1 FAILED: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:  # noqa: BLE001  (also catches ExceptionGroup)
        print(f"\nCHECKPOINT 1 ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        _show(e)
        sys.exit(1)
