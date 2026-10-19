"""Every registered MCP tool must publish explicit OpenAI Apps annotation hints."""

from __future__ import annotations

import pytest

from artifactsmith import server

# readOnly, destructive, openWorld. A new tool missing from this map fails the test.
_EXPECTED: dict[str, tuple[bool, bool, bool]] = {
    "create": (False, False, False),
    "edit": (False, False, False),
    "publish": (False, False, False),
    "status": (False, False, False),
    "list_artifacts": (True, False, False),
    "inspect": (False, False, False),
    "export": (True, False, False),
    "share": (False, False, True),
    "unshare": (False, True, False),
    "revoke_previews": (False, True, False),
    "delete": (False, True, False),
}

_HINTS = ("readOnlyHint", "destructiveHint", "openWorldHint")


@pytest.mark.asyncio
async def test_every_tool_has_explicit_annotation_booleans() -> None:
    tools = await server.mcp.list_tools()
    assert {tool.name for tool in tools} == set(_EXPECTED)
    for tool in tools:
        annotations = tool.annotations
        assert annotations is not None, tool.name
        dumped = annotations.model_dump(mode="json", by_alias=True, exclude_none=True)
        read_only, destructive, open_world = _EXPECTED[tool.name]
        assert dumped["readOnlyHint"] is read_only
        assert dumped["destructiveHint"] is destructive
        assert dumped["openWorldHint"] is open_world
        for name in _HINTS:
            assert isinstance(dumped[name], bool), (tool.name, name, dumped[name])
        assert isinstance(dumped["title"], str) and dumped["title"]
        assert tool.title == dumped["title"]
        assert tool.description
        assert tool.inputSchema["type"] == "object"
