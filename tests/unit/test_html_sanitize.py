"""Parser-based HTML sanitizer: standalone exports must be safe without preview CSP."""

from __future__ import annotations

from artifactsmith.builder import sanitize_html
from artifactsmith.renderers.html_sanitize import sanitize_css, sanitize_html_document


def test_removes_event_handlers_and_scripts():
    raw = '<html><body onload="alert(1)"><p onclick="x">hi<script>alert(1)</script></p></body></html>'
    out = sanitize_html(raw).lower()
    assert "onload" not in out
    assert "onclick" not in out
    assert "<script" not in out
    assert "alert" not in out
    assert "<html" in out and "</html>" in out


def test_strips_javascript_and_protocol_relative_links():
    raw = """<html><body>
    <a href="javascript:alert(1)">j</a>
    <a href="//example.com/x">proto</a>
    <a href="https://example.com/x">abs</a>
    <a href="https&#58;//example.com/x">enc</a>
    </body></html>"""
    out = sanitize_html(raw)
    low = out.lower()
    assert "javascript:" not in low
    assert "//example.com" not in low
    assert "https://example.com" not in low
    assert "https&#58;" not in low


def test_strips_css_remote_urls_and_imports():
    css = "body{background:url(https://evil.com/x.png)} @import url('https://evil.com/a.css'); color:red"
    clean = sanitize_css(css)
    assert "https://" not in clean.lower()
    assert "@import" not in clean.lower()
    assert "color:red" in clean.replace(" ", "") or "color: red" in clean or "color:red" in clean


def test_style_block_urls_do_not_survive_document_sanitize():
    raw = """<!DOCTYPE html><html><head>
    <style>body{background:url(https://evil.com/x.png)}</style>
    </head><body><p>ok</p></body></html>"""
    out = sanitize_html_document(raw)
    assert "evil.com" not in out
    assert "https://" not in out.lower()
    assert "<p>ok</p>" in out


def test_svg_iframe_removed():
    raw = '<html><body><svg onload="alert(1)"></svg><iframe src="https://evil.com"></iframe><p>x</p></body></html>'
    out = sanitize_html(raw).lower()
    assert "<svg" not in out
    assert "<iframe" not in out
    assert "<p>x</p>" in out


def test_img_src_stripped():
    raw = '<html><body><img src="https://x.com/a.png" onerror="alert(1)" alt="a"></body></html>'
    out = sanitize_html(raw).lower()
    assert "onerror" not in out
    assert "https://" not in out
    assert 'src="' not in out or 'src=""' in out or "<img" in out


def test_export_bytes_safe_without_csp():
    """Downloaded file must not carry active handlers even if opened outside preview."""
    raw = '<html><body onload="steal()"><a href="javascript:steal()">x</a></body></html>'
    exported = sanitize_html(raw).encode("utf-8")
    text = exported.decode().lower()
    assert "onload" not in text
    assert "javascript:" not in text
