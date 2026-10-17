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


def test_readme_states_requirements_client_setup_and_stdio_limits():
    text = Path(__file__).resolve().parents[2].joinpath("README.md").read_text(encoding="utf-8")
    before, quickstart = text.split("## Quickstart", 1)
    requirements = [line for line in before.splitlines() if line.strip()][-1]
    assert requirements == (
        "Requirements: Docker Engine with the Compose plugin. "
        "A model key (OpenAI-compatible API or local endpoint) is needed to build pages; "
        "publishing ready-made Markdown or HTML works without one."
    )
    body, _rest = quickstart.split("## Connect a client", 1)
    assert "publish" in body
    settings = body.split("The server reads `AM_LLM_BASE`", 1)[1]
    assert "Run `docker compose up -d` again so the change takes effect." in settings
    assert '"share"' in body
    assert 'print(shared["url"])' in body
    connect, later = text.split("## Connect a client", 1)[1].split("\n## ", 1)
    assert "claude_desktop_config.json" in connect
    assert ".mcp.json" in connect
    assert "~/.cursor/mcp.json" in connect
    assert "claude mcp add --transport http artifactsmith http://127.0.0.1:8780/mcp" in connect
    assert "Authorization: Bearer YOUR_TOKEN" in connect
    assert "Claude.ai custom connectors sign in with OAuth." in connect
    assert "ChatGPT personal plans cannot add a custom MCP server." in connect
    assert "Free and Plus can use a directory listing." not in connect
    assert "Node.js" in connect
    assert "mcp-remote" in connect
    assert "Pass this as source_content: The pilot store code is HARBOR-17." in connect
    assert "cannot use the object store or the model" in connect
    assert "cannot build or publish a page" in later
    assert "can instead run `artifactsmith stdio`" not in text
    joined = connect + body
    assert "free builds" not in joined.lower()
    assert "trial" not in connect.lower()


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
