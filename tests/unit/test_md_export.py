"""Markdown export keeps sanitizer-allowed links and drops blocked ones."""

from __future__ import annotations

from artifactsmith.renderers import get_renderer
from artifactsmith.renderers.md_sanitize import collect_link_destinations
from artifactsmith.renderers.safety import sanitize_text


def _export(body: str) -> str:
    cleaned = sanitize_text(body)
    rendered = get_renderer("markdown").render(title="Page", body=cleaned)
    return rendered.files["document.md"].decode()


def test_markdown_export_keeps_normal_link() -> None:
    text = _export(
        "See [the paper](https://example.com/paper) and "
        '<a href="https://example.com/notes">the notes</a>.\n\n'
        '<p>Read <a href="https://example.com/guide">the guide</a>.</p>\n'
    )
    assert "[the paper](https://example.com/paper)" in text
    assert "[the notes](https://example.com/notes)" in text
    assert "[the guide](https://example.com/guide)" in text
    assert set(collect_link_destinations(text)) == {
        "https://example.com/paper",
        "https://example.com/notes",
        "https://example.com/guide",
    }


def test_markdown_export_keeps_odd_characters_in_link_text() -> None:
    text = _export('See [Q&A <draft> "v2"](https://example.com/qa).')
    assert collect_link_destinations(text) == ["https://example.com/qa"]
    visible = text.replace("\\", "")
    assert "Q&A" in visible
    assert "<draft>" in visible
    assert '"v2"' in visible


def test_markdown_export_blocked_link_stays_blocked() -> None:
    text = _export(
        'See [admin](http://127.0.0.1/secret) and <a href="javascript:alert(1)">click</a> '
        'and <a href="http://10.1.1.1/x">local</a>.'
    )
    assert collect_link_destinations(text) == []
    assert "](http://127.0.0.1" not in text
    assert "](http://10.1.1.1" not in text
    assert "javascript:" not in text.lower()
    assert "admin" in text
    assert "click" in text
    assert "local" in text
