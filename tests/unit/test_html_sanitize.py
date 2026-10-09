"""Parser-based HTML sanitizer: standalone exports must be safe without preview CSP."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

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
    <a href="/\\evil.example">slash-backslash</a>
    <a href="\\\\evil.example/x">backslash-host</a>
    </body></html>"""
    out = sanitize_html(raw)
    low = out.lower()
    assert "javascript:" not in low
    assert "//example.com" not in low
    assert "https://example.com" not in low
    assert "evil.example" not in low


def test_style_breakout_via_html_entities_is_removed():
    """Entity-encoded </style><img onerror> must not become live markup after sanitize."""
    raw = (
        "<!DOCTYPE html><html><head>"
        "<style>p{}&lt;/style&gt;&lt;img src=x onerror=document.title='XSS'&gt;</style>"
        "</head><body><p>ok</p></body></html>"
    )
    out = sanitize_html_document(raw)
    assert "</style><img" not in out.lower().replace(" ", "")
    assert "onerror" not in out.lower()
    assert "<img" not in out.lower()
    # Style contents must never contain a tag-open character.
    assert "<" not in out.split("<style>")[1].split("</style>")[0] if "<style>" in out else True


def test_css_escape_url_and_import_dropped():
    assert "evil" not in sanitize_css('p{background:u\\72l("https://evil/x")}')
    assert "evil" not in sanitize_css('@\\69mport "https://evil/a.css"; p{color:red}')
    assert "color:red" in sanitize_css('@\\69mport "https://evil/a.css"; p{color:red}').replace(" ", "")
    assert "evil" not in sanitize_css('p{background:image-set("https://evil/x" 1x)}')
    assert "evil" not in sanitize_css("@font-face{src:u\\72l(//evil/x)} p{color:blue}")
    assert "url(" not in sanitize_css("p{background:url(https://evil/x)}")


def test_css_rejects_raw_tag_open():
    assert sanitize_css("p{color:red}</style><img src=x>") == ""


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
    raw = '<html><body onload="steal()"><a href="javascript:steal()">x</a></body></html>'
    exported = sanitize_html(raw).encode("utf-8")
    text = exported.decode().lower()
    assert "onload" not in text
    assert "javascript:" not in text


def _chrome() -> str | None:
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        path = subprocess.run(["which", name], capture_output=True, text=True, check=False)
        if path.returncode == 0 and path.stdout.strip():
            return path.stdout.strip()
    return None


def _chrome_dump_dom(html: str) -> str:
    """Load html with no CSP via a data: URL and return Chrome --dump-dom output.

    Chrome in this environment often fails to exit after --dump-dom, so we wrap
    the process with ``timeout`` and accept stdout that already contains a DOM.
    """
    import urllib.parse

    chrome = _chrome()
    assert chrome is not None
    url = "data:text/html;charset=utf-8," + urllib.parse.quote(html)
    with tempfile.TemporaryDirectory() as td:
        profile = Path(td) / "chrome-profile"
        profile.mkdir()
        proc = subprocess.run(
            [
                "timeout",
                "25",
                chrome,
                "--headless=old",
                "--disable-gpu",
                "--no-sandbox",
                f"--user-data-dir={profile}",
                "--virtual-time-budget=2000",
                "--timeout=5000",
                "--dump-dom",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=40,
            check=False,
        )
    assert "<html" in proc.stdout.lower(), proc.stderr[-2000:]
    return proc.stdout


@pytest.mark.skipif(_chrome() is None, reason="no headless Chrome available")
def test_harness_detects_live_onerror_without_csp():
    evil = (
        "<!DOCTYPE html><html><body>"
        "<img src=x onerror=\"document.documentElement.setAttribute('data-xss','1')\">"
        "</body></html>"
    )
    dom = _chrome_dump_dom(evil).lower()
    assert 'data-xss="1"' in dom or "data-xss='1'" in dom


@pytest.mark.skipif(_chrome() is None, reason="no headless Chrome available")
def test_standalone_export_style_breakout_does_not_run_in_chrome():
    """Open the sanitized export with no CSP; onerror must not fire."""
    raw = (
        "<!DOCTYPE html><html><head>"
        "<style>p{}&lt;/style&gt;&lt;img src=x onerror=\"document.documentElement.setAttribute('data-xss','1')\"&gt;</style>"
        "</head><body><p>ok</p></body></html>"
    )
    cleaned = sanitize_html_document(raw)
    dom = _chrome_dump_dom(cleaned).lower()
    assert 'data-xss="1"' not in dom
    assert "data-xss='1'" not in dom
    assert "onerror" not in dom
