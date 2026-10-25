"""Axes, label thinning, gaps, and wide phone tables for fast-path charts."""

from __future__ import annotations

from artifactsmith.layout.chart import chart_svg, parse_chart
from artifactsmith.layout.chart_scale import (
    LabelMark,
    bar_scale,
    line_scale,
    plan_category_labels,
    point_runs,
    text_width,
)
from artifactsmith.layout.css import stylesheet
from artifactsmith.layout.render import render_layout
from artifactsmith.renderers.html_sanitize import sanitize_html_document

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _rates() -> tuple[list[str], list[float | None]]:
    """33 months, January 2024 through September 2026, October 2025 missing."""
    labels: list[str] = []
    values: list[float | None] = []
    series = {
        2024: [3.7, 3.9, 3.9, 3.9, 3.9, 4.1, 4.2, 4.2, 4.1, 4.1, 4.2, 4.1],
        2025: [4.0, 4.2, 4.2, 4.2, 4.3, 4.1, 4.2, 4.3, 4.3, None, 4.5, 4.4],
        2026: [4.3, 4.4, 4.3, 4.3, 4.3, 4.2, 4.1, 4.1, 4.2],
    }
    for year, points in series.items():
        for month, value in zip(_MONTHS, points, strict=False):
            labels.append(f"{month} {year}")
            values.append(value)
    assert len(labels) == 33
    assert values.count(None) == 1
    return labels, values


def test_line_ticks_stay_on_a_narrow_band():
    scale = line_scale([3.7, 3.9, 4.2, 4.5])
    assert scale.lo <= 3.7
    assert scale.hi >= 4.5
    assert scale.lo > 2
    assert 0 not in scale.ticks
    assert 4 <= len(scale.ticks) <= 6
    for tick in scale.ticks:
        steps = round((tick - scale.lo) / scale.step)
        assert abs(scale.lo + steps * scale.step - tick) < 1e-6


def test_bar_ticks_include_zero_and_cover_the_peak():
    scale = bar_scale([38185, 28935, 35941])
    assert scale.lo == 0
    assert scale.hi >= 38185
    assert 0 in scale.ticks
    assert len(scale.ticks) <= 6


def test_negative_bars_cross_zero():
    scale = bar_scale([-2, 3, 1])
    assert scale.lo < 0 < scale.hi
    assert 0 in scale.ticks
    assert scale.lo <= -2 and scale.hi >= 3


def test_labels_thin_until_they_fit():
    labels, _values = _rates()
    font = 14.0
    slot = 10.0
    marks = plan_category_labels(labels, slot, font)
    shown = [(index, mark) for index, mark in enumerate(marks) if mark.shown]
    assert shown[0][0] == 0
    assert len(shown) < len(labels)
    for (left, mark), (right, _next) in zip(shown, shown[1:], strict=False):
        gap = (right - left) * slot
        reach = text_width(mark.shown, font)
        if mark.rotate:
            reach *= 0.766  # cos(40deg), the horizontal footprint of an end-anchored label
        assert reach <= gap + 1


def test_truncated_label_keeps_its_full_text():
    label = "Supercalifragilisticexpialidocious"
    marks = plan_category_labels([label] * 8, slot=8, font=14)
    truncated = [mark for mark in marks if mark.shown and mark.shown != mark.full]
    assert truncated
    assert all(mark.shown.endswith("…") for mark in truncated)
    assert all(mark.full == label for mark in truncated)


def test_a_missing_point_splits_the_line():
    assert point_runs([1.0, 2.0, None, 4.0]) == [[0, 1], [3]]
    labels, values = _rates()
    spec = parse_chart(
        "chart line unit=% y=Unemployment_rate",
        "\n".join(f"{label}: {'—' if value is None else value}" for label, value in zip(labels, values, strict=True)),
    )
    assert spec is not None
    assert spec.kind == "line"
    assert spec.y_title == "Unemployment rate"
    assert spec.series[0][1].count(None) == 1
    svg = chart_svg("line", labels, values, "%", "Unemployment rate, 2024–2026", y_title="Unemployment rate")
    assert svg.count("<polyline") == 2
    assert svg.count("<circle") == 32
    assert 'class="axis' in svg
    assert "4.0%" in svg or "4.2%" in svg
    assert "<script" not in svg.lower()


