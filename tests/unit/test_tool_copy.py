"""Agent-facing copy opens with the styled-page choice."""

from __future__ import annotations

from pathlib import Path

from artifactsmith import server

_ROOT = Path(__file__).resolve().parents[2]


def test_tool_copy_opens_with_the_styled_page_choice():
    texts = {
        "instructions": server.INSTRUCTIONS,
        "create": server.create.__doc__ or "",
        "publish": server.publish.__doc__ or "",
        "readme": (_ROOT / "README.md").read_text(encoding="utf-8"),
        "formats": (_ROOT / "docs" / "formats.md").read_text(encoding="utf-8"),
    }
    for name, text in texts.items():
        assert "call publish instead" not in text, name
        assert "format=markdown" in text, name
        assert "exactly as given" in text, name
    assert "warning" in texts["publish"]
    assert "styled automatically" in texts["publish"]
    assert "source_content" in texts["create"]
    assert "does not restyle the page you submitted" not in texts["publish"]
    assert "does not restyle the page you submitted" not in texts["readme"]
