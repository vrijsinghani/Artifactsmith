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
    documented = sorted(n for n in re.findall(r"^\|\s*`([a-z_]+)`\s*\|", section, flags=re.M) if n != "Tool")
    assert documented == registered, (documented, registered)
    for phantom in ("list_prompts", "get_prompt", "list_resources", "read_resource"):
        assert phantom not in documented
    for name in ("export", "share", "unshare", "delete"):
        assert f"### `{name}`" in section
    assert "200 characters" in section
    assert "2–63" in section
    assert "pilot-store-brief" in section


@pytest.mark.asyncio
async def test_create_schema_describes_slug_and_display_name():
    tools = {t.name: t for t in await server.mcp.list_tools()}
    props = tools["create"].inputSchema["properties"]
    for key in ("slug", "display_name"):
        desc = props[key].get("description") or ""
        assert "Optional" in desc or "at least one" in desc
        assert "Example" in desc
    assert "200" in props["display_name"]["description"]
    assert "2-63" in props["slug"]["description"]
    for name in ("export", "share", "unshare", "delete"):
        tool = tools[name]
        assert "Permission:" in (tool.description or "")
        assert "Returns:" in (tool.description or "")
        assert tool.inputSchema["properties"]["artifact_id"].get("description")
