"""Page-wide SVG limits, 2 MB input cap, and non-SVG byte stability."""

from __future__ import annotations

import time

import pytest

from artifactsmith.builder import sanitize_html
from artifactsmith.renderers.html_export import EXPORT_CSP, EXPORT_CSP_META
from artifactsmith.renderers.html_sanitize import HtmlSanitizeError, sanitize_html_document


def _doc(fragment: str) -> str:
    return f"<html><body>{fragment}</body></html>"


def _clean_svg(n: int) -> str:
    return f'<svg viewBox="0 0 1 1" role="img" aria-label="n{n}"></svg>'


def test_page_wide_root_limit_runs_on_clean_input():
    fragment = "".join(_clean_svg(i) for i in range(60))
    out = sanitize_html(_doc(fragment)).lower()
    assert out.count("<svg") == 50


def test_page_wide_element_limit_runs_on_clean_input():
    rects = "".join('<rect x="0" y="0" width="1" height="1" fill="#111"></rect>' for _ in range(3000))
    raw = _doc(f'<svg viewBox="0 0 1 1" role="img" aria-label="many">{rects}</svg>')
    out = sanitize_html(raw).lower()
    assert out.count("<svg") == 0
    assert "<rect" not in out


def test_page_wide_text_node_limit_runs_on_clean_input():
    texts = "".join(f'<text x="0" y="1" fill="#111">{i}</text>' for i in range(1200))
    raw = _doc(f'<svg viewBox="0 0 1 1" role="img" aria-label="text">{texts}</svg>')
    out = sanitize_html(raw).lower()
    assert out.count("<svg") == 0


def test_page_wide_nesting_limit_runs_on_clean_input():
    inner = '<rect x="0" y="0" width="1" height="1" fill="#111"></rect>'
    nested = inner
    for i in range(40):
        nested = f'<svg viewBox="0 0 1 1" role="img" aria-label="d{i}">{nested}</svg>'
    out = sanitize_html(_doc(nested)).lower()
    assert "<rect" not in out


def test_g_nesting_counts_toward_depth_limit():
    inner = '<rect x="0" y="0" width="1" height="1" fill="#111"></rect>'
    nested = inner
    for _ in range(20):
        nested = f"<g>{nested}</g>"
    raw = _doc(f'<svg viewBox="0 0 1 1" role="img" aria-label="deep g">{nested}</svg>')
    out = sanitize_html(raw).lower()
    assert "<rect" not in out


def test_aria_labelledby_dropped_when_id_missing():
    raw = _doc(
        '<svg viewBox="0 0 10 10" role="img" aria-labelledby="missing">'
        '<rect x="0" y="0" width="1" height="1" fill="#111"></rect></svg>'
    )
    out = sanitize_html(raw).lower()
    assert "aria-labelledby" not in out
    assert "<rect" in out


def test_aria_labelledby_kept_when_id_exists():
    raw = _doc(
        '<h2 id="chart-title">Sales</h2>'
        '<svg viewBox="0 0 10 10" role="img" aria-labelledby="chart-title">'
        '<rect x="0" y="0" width="1" height="1" fill="#111"></rect></svg>'
    )
    out = sanitize_html(raw)
    assert 'aria-labelledby="chart-title"' in out
    assert 'id="chart-title"' in out


def test_html_over_2mb_is_rejected():
    raw = "<html><body><p>" + ("x" * 2_000_000) + "</p></body></html>"
    with pytest.raises(HtmlSanitizeError, match="page too large"):
        sanitize_html(raw)


def test_large_html_without_svg_stays_fast():
    raw = "<html><body><p>" + ("x" * 1_500_000) + "</p></body></html>"
    start = time.perf_counter()
    out = sanitize_html(raw)
    elapsed = time.perf_counter() - start
    assert "<p>" in out
    assert "<svg" not in out.lower()
    assert elapsed < 2.0


def test_non_svg_keeps_blank_lines_after_body():
    raw = "<html><body>\n\n<p>hello</p>\n</body></html>"
    out = sanitize_html(raw)
    body = out.split("<body>", 1)[1].split("</body>", 1)[0]
    assert "\n\n<p>hello</p>" in body


def test_export_csp_is_first_in_head():
    out = sanitize_html_document("<html><body><p>x</p></body></html>")
    assert out.index(EXPORT_CSP_META) < out.index('<meta charset="utf-8">')
    assert EXPORT_CSP in out
    assert out.count("Content-Security-Policy") == 1


def test_data_image_survives_and_remote_font_does_not():
    raw = """<!DOCTYPE html><html><head>
    <style>@font-face{src:url(https://evil.example/x.woff)}p{color:red}</style>
    </head><body>
    <img src="data:image/png;base64,iVBORw0KGgo=" alt="dot">
    <img src="https://cdn.example.net/a.png" alt="remote">
    </body></html>"""
    out = sanitize_html_document(raw)
    assert "data:image/png" in out
    assert "<img" in out.lower()
    assert "evil.example" not in out.lower()
    assert "url(" not in out.lower()
    assert 'href="https://cdn.example.net/a.png"' in out
