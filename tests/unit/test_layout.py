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
    from artifactsmith.builder import SYSTEM
    from artifactsmith.llm import supports_effort

    assert _tokens(SYNTAX_RULES) <= 400
    house = fast_system_prompt("house")
    assert "===FILE: page.md===" in house
    assert "Visual design:" not in house
    assert "No emoji." in house
    assert "::: stat" in house
    assert "[Confirmed]{.green}" in house
    assert "::: callout" in house
    assert "```chart bar" in house
    assert "chart that column" in house
    assert "Skip years, IDs, ranks, and mixed facts" in house
    assert "July: 38185" in house
    # The example sits outside SYNTAX_RULES. The whole fast prompt stays no
    # longer than the full HTML prompt, so a large source does not pay extra
    # prefill. effort=low is attached only for o1/o3/o4/gpt-5, with no probe.
    assert _tokens(house) <= _tokens(SYSTEM)
    assert supports_effort("gpt-4o-mini") is False
    assert supports_effort("gpt-5-mini") is True
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


def test_bracket_run_renders_quickly():
    import time

    started = time.perf_counter()
    html, notes = render_layout("# T\n\n" + ("[" * 60000), style="house", title="T")
    assert time.perf_counter() - started < 1
    assert notes == []
    assert "[" in html
    assert len(html.encode()) < 200_000


def test_colspan_bomb_is_clamped():
    header = "| " + " | ".join("h{99}" for _ in range(100)) + " |"
    sep = "| " + " | ".join("---" for _ in range(100)) + " |"
    row = "| " + " | ".join("x" for _ in range(100)) + " |"
    source = "# T\n\n" + header + "\n" + sep + "\n" + "\n".join(row for _ in range(100)) + "\n"
    html, notes = render_layout(source, style="house", title="T")
    assert "table clipped" in notes
    spans = [int(item) for item in re.findall(r'colspan="(\d+)"', html)]
    assert spans
    assert max(spans) <= 12
    assert html.count("<td") < 5000
    assert len(html.encode()) < 500_000


def test_plain_markdown_gets_hero_stats_and_badges():
    source = """# Rebate watch

**Hold the plan.** The quarter is closed and the rebate below is owed.

- **Rebate owed:** $15,459
- **Invoices:** 136
- **Billed:** $103,061

| Member | Status | Rebate |
|---|---|---|
| North | Confirmed | $1,705 |
| South | Open | $810 |

| Item | Amount |
|---|---|
| North | $10 |
| Total | $10 |
"""
    html, notes = render_layout(source, style="house", title="Rebate")
    assert notes == []
    assert "asl-hero" in html
    assert "Hold the plan." in html
    assert "asl-stat" in html
    assert "Rebate owed" in html
    assert 'class="asl-badge tone-green"' in html
    assert 'class="asl-badge tone-amber"' in html
    assert "total-line" in html
    assert "max-width:72rem" in html
    assert "margin:-1.25rem" not in html
    doc = sanitize_html_document(html)
    assert "asl-hero" in doc
    assert "asl-stat" in doc
    assert "asl-badge" in doc


def test_callout_aliases_html_fence_and_block_name():
    source = """# T

::: callout
Keep this
:::

::: tip
A tip
:::

::: info
An info
:::

```html
<button>Press</button>
```

::: html
<p>kept</p>
:::

::: this-name-is-longer-than-twenty-four
nope
:::
"""
    html, notes = render_layout(source, style="editorial", title="T")
    assert html.count("asl-callout") >= 3
    assert "Keep this" in html and "A tip" in html and "An info" in html
    assert "<button" not in html
    assert "&lt;button&gt;Press&lt;/button&gt;" in html
    assert "kept" in html
    assert notes == ["unknown block block"]
    assert "twenty-four" not in " ".join(notes)


def test_header_shares_the_content_column():
    for style in STYLES:
        css = stylesheet(style)
        assert "margin:-1.25rem" not in css
        assert ".asl-title,main{max-width:72rem" in css
        assert len(css.encode()) < 8192


