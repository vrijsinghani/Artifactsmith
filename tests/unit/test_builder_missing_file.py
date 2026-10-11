"""Missing ===FILE=== marker: recover one HTML/Markdown document; keep the marked path identical."""

from __future__ import annotations

import logging

import pytest

from artifactsmith import builder as b

_HTML = "<!DOCTYPE html>\n<html><body>ok</body></html>"
_INLINE_HTML = "<!DOCTYPE html><html><body>ok</body></html>"
_MARKED = f"===ASSUMPTIONS===\n- used the source\n===SUMMARY===\nA page.\n===FILE: index.html===\n{_HTML}\n===END===\n"


def test_parse_output_recovers_plain_html_without_file_marker(caplog: pytest.LogCaptureFixture) -> None:
    text = f"===ASSUMPTIONS===\n- none\n===SUMMARY===\nA page.\n{_HTML}\n===END===\n"
    with caplog.at_level(logging.WARNING, logger="artifactsmith.builder"):
        assumptions, summary, body = b.parse_output(text, fmt="html")
    assert assumptions == []
    assert summary == "A page."
    assert body == _HTML
    assert any("no FILE section" in r.message for r in caplog.records)


def test_parse_output_recovers_html_inside_html_fence() -> None:
    text = f"===ASSUMPTIONS===\n- none\n===SUMMARY===\nA page.\n```html\n{_HTML}\n```\n===END===\n"
    assumptions, summary, body = b.parse_output(text, fmt="html")
    assert assumptions == []
    assert summary == "A page."
    assert body == _HTML


def test_parse_output_whole_output_html_without_markers() -> None:
    assumptions, summary, body = b.parse_output(_HTML, fmt="html")
    assert (assumptions, summary, body) == ([], "", _HTML)


def test_parse_output_leading_prose_fails_unless_document_is_separable() -> None:
    inline = f"Here is the page: {_INLINE_HTML}"
    with pytest.raises(b.BuildError, match="section markers"):
        b.parse_output(inline, fmt="html")
    after_summary = f"===SUMMARY===\nEnjoy this: {_INLINE_HTML}\n===END===\n"
    with pytest.raises(b.BuildError, match="no FILE"):
        b.parse_output(after_summary, fmt="html")

    separable = f"Here is the page.\n{_HTML}"
    assumptions, summary, body = b.parse_output(separable, fmt="html")
    assert assumptions == []
    assert summary == ""
    assert body == _HTML

    after_summary_sep = f"===SUMMARY===\nA page.\n{_HTML}\n===END===\n"
    assumptions, summary, body = b.parse_output(after_summary_sep, fmt="html")
    assert assumptions == []
    assert summary == "A page."
    assert body == _HTML


def test_parse_output_normal_file_section_unchanged() -> None:
    expected = b.parse_output(_MARKED)
    assert b.parse_output(_MARKED, fmt="html") == expected
    assert expected == (["used the source"], "A page.", _HTML)


def test_parse_output_markdown_recovers_only_when_document_is_clear() -> None:
    md = "# Pilot\n\nThe code is HARBOR-17."
    text = f"===ASSUMPTIONS===\n- none\n===SUMMARY===\nA note.\n{md}\n===END===\n"
    assumptions, summary, body = b.parse_output(text, fmt="markdown")
    assert assumptions == []
    assert summary == "A note."
    assert body == md

    fenced = f"===SUMMARY===\nA note.\n```markdown\n{md}\n```\n===END===\n"
    _, _, fenced_body = b.parse_output(fenced, fmt="markdown")
    assert fenced_body == md

    prose = "===SUMMARY===\nA note.\nJust some sentences, no heading.\n===END===\n"
    with pytest.raises(b.BuildError, match="no FILE"):
        b.parse_output(prose, fmt="markdown")
    with pytest.raises(b.BuildError, match="section markers"):
        b.parse_output(_HTML, fmt="markdown")


def test_parse_output_pdf_and_two_html_documents_stay_strict() -> None:
    text = f"===SUMMARY===\nA page.\n{_HTML}\n===END===\n"
    with pytest.raises(b.BuildError, match="no FILE"):
        b.parse_output(text, fmt="pdf")
    stacked = f"===SUMMARY===\nA page.\n{_HTML}\n{_HTML}\n===END===\n"
    with pytest.raises(b.BuildError, match="no FILE"):
        b.parse_output(stacked, fmt="html")


@pytest.mark.asyncio
async def test_run_build_recovered_html_still_rejects_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    secret_html = "<!DOCTYPE html><html><body>sk-" + ("b" * 24) + "</body></html>"

    async def fake_call(model: str, system: str, user: str, timeout: int = 300) -> str:
        return f"===ASSUMPTIONS===\n- none\n===SUMMARY===\npage\n{secret_html}\n===END===\n"

    monkeypatch.setattr(b.llm, "call", fake_call)
    with pytest.raises(b.BuildError, match="secret"):
        await b.run_build(
            kind="web_static",
            slug="s",
            display_name="x",
            verbatim="x",
            model="test-model",
            base_source=None,
            base_version=None,
            history=[],
            progress=lambda _m: None,
            format="html",
        )
