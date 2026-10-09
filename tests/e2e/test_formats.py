#!/usr/bin/env python3
"""Format smoke: create markdown/pdf/docx/xlsx against the test compose stack."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import httpx

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from tests.e2e.stack import API, MCP_URL, Fail, call, check, provision, wait_health  # noqa: E402

FORMATS = ("markdown", "pdf", "docx", "xlsx")
MAGIC = {
    "markdown": b"# ",
    "pdf": b"%PDF",
    "docx": b"PK",
    "xlsx": b"PK",
}


async def main() -> int:
    await wait_health(API)
    run_id = f"{int(time.time())}{os.getpid() % 1000}"
    token = provision(f"fmt-{run_id}", "formats", "create,read,edit,export,share,delete")

    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

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