def test_styles_design_plain_markdown_themselves():
    source = """# Rebate watch

**Hold the payout.** The rebate below is owed on paid invoices only.

- **Rebate owed:** $15,459
- **Invoices:** 136

## Risks
- High: the payout calendar slips a week
- Watch: one invoice is still open
- Low: the tax line is estimated

```py
print(1)
```

## The record
The first paragraph of this section is long enough to sit beside another one on a desktop page, and it stays real prose rather than a short label.

The second paragraph keeps the line length comfortable and gives the section two columns.
"""
    bold, notes = render_layout(source, style="bold", title="Rebate")
    assert notes == []
    body = bold.split("<body>", 1)[1]
    start = body.index('class="asl-hero-band"')
    risks = body.index("asl-risks")
    assert start < body.index("Hold the payout.") < risks
    assert start < body.index("Rebate owed") < risks
    assert "asl-risks" in bold
    assert "tone-red" in bold and "tone-amber" in bold and "tone-green" in bold
    assert 'class="asl-code panel"' in bold
    assert 'class="dots"' in bold
    doc = sanitize_html_document(bold)
    assert "asl-hero-band" in doc and "asl-risks" in doc and "<script" not in doc.lower()

    editorial, notes = render_layout(source, style="editorial", title="Rebate")
    assert notes == []
    body = editorial.split("<body>", 1)[1]
    hero = body.split('class="asl-hero"', 1)[1].split("</div>", 1)[0]
    assert 'class="drop"' not in hero
    assert '<p class="drop">' in body
    assert 'class="cap"' not in body
    quote = body.split("asl-quote pull", 1)[1].split("</blockquote>", 1)[0]
    assert "real prose rather than a short label" in quote
    assert "rebate below is owed" not in quote
    assert "Hold the payout." not in quote
    assert 'class="sec"' in editorial
    assert "01" in editorial and "02" in editorial
    assert 'class="cols-2"' in editorial
    sheet = editorial.split("<style>", 1)[1].split("</style>", 1)[0]
    assert sheet.count("float:") == 1
    assert "p.drop::first-letter" in sheet and "float:left" in sheet
    saved = sanitize_html_document(editorial)
    assert saved.replace(" ", "").count("float:") == 1
    assert "first-letter" in saved

    house, _notes = render_layout(source, style="house", title="Rebate")
    assert "asl-hero-band" not in house
    assert "asl-risks" not in house
    assert 'class="sec"' not in house

    playful, _notes = render_layout(source, style="playful", title="Rebate")
    assert "asl-hero sticker" in playful
    assert "sticker" in playful.split("<section", 1)[1]

    terminal, _notes = render_layout(source, style="terminal", title="Rebate")
    assert 'class="prompt"' in terminal
    assert "asl-list log" in terminal

    swiss, _notes = render_layout(source, style="swiss", title="Rebate")
    assert "asl-hero swiss" in swiss
    assert 'class="idx"' in swiss
    for style in STYLES:
        assert len(stylesheet(style).encode()) < 8192


def test_editorial_drop_cap_floats_only_the_first_letter():
    source = """# Rebate

::: note
For July the verdict sits in this callout and must not take a drop cap on the letter F.
:::

The body opens with a long line and a **bold rebate** phrase that stays inside the same paragraph so the letter floats beside the words.
"""
    html, notes = render_layout(source, style="editorial", title="Rebate")
    assert notes == []
    body = html.split("<body>", 1)[1]
    callout = body.split("</aside>", 1)[0]
    assert 'class="drop"' not in callout
    para = body.split('<p class="drop">', 1)[1].split("</p>", 1)[0]
    assert "<strong>bold rebate</strong>" in para
    assert "cap" not in para
    sheet = html.split("<style>", 1)[1].split("</style>", 1)[0]
    assert sheet.count("float:") == 1
    assert "display:flow-root" in sheet
    assert "display:grid" not in sheet.split("p.drop{", 1)[1].split("}", 1)[0]
    doc = sanitize_html_document(html)
    compact = doc.replace(" ", "")
    assert compact.count("float:") == 1
    assert "display:flow-root" in compact

    short = """# Rebate

## Facts
Account value $62,832. Available funds (not cash) $30,193.

## Notes
Prices as of 2:44 PM ET.

## Later
The third section holds a long prose paragraph that must not take a drop cap once the opening sections had none at all, and the sentence keeps going so length alone would have qualified it.
"""
    html, notes = render_layout(short, style="editorial", title="Rebate")
    assert notes == []
    assert '<p class="drop">' not in html.split("<body>", 1)[1]

    labeled = """# Rebate

## Facts
Source: transcript dated 2026-10-09 and the rest of this note is long enough that length alone would qualify it for a drop cap on the page.
"""
    html, notes = render_layout(labeled, style="editorial", title="Rebate")
    assert notes == []
    assert '<p class="drop">' not in html.split("<body>", 1)[1]


