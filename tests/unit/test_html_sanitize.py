"""Parser-based HTML sanitizer: standalone exports must be safe without preview CSP."""

from __future__ import annotations

import json
import os
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


def test_allows_public_https_and_http_href():
    raw = """<html><body>
    <a href="https://example.com/paper">https cite</a>
    <a href="http://example.com/old">http cite</a>
    <a href="#section">frag</a>
    </body></html>"""
    out = sanitize_html(raw)
    low = out.lower()
    assert 'href="https://example.com/paper"' in low
    assert 'href="http://example.com/old"' in low
    assert 'rel="noopener noreferrer nofollow"' in low
    assert 'target="_blank"' in low
    assert 'href="#section"' in low


def test_strips_javascript_private_and_protocol_relative_links():
    raw = """<html><body>
    <a href="javascript:alert(1)">j</a>
    <a href="//example.com/x">proto</a>
    <a href="https://127.0.0.1/x">loop</a>
    <a href="https&#58;//127.0.0.1/x">enc-loop</a>
    <a href="/\\evil.example">slash-backslash</a>
    <a href="\\\\evil.example/x">backslash-host</a>
    <a href="java\u200bscript:alert(1)">zwsp</a>
    <a href="vbscript:msgbox(1)">vb</a>
    <a href="data:text/html,x">data</a>
    <a href="file:///etc/passwd">file</a>
    </body></html>"""
    out = sanitize_html(raw)
    low = out.lower()
    assert "javascript:" not in low
    assert "vbscript:" not in low
    assert "data:" not in low
    assert "file:" not in low
    assert 'href="//example.com' not in low
    assert "127.0.0.1" not in low
    assert "evil.example" not in low


def test_linkifies_bare_public_urls_in_text():
    raw = "<html><body><p>See https://example.com/doc for details.</p></body></html>"
    out = sanitize_html(raw)
    assert 'href="https://example.com/doc"' in out
    assert 'rel="noopener noreferrer nofollow"' in out
    assert 'target="_blank"' in out


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
    assert "url(" not in out.lower()
    assert "<p>ok</p>" in out


def test_svg_iframe_removed():
    raw = '<html><body><svg onload="alert(1)"></svg><iframe src="https://evil.com"></iframe><p>x</p></body></html>'
    out = sanitize_html(raw).lower()
    assert "<svg" not in out
    assert "<iframe" not in out
    assert "<p>x</p>" in out


def test_img_src_stripped_even_for_public_hosts():
    raw = '<html><body><img src="https://x.com/a.png" onerror="alert(1)" alt="a"></body></html>'
    out = sanitize_html(raw).lower()
    assert "onerror" not in out
    assert 'src="https://' not in out
    assert 'src="' not in out or 'src=""' in out or "<img" in out


def test_export_bytes_safe_without_csp():
    raw = '<html><body onload="steal()"><a href="javascript:steal()">x</a></body></html>'
    exported = sanitize_html(raw).encode("utf-8")
    text = exported.decode().lower()
    assert "onload" not in text
    assert "javascript:" not in text


def _chrome() -> str | None:
    env = os.environ.get("CHROME_BIN") or os.environ.get("CHROMIUM_BIN")
    if env and Path(env).is_file():
        return env
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


def _chrome_netlog_urls(html: str) -> list[str]:
    """Open sanitized HTML with no CSP; return request URLs from the netlog."""
    chrome = _chrome()
    assert chrome is not None
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        html_path = root / "export.html"
        html_path.write_text(html, encoding="utf-8")
        netlog = root / "netlog.json"
        profile = root / "chrome-profile"
        profile.mkdir()
        subprocess.run(
            [
                "timeout",
                "25",
                chrome,
                "--headless=old",
                "--disable-gpu",
                "--no-sandbox",
                f"--user-data-dir={profile}",
                f"--log-net-log={netlog}",
                "--net-log-capture-mode=Everything",
                "--virtual-time-budget=3000",
                "--timeout=5000",
                "--dump-dom",
                html_path.resolve().as_uri(),
            ],
            capture_output=True,
            text=True,
            timeout=40,
            check=False,
        )
        assert netlog.is_file(), "chrome netlog file was not created"
        assert netlog.stat().st_size > 0, "chrome netlog file is empty"
        raw = netlog.read_text(encoding="utf-8", errors="replace")
        assert raw.strip(), "chrome netlog file has no content"
        # Netlog is JSON; collect string values that look like absolute URLs.
        urls: list[str] = []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Partial netlog on timeout — still scan text (file exists and is non-empty).
            for m in __import__("re").findall(r"https?://[^\s\"']+", raw):
                urls.append(m)
            return urls
        blob = json.dumps(data)
        for m in __import__("re").findall(r"https?://[^\s\"'\\]+", blob):
            urls.append(m)
        return urls


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


@pytest.mark.skipif(_chrome() is None, reason="no headless Chrome available")
def test_html_export_with_public_link_does_not_fetch_remote():
    """Opening an export with a public <a href> must not phone home (no CSP)."""
    raw = """<!DOCTYPE html><html><body>
    <p>Source: <a href="https://example.com/paper">paper</a></p>
    <img src="https://evil.example/tracker.png" alt="x">
    <p>Also https://example.org/bare</p>
    </body></html>"""
    cleaned = sanitize_html(raw)
    assert 'href="https://example.com/paper"' in cleaned
    assert "evil.example" not in cleaned
    assert 'src="https://' not in cleaned.lower()
    urls = _chrome_netlog_urls(cleaned)
    watched = ("example.com", "example.org", "evil.example")
    remote = [u for u in urls if u.startswith(("http://", "https://")) and any(h in u for h in watched)]
    # Anchors must not be fetched on open; stripped img must not appear.
    assert remote == [], remote
