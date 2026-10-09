#!/usr/bin/env python3
"""Format smoke: create markdown/pdf/docx/xlsx against the compose mock-llm stack."""

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
MCP_URL = f"{API}/mcp"
FORMATS = ("markdown", "pdf", "docx", "xlsx")
MAGIC = {
    "markdown": b"# ",
    "pdf": b"%PDF",
    "docx": b"PK",
    "xlsx": b"PK",
}


class Fail(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise Fail(msg)
    print(f"  ok: {msg}")


def compose(*args) -> str:
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
    for line in reversed(out.strip().splitlines()):
        if line.strip().startswith("{"):
            return json.loads(line)["token"]
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


async def main() -> int:
    await wait_health(API)
    run_id = f"{int(time.time())}{os.getpid() % 1000}"
    token = provision(f"fmt-{run_id}", "formats", "create,read,edit,export,share,delete")

    async with streamablehttp_client(
        MCP_URL,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
        sse_read_timeout=120,
    ) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for fmt in FORMATS:
                created = await call(
                    s,
                    "create",
                    {
                        "slug": f"fmt-{fmt}-{run_id}",
                        "display_name": f"Format {fmt}",
                        "kind": "web_static",
                        "format": fmt,
                        "verbatim_request": "Show the pilot store code.",
                        "source_content": "The pilot store code is HARBOR-17.",
                    },
                )
                check("job_id" in created, f"{fmt}: create queued")
                st = await call(s, "status", {"job_id": created["job_id"], "wait": 90})
                check(st.get("status") == "done", f"{fmt}: build done")
                check(st.get("format") == fmt, f"{fmt}: format recorded")
                exported = await call(s, "export", {"artifact_id": created["artifact_id"]})
                check("download_url" in exported, f"{fmt}: export url")
                body = httpx.get(exported["download_url"], timeout=20).content
                check(body.startswith(MAGIC[fmt]), f"{fmt}: magic {MAGIC[fmt]!r}")
                if fmt == "markdown":
                    check(b"HARBOR-17" in body, "markdown contains fact")
    print("FORMAT SMOKE PASSED")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Fail as e:
        print(f"FORMAT SMOKE FAILED: {e}", file=sys.stderr)
        sys.exit(1)
