"""Every registered MCP tool must publish explicit annotation hints.

readOnly, destructive, openWorld. A new tool missing from this map fails the test.
Side effects that fix these booleans are asserted in test_tool_side_effects.py.
"""

from __future__ import annotations

import pytest

from artifactsmith import server

# readOnly, destructive, openWorld.
_EXPECTED: dict[str, tuple[bool, bool, bool]] = {
    # Queues a new artifact and job. An existing done slug is refused, not overwritten.
    "create": (False, False, False),
    # Queues a new version and leaves earlier versions in place.
    "edit": (False, False, False),
    # Stores a finished page and queues the safety job. It does not create a public link.
    "publish": (False, False, False),
    # A done card inserts a private /p/ link. wait only polls; the insert is additive.
    "status": (False, False, False),
    # Reads the caller's catalog only.
    "list_artifacts": (True, False, False),
    # A done card inserts a private /p/ link. include_content only reads stored text.
    "inspect": (False, False, False),
    # 15-minute HMAC download URL. No link row is stored. The audit line does not grant access.
    "export": (True, False, False),
    # Inserts a share row whose URL is reachable by anyone on the public internet.
    "share": (False, False, True),
    # Revokes public share links in the caller's workspace.
    "unshare": (False, True, False),
    # Expires private preview capabilities in the caller's workspace.
    "revoke_previews": (False, True, False),
    # After confirmation, purges the artifact, its objects, and its links.
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