def test_pull_quote_is_a_sentence():
    dated = """# Brief

The window runs **July 1 to September 30, 2026** and the rebate is owed on paid invoices only.
"""
    html, notes = render_layout(dated, style="editorial", title="Brief")
    assert notes == []
    assert "asl-quote pull" not in html

    numbered = """# Brief

**$15,459.15**
"""
    html, notes = render_layout(numbered, style="editorial", title="Brief")
    assert notes == []
    assert "asl-quote pull" not in html

    short = """# Brief

**Hold the payout.**
"""
    html, notes = render_layout(short, style="editorial", title="Brief")
    assert notes == []
    assert "asl-quote pull" not in html

    quoted = """# Brief

::: note
Hold the payout. The rebate below is owed on paid invoices only and the date is July 1 to September 30, 2026.
:::

## Record
> July 1 to September 30, 2026

The rebate stays on paid invoices across the whole quarter and nothing else is due.
"""
    html, notes = render_layout(quoted, style="editorial", title="Brief")
    assert notes == []
    quote = re.sub("<[^>]+>", "", html.split('class="asl-quote pull"', 1)[1].split("</blockquote>", 1)[0])
    assert "September 30" not in quote
    assert "Hold the payout" not in quote
    assert "rebate stays on paid invoices" in quote
    assert len(quote.split()) >= 8


def test_bold_risk_cards_fit_real_items():
    detail = (
        "high chance the payout calendar slips a week and the invoice stays blocked until the next cycle closes. "
        "high chance the payout calendar slips a week and the invoice stays blocked until the next cycle closes"
    )
    lead = f"Payout calendar: {detail}"
    assert 160 < len(lead) <= 300
    source = f"""# Rebate

## Main risks
- **Payout calendar:** {detail}
- **Open invoice:** one invoice is still open and the tax line is only an estimate until the books close
"""
    html, notes = render_layout(source, style="bold", title="Rebate")
    assert notes == []
    assert "asl-risks" in html
    assert 'class="risk-title">Payout calendar</strong>' in html
    assert "tone-red" in html.split("<body>", 1)[1]

    too_long = "Issue: " + ("x" * 320)
    assert len(too_long) > 320
    html, notes = render_layout(
        f"# Rebate\n\n## Issues\n- High: {too_long}\n- Low: still open\n",
        style="bold",
        title="Rebate",
    )
    assert notes == []
    assert "asl-risks" not in html.split("<body>", 1)[1]

    html, notes = render_layout(
        "# Rebate\n\n## Notes\n- High: the payout slips\n- Low: the tax line is estimated\n",
        style="bold",
        title="Rebate",
    )
    assert notes == []
    assert "asl-risks" not in html.split("<body>", 1)[1]


def _page(headers: list[str], rows: list[list[str]]) -> str:
    head = "| " + " | ".join(headers) + " |"
    rule = "|" + "|".join("---" for _ in headers) + "|"
    body = "\n".join("| " + " | ".join(row) + " |" for row in rows)
    html, notes = render_layout(f"# T\n\n{head}\n{rule}\n{body}\n", style="house", title="T")
    assert notes == []
    return html


