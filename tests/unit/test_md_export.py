"""Markdown export keeps sanitizer-allowed links and drops blocked ones."""

from __future__ import annotations

import time

from artifactsmith.renderers import get_renderer
from artifactsmith.renderers.md_sanitize import _destinations_policy_clean, collect_link_destinations
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


def test_blocked_href_scheme_less_host_stays_unlinkified() -> None:
    text = _export('<a href="javascript:x">www.10.0.0.5.nip.io</a>')
    assert _destinations_policy_clean(text)
    assert collect_link_destinations(text) == []


def test_anchor_split_by_blank_line_keeps_label() -> None:
    text = _export('<p>Read <a href="https://example.com/ok">x\n\ny</a>.</p>')
    assert "x" in text


def test_unclosed_anchors_stay_linear() -> None:
    body = '<a href="https://example.com/ok">x ' * 20000
    started = time.perf_counter()
    get_renderer("markdown").render(title="Page", body=body)
    assert time.perf_counter() - started < 2


def test_label_cannot_smuggle_a_second_destination() -> None:
    text = _export('<a href="https://example.com/ok">x](javascript:alert(1))</a>')
    assert collect_link_destinations(text) == ["https://example.com/ok"]
