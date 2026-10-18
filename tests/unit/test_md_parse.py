import pytest

from artifactsmith.renderers.md_parse import Block, blocks_to_simple_html, iter_styled_spans, parse_blocks


@pytest.mark.parametrize("heading", ["Title", "TITLE", "  title  "])
def test_pdf_html_skips_only_matching_leading_h1(heading):
    blocks = [Block(kind="heading", level=1, text=heading), Block(kind="paragraph", text="Body.")]
    html = blocks_to_simple_html("  Title  ", blocks)
    assert html.count("<h1>") == 1
    assert "<h1>  Title  </h1>" in html
    assert "<p>Body.</p>" in html
    assert len(blocks) == 2  # Rendering must not remove the caller's parsed block.


@pytest.mark.parametrize(
    ("body", "expected_headings"),
    [
        ("# Different\n\nBody.", ["<h1>Title</h1>", "<h1>Different</h1>"]),
        ("Intro.\n\n# Title", ["<h1>Title</h1>", "<h1>Title</h1>"]),
        ("## Title\n\nBody.", ["<h1>Title</h1>", "<h2>Title</h2>"]),
        ("Body without a heading.", ["<h1>Title</h1>"]),
        ("", ["<h1>Title</h1>"]),
        ("# Title\n\n# Title", ["<h1>Title</h1>", "<h1>Title</h1>"]),
    ],
)
def test_pdf_html_preserves_other_headings(body, expected_headings):
    html = blocks_to_simple_html("Title", parse_blocks(body))
    assert html.count("<h1>") == sum(heading.startswith("<h1>") for heading in expected_headings)
    for heading in expected_headings:
        assert heading in html


def test_parse_headings_lists_tables_code():
    text = """# Title

A paragraph.

- one
- two

```
code
```

| a | b |
| --- | --- |
| 1 | 2 |
"""
    kinds = [b.kind for b in parse_blocks(text)]
    assert kinds == ["heading", "paragraph", "list", "code", "table"]
    html = blocks_to_simple_html("Doc", parse_blocks(text))
    assert "<table>" in html
    assert "&lt;" not in html
    assert "<script" not in html


def test_inline_html_renders_emphasis_not_markers():
    html = blocks_to_simple_html("Doc", parse_blocks("Use **bold**, *italic*, and `code`."))
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert "<code>code</code>" in html
    assert "**bold**" not in html
    assert "*italic*" not in html


def test_pre_css_wraps_long_code_lines():
    html = blocks_to_simple_html("Doc", parse_blocks("```\n" + "x" * 200 + "\n```"))
    assert "white-space:pre-wrap" in html
    assert "overflow-wrap:anywhere" in html
    assert "word-break:break-all" in html
    assert "overflow:auto" not in html


def test_styled_spans_drop_emphasis_markers():
    spans = iter_styled_spans("**bold** and *italic* and `cmd`")
    assert [("bold", True, False, False), ("italic", False, True, False), ("cmd", False, False, True)] == [
        (s.text, s.bold, s.italic, s.code) for s in spans if s.text.strip() and s.text.strip() != "and"
    ]
