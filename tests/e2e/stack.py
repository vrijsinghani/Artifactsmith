"""Helpers for compose end-to-end scripts. Not collected by pytest."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

REPO = Path(__file__).resolve().parents[2]
API = "http://127.0.0.1:8780"
PREVIEW = "http://127.0.0.1:8781"
MCP_URL = f"{API}/mcp"
COMPOSE = ["-f", "compose.yaml", "-f", "compose.test.yaml"]


class Fail(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise Fail(msg)
    print(f"  ok: {msg}")


def compose(*args, capture=True) -> str:
    r = subprocess.run(["docker", "compose", *COMPOSE, *args], cwd=REPO, capture_output=True, text=True)
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
    import asyncio

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