def test_auto_bar_chart_follows_a_short_number_table():
    rows = "\n".join(f"| Shop {index} | ${index * 100} |" for index in range(1, 5))
    source = f"""# Bills

| Service | Billed |
|---|---|
{rows}
"""
    html, notes = render_layout(source, style="house", title="Bills")
    assert notes == []
    assert html.count('class="asl-chart"') == 1
    assert html.index("</table>") < html.index('class="asl-chart"')
    assert 'class="bar"' in html
    doc = sanitize_html_document(html)
    assert "<svg" in doc and "<script" not in doc.lower()

    written = source + "\n```chart bar\nA: 1\nB: 2\nC: 3\n```\n"
    html, notes = render_layout(written, style="bold", title="Bills")
    assert notes == []
    assert html.count("<svg") == 1

    short = """# Bills

| Service | Billed |
|---|---|
| One | $1 |
| Two | $2 |
"""
    html, _notes = render_layout(short, style="editorial", title="Bills")
    assert 'class="asl-chart"' not in html

    wide = """# Bills

| Service | Billed | Revenue |
|---|---|---|
| One | $1 | $1 |
| Two | $2 | $1 |
| Three | $3 | $1 |
"""
    html, _notes = render_layout(wide, style="swiss", title="Bills")
    assert 'class="asl-chart"' not in html

    many = "\n".join(f"| Shop {index} | {index} |" for index in range(1, 14))
    html, _notes = render_layout(f"# Bills\n\n| Service | Billed |\n|---|---|\n{many}\n", style="house", title="Bills")
    assert 'class="asl-chart"' not in html

    skipped = [
        (["Product", "Launched"], [["A", "1998"], ["B", "2004"], ["C", "2019"], ["D", "2024"]]),
        (["Customer", "Account ID"], [["Ada", "100234"], ["Bea", "100235"], ["Cam", "100236"]]),
        (["Store", "ZIP"], [["North", "33602"], ["South", "33606"], ["East", "33609"]]),
        (["Office", "Phone"], [["North", "8135550100"], ["South", "8135550101"], ["East", "8135550102"]]),
        (["Metric", "Value"], [["Invoices", "136"], ["Members", "28"], ["Months", "3"], ["Rate", "15"]]),
        (["Model", "Rank"], [["A", "1"], ["B", "2"], ["C", "3"]]),
    ]
    for headers, rows in skipped:
        assert 'class="asl-chart"' not in _page(headers, rows)
        assert "1,998" not in _page(headers, rows)

    mixed_rows = [["Invoices", "136"], ["Members", "28"], ["Months", "3"], ["Rebate rate", "15"]]
    for headers in (
        ["Metric", "Value"],
        ["KPI", "Value"],
        ["Stat", "Total"],
        ["Fact", "Figure"],
        ["Detail", "Count"],
        ["Summary", "Amount"],
        ["Category", "Value"],
    ):
        assert 'class="asl-chart"' not in _page(headers, mixed_rows)

    billing = _page(
        ["Month", "Invoices", "Billed", "Rebate (15%)"],
        [
            ["July", "45", "$38,185", "$5,727.75"],
            ["August", "43", "$28,935", "$4,340.25"],
            ["September", "48", "$35,941", "$5,391.15"],
        ],
    )
    assert billing.count('class="asl-chart"') == 1
    label = billing.split('aria-label="', 1)[1].split('"', 1)[0]
    assert "July" in label and "$38,185" in label and "45" not in label

    revenue = _page(
        ["Region", "Revenue"],
        [["North", "$1,705"], ["South", "$810"], ["East", "$0"]],
    )
    assert revenue.count('class="asl-chart"') == 1
    assert "$1,705" in revenue

    monthly = _page(
        ["Month", "Shop", "Billed"],
        [["July", "North", "$38,185"], ["August", "South", "$28,935"], ["September", "East", "$35,941"]],
    )
    assert monthly.count('class="asl-chart"') == 1
    assert monthly.index("</table>") < monthly.index('class="asl-chart"')
    assert "July" in monthly and "North" not in monthly.split("<svg", 1)[1]

    from artifactsmith.layout.chart import aria_label

    years = aria_label("bar", "", ["A", "B"], [1998, 2004], "")
    assert "1998" in years and "2004" in years and "1,998" not in years
    assert "$1,998" in aria_label("bar", "", ["A"], [1998], "$")


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
