#!/usr/bin/env python3
"""Example MCP client for ArtifactSmith. Set ARTIFACTS_MCP_TOKEN to a token from `artifactsmith token add`.

  ARTIFACTS_MCP_TOKEN=asmb_... python examples/python_client.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

MCP_URL = os.environ.get("ARTIFACTS_MCP_URL", "http://127.0.0.1:8780/mcp")


def _result(res) -> dict:
    if res.structuredContent is not None:
        return res.structuredContent
    if res.content and getattr(res.content[0], "text", None):
        return json.loads(res.content[0].text)
    return {"error": "no content"}


async def main() -> int:
    token = os.environ.get("ARTIFACTS_MCP_TOKEN", "")
    if not token:
        print("set ARTIFACTS_MCP_TOKEN", file=sys.stderr)
        return 2
    headers = {"Authorization": f"Bearer {token}"}
    async with streamablehttp_client(MCP_URL, headers=headers) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("tools:", [t.name for t in tools.tools])

            created = _result(await s.call_tool("create", {
                "slug": "pilot-store", "display_name": "Pilot Store", "kind": "web_static",
                "verbatim_request": "Show the pilot store code.",
                "source_content": "The pilot store code is HARBOR-17.",
            }))
            print("create:", json.dumps(created, indent=2))

            st = _result(await s.call_tool("status", {"job_id": created["job_id"], "wait": 90}))
            print("status:", json.dumps(st, indent=2))
            if st.get("preview_url"):
                html = httpx.get(st["preview_url"], timeout=10).text
                print("preview contains fact:", "HARBOR-17" in html)

            shared = _result(await s.call_tool("share", {"artifact_id": created["artifact_id"]}))
            print("share:", json.dumps(shared, indent=2))
            await s.call_tool("unshare", {"artifact_id": created["artifact_id"]})
            print("unshared")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
