"""Allowed inline SVG charts must survive sanitizer byte-for-byte."""

from __future__ import annotations

from artifactsmith.builder import sanitize_html

BAR = (
    '<svg viewBox="0 0 100 40" role="img" aria-label="Bar chart of quarterly leads">'
    '<rect x="0" y="10" width="40" height="20" fill="#087f8c"></rect>'
    '<rect x="50" y="4" width="30" height="26" fill="#b7cbd0"></rect>'
    '<text x="8" y="24" fill="#17212b" font-size="12">40</text>'
    "</svg>"
)
LINE = (
    '<svg viewBox="0 0 80 40" role="img" aria-label="Line chart of weekly leads">'
    '<polyline points="0 30 20 10 40 20 60 5 80 25" fill="none" stroke="#087f8c" stroke-width="2"></polyline>'
    "</svg>"
)
DONUT = (
    '<svg viewBox="0 0 42 42" role="img" aria-label="Donut chart of share">'
    '<circle cx="21" cy="21" r="16" fill="none" stroke="#d6d6d6" stroke-width="6"></circle>'
    '<path d="M21 5 a16 16 0 1 1 0 32 a16 16 0 1 1 0 -32" fill="none" stroke="#087f8c" '
    'stroke-width="6" stroke-dasharray="30 70"></path>'
    "</svg>"
)


def _doc(fragment: str) -> str:
    return f"<html><body>{fragment}</body></html>"


def test_bar_chart_survives_byte_for_byte():
    out = sanitize_html(_doc(BAR))
    assert BAR in out


def test_line_chart_survives_byte_for_byte():
    out = sanitize_html(_doc(LINE))
    assert LINE in out


def test_donut_chart_survives_byte_for_byte():
    out = sanitize_html(_doc(DONUT))
    assert DONUT in out


def test_sanitize_html_is_idempotent_for_charts():
    raw = _doc(BAR + LINE + DONUT)
    once = sanitize_html(raw)
    twice = sanitize_html(once)
    assert BAR in once and LINE in once and DONUT in once
    assert BAR in twice and LINE in twice and DONUT in twice


def test_chart_colored_via_css_class_keeps_fill():
    raw = """<!DOCTYPE html><html><head>
    <style>
    :root { --blue: #087f8c; --mute: #b7cbd0; }
    .bar { fill: var(--blue); }
    .bar-mute { fill: var(--mute); }
    .axis { fill: none; stroke: #17212b; stroke-width: 1; }
    </style>
    </head><body>
    <svg viewBox="0 0 100 40" role="img" aria-label="Quarterly leads">
    <rect class="bar" x="0" y="10" width="40" height="20"></rect>
    <rect class="bar-mute" x="50" y="4" width="30" height="26"></rect>
    <line class="axis" x1="0" y1="38" x2="100" y2="38"></line>
    </svg>
    </body></html>"""
    out = sanitize_html(raw)
    compact = "".join(out.split()).lower()
    assert "fill:var(--blue)" in compact
    assert "fill:var(--mute)" in compact
    assert "stroke:#17212b" in compact
    assert 'class="bar"' in out
    assert "<rect" in out.lower()
    assert "url(" not in compact