def test_thirty_three_month_line_survives_the_sanitizer():
    labels, values = _rates()
    lines = [f"{label}: {'—' if value is None else value}" for label, value in zip(labels, values, strict=True)]
    source = "# US unemployment rate, 2024–2026\n\n```chart line unit=% y=Rate\n" + "\n".join(lines) + "\n```\n"
    html, notes = render_layout(source, style="house", title="US unemployment rate, 2024–2026")
    assert notes == []
    assert html.count("<polyline") == 2
    assert html.count("<circle") == 32
    doc = sanitize_html_document(html)
    assert doc.count("<polyline") == 2
    assert "<circle" in doc
    assert 'class="axis' in doc
    assert "<script" not in doc.lower()
    assert "Unemployment rate" in doc or "Rate" in doc


def test_long_category_names_become_horizontal_bars():
    labels = ["North American wholesale", "Paid search international", "Email retention sequence"]
    spec = parse_chart("chart bar unit=$", "\n".join(f"{label}: {index + 1}" for index, label in enumerate(labels)))
    assert spec is not None
    assert spec.kind == "hbar"
    svg = chart_svg(spec.kind, spec.labels, spec.series[0][1], "$", "Channels")
    assert "<rect" in svg
    assert "<title>North American wholesale</title>" in svg or "North American wholesale" in svg


def test_multi_series_has_a_legend_and_a_body_title_does_not_leak():
    source = """# Compare

```chart line unit=%
| | Men | Women
| Jan | 4.0 | 3.8
| Feb | — | 3.9
| Mar | 4.2 | 4.1
```
"""
    html, notes = render_layout(source, style="house", title="Compare")
    assert notes == []
    assert "Men" in html and "Women" in html
    # Women is one line. Men is two dots with a gap, not a line across February.
    assert html.count("<polyline") == 1
    assert html.count("<circle") == 5
    doc = sanitize_html_document(html)
    body = doc.split("<body>", 1)[1]
    assert "<title>Men</title>" not in body
    assert "Men" in body
    assert "<script" not in doc.lower()


def test_wide_tables_scroll_and_small_tables_stay_cards():
    wide = """# T

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2024 | 3.7 | 3.9 | 3.9 | 3.9 | 3.9 | 4.1 | 4.2 | 4.2 | 4.1 | 4.1 | 4.2 | 4.1 |
| 2025 | 4.0 | 4.2 | 4.2 | 4.2 | 4.3 | 4.1 | 4.2 | 4.3 | 4.3 | — | 4.5 | 4.4 |
| 2026 | 4.3 | 4.4 | 4.3 | 4.3 | 4.3 | 4.2 | 4.1 | 4.1 | 4.2 | — | — | — |
"""
    html, _notes = render_layout(wide, style="house", title="T")
    assert 'class="asl-table-wrap freeze"' in html
    assert "stack" not in html.split("<table", 1)[1].split(">", 1)[0]
    long = "# T\n\n| A | B | C |\n|---|---|---|\n" + "\n".join(f"| r{i} | {i} | {i} |" for i in range(9))
    scrolled, _notes = render_layout(long, style="house", title="T")
    assert "freeze" in scrolled
    assert "stack" not in scrolled.split("<table", 1)[1].split(">", 1)[0]
    small = "# T\n\n| A | B | C | D |\n|---|---|---|---|\n| one | 1 | 2 | 3 |\n| two | 4 | 5 | 6 |\n"
    cards, _notes = render_layout(small, style="house", title="T")
    assert 'class="asl-table-wrap stack"' in cards
    assert 'class="asl-table-wrap freeze"' not in cards
    assert "position:sticky" in html
    for style in ("house", "bold", "editorial", "playful", "terminal", "swiss"):
        assert len(stylesheet(style).encode()) < 8192


def test_svg_title_tooltip_survives_and_a_body_title_does_not():
    page = (
        "<!DOCTYPE html><html><head><title>Page</title></head><body>"
        "<p><title>leak</title>after</p>"
        '<svg viewBox="0 0 40 20" role="img" aria-label="Labels">'
        '<text x="2" y="14" font-size="12"><title>January 2024</title>Jan</text>'
        "</svg></body></html>"
    )
    doc = sanitize_html_document(page)
    assert "<title>Page</title>" in doc
    assert "January 2024" in doc
    assert ">leak<" not in doc
    assert "after" in doc
    script = '<svg><title><script>alert(1)</script>ok</title><rect width="1" height="1" fill="#111"></rect></svg>'
    cleaned = sanitize_html_document(f"<html><head><title>Page</title></head><body>{script}</body></html>")
    assert "<script" not in cleaned.lower()
    assert "<title>Page</title>" in cleaned
    body = cleaned.split("<body>", 1)[1]
    assert "<title>ok</title>" in body
    assert "alert(1)" not in body


def test_mark_type_is_used() -> None:
    mark = LabelMark("Jan", "Jan 2024", True)
    assert mark.shown == "Jan"
