"""Markdown layout: allow-list, blocks, charts, samples, contrast."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from artifactsmith.layout.chart import chart_svg
from artifactsmith.layout.css import BADGE_PAIRS, CONTRAST_PAIRS, stylesheet
from artifactsmith.layout.model import LayoutError
from artifactsmith.layout.parse import parse_markdown
from artifactsmith.layout.prompt import SYNTAX_RULES, fast_system_prompt
from artifactsmith.layout.render import render_layout
from artifactsmith.renderers.html_sanitize import sanitize_html_document
from artifactsmith.renderers.svg_sanitize import sanitize_svg_fragment
from artifactsmith.styles import STYLES

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "examples" / "layout"


def _tokens(text: str) -> int:
    return len(re.findall(r"\w+|[^\w\s]", text))


def _contrast(fore: str, back: str) -> float:
    def channel(hex_byte: str) -> float:
        value = int(hex_byte, 16) / 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    def lum(color: str) -> float:
        raw = color.lstrip("#")
        red, green, blue = channel(raw[0:2]), channel(raw[2:4]), channel(raw[4:6])
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue

    hi, lo = max(lum(fore), lum(back)), min(lum(fore), lum(back))
    return (hi + 0.05) / (lo + 0.05)


def test_syntax_rules_stay_short():
    assert _tokens(SYNTAX_RULES) <= 400
    house = fast_system_prompt("house")
    assert "===FILE: page.md===" in house
    assert "Visual design:" not in house
    assert "No emoji." in house
    bold = fast_system_prompt("bold")
    assert "No emoji." not in bold


def test_dangerous_attributes_and_html_are_dropped():
    source = """# Title

<script>alert(1)</script>

[go](javascript:alert)

[local](http://127.0.0.1/secret)

{onclick=alert(1)}

Hello [bad]{onclick=alert(1)}

[ok]{.green}
"""
    html, _notes = render_layout(source, style="house", title="Title")
    doc = sanitize_html_document(html)
    low = doc.lower()
    assert "<script" not in low
    assert 'href="javascript:' not in low
    assert "127.0.0.1" not in low
    assert "onclick" not in low
    assert "Hello" in doc
    assert "local" in doc
    assert "bad" in doc
    assert "asl-badge" in doc
    blocks, notes = parse_markdown("{onclick=alert(1)}\n")
    assert blocks == []
    assert notes == []


def test_unknown_block_and_second_emph_are_notes():
    source = """# Title

::: mystery
plain words
:::

::: note {.emph}
First
:::

::: warn {.emph}
Second
:::
"""
    _html, notes = render_layout(source, style="house", title="Title")
    assert "unknown block mystery" in notes
    assert "ignored extra emph" in notes
    assert "First" in _html and "Second" in _html


def test_each_block_renders_in_each_style():
    source = """# Board {variant=band}

A short dek

::: note {variant=box}
A note
:::

:::: grid 2
::: stat {tone=green .emph}
Open | 3 | left
:::
::: stat {tone=red}
Closed | 1
:::
::::

::: timeline
- March | Shipped
:::

::: steps
1. First
2. Second
:::

::: compare
| A | B |
|---|---|
| 1 | 2 |
:::

::: quote
Said it.
:::

Term
: value

```chart pie unit=%
Yes: 3
No: 1
```

```chart line
Jan: 1
Feb: 2
```

```chart donut
One: 1
Two: 1
```

```chart hbar
North: 4
South: 2
```

```py
print(1)
```

::: html
<p>kept</p>
:::
"""
    for style in STYLES:
        html, notes = render_layout(source, style=style, title="Board")
        assert "unknown block" not in notes
        for needle in ("asl-callout", "asl-stat", "asl-timeline", "asl-steps", "asl-quote", "asl-kv", "asl-chart"):
            assert needle in html
        assert html.count("<svg") >= 4
        doc = sanitize_html_document(html)
        assert "<svg" in doc
        assert len(stylesheet(style).encode()) < 8192


def test_chart_svg_passes_sanitizer():
    svg = chart_svg("bar", ["Jul", "Aug"], [38185, 28935], "$", "Bar chart, Monthly: Jul $38,185, Aug $28,935")
    cleaned = sanitize_svg_fragment(f"<div>{svg}</div>")
    assert 'role="img"' in cleaned
    assert "aria-label=" in cleaned
    assert "<script" not in cleaned.lower()
    page = sanitize_html_document(
        "<!DOCTYPE html><html><body><figure class='asl-chart'>" + svg + "</figure></body></html>"
    )
    assert "<svg" in page
    assert "<script" not in page.lower()


def test_non_numeric_chart_becomes_a_table():
    blocks, notes = parse_markdown("```chart bar\nLater: soon\n```\n")
    assert notes == ["chart rendered as a table"]
    assert blocks[0].kind == "table"


def test_empty_layout_raises():
    with pytest.raises(LayoutError):
        render_layout("", style="house", title="Empty")


def test_contrast_pairs_meet_aa():
    for _style, pairs in CONTRAST_PAIRS.items():
        for fore, back in pairs:
            assert _contrast(fore, back) >= 4.5
    for fore, back in BADGE_PAIRS:
        assert _contrast(fore, back) >= 4.5
    assert _contrast("#7f1d1d", "#fff8ee") >= 4.5
    assert _contrast("#ffb020", "#14211b") >= 4.5
    assert _contrast("#ff8d8d", "#14211b") >= 4.5


def test_samples_render_without_notes():
    expectations = {
        "a-pmcc-watch.md": ("Hold both", 'colspan="8"', "Long-term call", "Confirmed", "nasdaq.com"),
        "b-client-brief.md": ("$15,459.15", "asl-chart", "aria-label="),
        "d-long-summary.md": ("Decision", "Rick approved", "asl-kv", "variant-rows"),
    }
    for name, needles in expectations.items():
        html, notes = render_layout((SAMPLES / name).read_text(encoding="utf-8"), style="house", title=name)
        assert notes == []
        for needle in needles:
            assert needle in html
        doc = sanitize_html_document(html)
        assert "Hold both" in doc or "$15,459.15" in doc or "Decision" in doc
