"""README MCP tool table must match the tools the server actually registers."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from artifactsmith import server


@pytest.mark.asyncio
async def test_readme_mcp_tools_match_server():
    registered = sorted(t.name for t in await server.mcp.list_tools())
    text = Path(__file__).resolve().parents[2].joinpath("README.md").read_text(encoding="utf-8")
    start = text.index("## MCP tools")
    end = text.index("\n## ", start + 1)
    section = text[start:end]
    documented = sorted(
        n for n in re.findall(r"^\|\s*`([a-z_]+)`\s*\|", section, flags=re.M) if n != "Tool"
    )
    assert documented == registered, (documented, registered)
    for phantom in ("list_prompts", "get_prompt", "list_resources", "read_resource"):
        assert phantom not in documented
