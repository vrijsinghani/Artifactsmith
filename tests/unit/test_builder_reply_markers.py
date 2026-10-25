"""Marker lines inside a page are content. A cut-off reply fails instead of publishing."""

from __future__ import annotations

import pytest

from artifactsmith import builder as b

_PAGE = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Reply format</title></head>
<body>
<h1>Reply format</h1>
<p>The builder reply uses these markers.</p>
<pre><code>===ASSUMPTIONS===
===SUMMARY===
===FILE: x===
===END===
</code></pre>
<p>The same markers as plain lines:</p>
===ASSUMPTIONS===
===SUMMARY===
===FILE: x===
===END===
<p>The rest of the page stays.</p>
</body>
</html>"""

_NORMAL = (
    "===ASSUMPTIONS===\n- used the source\n===SUMMARY===\nA page.\n"
    "===FILE: index.html===\n<html><body>ok</body></html>\n===END===\n"
)


def _reply(body: str, summary: str = "Documents the reply format.") -> str:
    return f"===ASSUMPTIONS===\n- none\n===SUMMARY===\n{summary}\n===FILE: index.html===\n{body}\n===END===\n"


def test_normal_reply_is_unchanged() -> None:
    assert b.parse_output(_NORMAL) == (["used the source"], "A page.", "<html><body>ok</body></html>")
    assert b.parse_output(_NORMAL, fmt="html") == (["used the source"], "A page.", "<html><body>ok</body></html>")


def test_markers_inside_the_page_parse_in_full() -> None:
    assumptions, summary, body = b.parse_output(_reply(_PAGE), fmt="html")
    assert assumptions == []
    assert summary == "Documents the reply format."
    assert body == _PAGE
    assert body.count("===ASSUMPTIONS===") == 2
    assert body.count("===SUMMARY===") == 2
    assert body.count("===FILE: x===") == 2
    assert body.count("===END===") == 2


def test_truncated_reply_is_an_error() -> None:
    text = (
        "===ASSUMPTIONS===\n- none\n===SUMMARY===\nA page.\n"
        "===FILE: index.html===\n<html><body><p>stopped mid-sentence\n"
    )
    with pytest.raises(b.BuildError, match="truncated: missing ===END==="):
        b.parse_output(text, fmt="html")


@pytest.mark.asyncio
async def test_markers_inside_the_page_publish_in_full(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_call(model: str, system: str, user: str, timeout: float = 300) -> str:
        return _reply(_PAGE)

    monkeypatch.setattr(b.llm, "call", fake_call)
    result = await b.run_build(
        kind="web_static",
        slug="s",
        display_name="Reply format",
        verbatim="document the reply format",
        model="test-model",
        base_source=None,
        base_version=None,
        history=[],
        progress=lambda _m: None,
        format="html",
    )
    page = result.files["index.html"].decode()
    assert "The rest of the page stays." in page
    assert page.count("===ASSUMPTIONS===") == 2
    assert page.count("===SUMMARY===") == 2
    assert page.count("===FILE: x===") == 2
    assert page.count("===END===") == 2
    assert "<ASL>" not in page


@pytest.mark.asyncio
async def test_truncated_reply_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def fake_call(model: str, system: str, user: str, timeout: float = 300) -> str:
        nonlocal calls
        calls += 1
        return (
            "===ASSUMPTIONS===\n- none\n===SUMMARY===\nA page.\n"
            "===FILE: index.html===\n<html><body><p>stopped mid-sentence\n"
        )

    monkeypatch.setattr(b.llm, "call", fake_call)
    with pytest.raises(b.BuildError, match="truncated: missing ===END==="):
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
    assert calls == 2


@pytest.mark.asyncio
async def test_page_content_after_end_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    small = "<!DOCTYPE html>\n<html><body><p>Hi</p></body></html>"
    tail = "\n".join("<p>This sentence was cut off the page and should not vanish.</p>" for _ in range(40))
    reply = _reply(small) + tail

    async def fake_call(model: str, system: str, user: str, timeout: float = 300) -> str:
        return reply

    monkeypatch.setattr(b.llm, "call", fake_call)
    with pytest.raises(b.BuildError, match="page content after ===END==="):
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


@pytest.mark.asyncio
async def test_file_with_no_visible_text_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    empty = "<!DOCTYPE html>\n<html><body>\n<ASL></ASL>\n<!-- " + ("pad " * 80) + " -->\n</body></html>"
    reply = _reply(empty)

    async def fake_call(model: str, system: str, user: str, timeout: float = 300) -> str:
        return reply

    monkeypatch.setattr(b.llm, "call", fake_call)
    with pytest.raises(b.BuildError, match="no visible text"):
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


_SPEC_BLOCK = """Reply exactly:
===ASSUMPTIONS===
- one per line, or - none
===SUMMARY===
one or two sentences
===FILE: page.asl===
<ASL>
===END==="""


def test_spec_page_documenting_the_reply_format_parses_whole() -> None:
    # Real case: the ASL spec page (art_hjrzx4tl2gvq3h v1) published as only "<ASL>".
    page = (
        "<!DOCTYPE html>\n<html><head><title>ASL</title></head><body><h1>ASL</h1>\n<pre><code>"
        + _SPEC_BLOCK
        + "\n</code></pre>\n<p>Size: 429 tokens.</p>\n</body></html>"
    )
    assumptions, summary, body = b.parse_output(_reply(page), fmt="html")
    assert body == page
    assert summary == "Documents the reply format."
    b._guard_primary_file(_reply(page), body, "html")


def test_complete_html_without_end_is_kept() -> None:
    page = "<!DOCTYPE html>\n<html><body><p>Done.</p></body></html>"
    text = "===ASSUMPTIONS===\n- none\n===SUMMARY===\nA page.\n===FILE: index.html===\n" + page + "\n"
    assert b.parse_output(text, fmt="html") == ([], "A page.", page)


def test_complete_html_with_inner_end_and_no_final_end_is_kept() -> None:
    page = "<!DOCTYPE html>\n<html><body><pre>\n===END===\n</pre><p>More.</p></body></html>"
    text = "===ASSUMPTIONS===\n- none\n===SUMMARY===\nA page.\n===FILE: index.html===\n" + page
    assert b.parse_output(text, fmt="html")[2] == page


@pytest.mark.parametrize("fmt", ["html", "markdown"])
def test_summary_after_the_file_is_not_page_content(fmt: str) -> None:
    page = "<!DOCTYPE html>\n<html><body><p>Hi there.</p></body></html>" if fmt == "html" else "# Hi\n\nThere."
    for tail in ("===SUMMARY===\nA page.\n===END===\n", "===SUMMARY===\nA page.\n"):
        text = "===ASSUMPTIONS===\n- none\n===FILE: f===\n" + page + "\n===END===\n" + tail
        assert b.parse_output(text, fmt=fmt) == ([], "A page.", page)


def test_markdown_code_fence_with_marker_lines_stays_in_the_file() -> None:
    md = "# Reply format\n\n```\n===FILE: content.md===\n# Doc\n===END===\n===SUMMARY===\nx\n```\n\nThe rest stays."
    text = "===ASSUMPTIONS===\n- none\n===SUMMARY===\nA doc.\n===FILE: content.md===\n" + md + "\n===END===\n"
    assert b.parse_output(text, fmt="markdown") == ([], "A doc.", md)


@pytest.mark.parametrize("fmt", ["xlsx", "markdown", "docx"])
def test_small_file_with_long_metadata_is_kept(fmt: str) -> None:
    table = "| Item | Amount |\n|---|---|\n| Setup | 1000 |"
    longa = "\n".join(
        f"- Assumption {i}: the request did not say which quarter, so I used the source one." for i in range(8)
    )
    text = f"===ASSUMPTIONS===\n{longa}\n===SUMMARY===\n{'A one-row workbook. ' * 10}\n===FILE: content.md===\n{table}\n===END===\n"
    _, _, body = b.parse_output(text, fmt=fmt)
    assert body == table
    b._guard_primary_file(text, body, fmt)


def test_chart_only_page_counts_its_label_as_visible() -> None:
    page = '<!DOCTYPE html><html><body><svg role="img" aria-label="Sales by month" viewBox="0 0 9 9"><rect width="5" height="5"/></svg></body></html>'
    b._guard_primary_file(_reply(page), b.parse_output(_reply(page), fmt="html")[2], "html")


def test_short_trailing_note_after_end_is_ignored() -> None:
    page = "<!DOCTYPE html>\n<html><body><p>Hi.</p></body></html>"
    assert b.parse_output(_reply(page) + "Let me know if you want changes.\n", fmt="html")[2] == page


_TCP = (
    "# TCP three-way handshake\n\nTCP uses three messages to establish a reliable connection. The client sends SYN.\n"
)
_TCP_SENTENCE = "TCP uses three messages to establish a reliable connection."


def _markdown_reply(summary: str, body: str = _TCP) -> str:
    return f"===ASSUMPTIONS===\n- none\n===SUMMARY===\n{summary}\n===FILE: page.md===\n{body}===END===\n"


@pytest.mark.parametrize(
    "summary",
    [
        "one or two sentences",
        "One or two sentences.",
        "one  or\ttwo sentences",
        '"one or two sentences"',
        "<one sentence>",
        "<one sentence>.",
        "&lt;one sentence&gt;",
        "one or two plain sentences describing what you built",
        "one or two plain sentences describing only what changed",
        "one or two short sentences naming exactly which data is missing",
        "<Markdown>",
        "- one per line, or - none",
    ],
)
def test_placeholder_summary_uses_the_first_sentence(summary: str) -> None:
    assumptions, got, body = b.parse_output(_markdown_reply(summary), fmt="markdown")
    assert assumptions == []
    assert got == _TCP_SENTENCE
    assert body.startswith("# TCP")
    assert "one or two sentences" not in got


def test_a_real_summary_is_kept_even_when_it_mentions_the_phrase() -> None:
    summary = "The notes are one or two sentences."
    assert b.parse_output(_markdown_reply(summary), fmt="markdown")[1] == summary
    html = (
        "<!DOCTYPE html><html><body><h1>TCP</h1>"
        "<p>TCP uses three messages to establish a reliable connection. Next.</p></body></html>"
    )
    assert b.parse_output(_reply(html, summary="one or two sentences"), fmt="html")[1] == _TCP_SENTENCE


def test_placeholder_summary_on_a_title_only_page_uses_the_title() -> None:
    _assumptions, got, _body = b.parse_output(_markdown_reply("one or two sentences", "# Title only\n"), fmt="markdown")
    assert got == "Title only"


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("<script>alert(1)</script> is bad.", "is bad."),
        ("<img src=x onerror=alert(1)> is bad.", "is bad."),
        (
            "&lt;script&gt;alert(1)&lt;/script&gt; is bad.",
            "&lt;script&gt;alert(1)&lt;/script&gt; is bad.",
        ),
        ("**[click](javascript:alert(1))** now.", "click now."),
        ("See [docs](https://example.com/a[b]) now.", "See docs now."),
        ("See [docs](<https://example.com/a(b)>) now.", "See docs now."),
    ],
)
def test_fallback_summary_is_plain_text(sentence: str, expected: str) -> None:
    body = f"# Title\n\n{sentence}\n"
    got = b.parse_output(_markdown_reply("one or two sentences", body), fmt="markdown")[1]
    assert got == expected
    assert "<script" not in got
    assert "<img" not in got
    assert "onerror" not in got
    assert "javascript:" not in got


def test_html_fallback_summary_does_not_unescape_tags() -> None:
    page = (
        "<!DOCTYPE html><html><body><h1>Ignored</h1><p>&lt;script&gt;alert(1)&lt;/script&gt; is bad.</p></body></html>"
    )
    got = b.parse_output(_reply(page, summary="one or two sentences"), fmt="html")[1]
    assert got == "&lt;script&gt;alert(1)&lt;/script&gt; is bad."
    assert "<script" not in got
    script = (
        "<!DOCTYPE html><html><body><p><script>alert(1)</script> is bad. <img src=x onerror=alert(1)></p></body></html>"
    )
    got = b.parse_output(_reply(script, summary="<one sentence>"), fmt="html")[1]
    assert got == "is bad."
    assert "<script" not in got and "onerror" not in got and "<img" not in got


def test_table_only_page_summary_uses_the_title_or_a_sentence() -> None:
    titled = "# Rebate by month\n\n| Month | Billed |\n|---|---|\n| July | $1 |\n"
    assert b.parse_output(_markdown_reply("one or two sentences", titled), fmt="markdown")[1] == "Rebate by month"
    bare = "| Month | Billed |\n|---|---|\n| July | $1 |\n"
    assert b.parse_output(_markdown_reply("<one sentence>", bare), fmt="markdown")[1] == "This page is a table."
    prose = "# Rebate by month\n\nJuly was the peak.\n\n| Month | Billed |\n|---|---|\n| July | $1 |\n"
    assert b.parse_output(_markdown_reply("one or two sentences", prose), fmt="markdown")[1] == "July was the peak."
    html = (
        "<!DOCTYPE html><html><head><title>Months</title></head><body>"
        "<table><tr><td>July</td><td>$1</td></tr></table></body></html>"
    )
    assert b.parse_output(_reply(html, summary="one or two sentences"), fmt="html")[1] == "Months"
    bare_html = "<!DOCTYPE html><html><body><table><tr><td>July</td><td>$1</td></tr></table></body></html>"
    assert b.parse_output(_reply(bare_html, summary="<one sentence>"), fmt="html")[1] == "This page is a table."
