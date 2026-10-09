"""Parser-based HTML sanitizer: standalone exports must be safe without preview CSP."""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from artifactsmith.builder import sanitize_html
from artifactsmith.renderers.css_sanitize import sanitize_inline_style
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


def _compact(css: str) -> str:
    return "".join(css.split()).lower()


# Builder house-style page: :root tokens and var() for color, type, and layout.
_BUILDER_SHARE_FIXTURE = """
:root {
  --ink: #1a1a1a;
  --paper: #fafafa;
  --accent: #0b6e4f;
  --accent-soft: #e6f4ef;
  --muted: #5c5c5c;
  --line: #d6d6d6;
  --sans: "Segoe UI", system-ui, sans-serif;
}
* { box-sizing: border-box; }
body {
  margin: 0 auto;
  max-width: 68ch;
  color: var(--ink);
  background: var(--paper);
  font: 1rem/1.55 var(--sans);
  padding: 1.5rem 1.25rem;
}
header {
  display: grid;
  grid-template-columns: 1fr auto;
  grid-template-rows: auto auto;
  place-items: start;
  gap: 0.75rem;
  padding-bottom: 1rem;
  border-bottom: 1px solid var(--line);
}
header > p { color: var(--muted, #5c5c5c); margin: 0; }
.hero {
  background: linear-gradient(180deg, var(--accent-soft), var(--paper));
  background-image: linear-gradient(180deg, var(--accent-soft), var(--paper));
  padding: 1.25rem;
  border-radius: 8px;
  box-shadow: 0 1px 2px color-mix(in srgb, var(--ink) 12%, transparent);
}
h1 { color: var(--accent); font-weight: 650; text-wrap: balance; }
.badge {
  position: sticky;
  top: 0;
  inset: 0 auto auto 0;
  accent-color: var(--accent);
}
@media (max-width: 640px) {
  header { grid-template-columns: 1fr; }
  body { font-size: 0.95rem; padding: 1rem; }
}
@supports (display: grid) {
  main { display: grid; row-gap: 1rem; }
}
"""


def test_builder_stylesheet_variables_media_gradients_survive():
    out = sanitize_css(_BUILDER_SHARE_FIXTURE)
    compact = _compact(out)
    required = (
        ":root",
        "--ink:#1a1a1a",
        "--paper:#fafafa",
        "--accent:#0b6e4f",
        "color:var(--ink)",
        "background:var(--paper)",
        "font:1rem/1.55var(--sans)",
        "var(--muted,#5c5c5c)",
        "@media",
        "max-width:640px",
        "linear-gradient",
        "grid-template-columns",
        "grid-template-rows",
        "place-items",
        "box-shadow",
        "text-wrap",
        "position:sticky",
        "@supports",
        "row-gap",
        "header>p",
    )
    missing = [item for item in required if item not in compact]
    assert missing == [], (missing, out)


def test_url_in_custom_property_drops_only_that_declaration():
    css = ":root{--ink:#111;--bg:url(https://evil.example/x.png);--paper:#fff}"
    out = sanitize_css(css)
    compact = _compact(out)
    assert "--ink:#111" in compact
    assert "--paper:#fff" in compact
    assert "evil" not in out.lower()
    assert "url(" not in out.lower()


def test_url_in_var_fallback_drops_only_that_declaration():
    css = "p{color:var(--ink,#111);background:var(--paper,url(https://evil.example/x.png))}"
    out = sanitize_css(css)
    compact = _compact(out)
    assert "color:var(--ink,#111)" in compact
    assert "background" not in compact
    assert "evil" not in out.lower()
    assert "url(" not in out.lower()


def test_url_in_gradient_drops_only_that_declaration():
    css = "p{color:var(--ink);background-image:linear-gradient(red,url(https://evil.example/x.png));margin:0}"
    out = sanitize_css(css)
    compact = _compact(out)
    assert "color:var(--ink)" in compact
    assert "margin:0" in compact
    assert "background-image" not in compact
    assert "evil" not in out.lower()
    assert "url(" not in out.lower()


def test_url_in_media_drops_only_that_declaration():
    css = "@media (max-width: 640px){p{color:var(--ink);background:url(https://evil.example/x.png)}}"
    out = sanitize_css(css)
    compact = _compact(out)
    assert "@media" in compact
    assert "color:var(--ink)" in compact
    assert "evil" not in out.lower()
    assert "url(" not in out.lower()


def test_style_breakout_in_css_still_returns_empty():
    assert sanitize_css("p{color:red}</style><img src=x>") == ""
    assert sanitize_css("p{content:'</style>'}") == ""
    assert sanitize_css(":root{--x:'</style>'}") == ""


def test_css_comment_with_angle_brackets_does_not_wipe_stylesheet():
    out = sanitize_css("/* <header> */ body{color:red}")
    assert "color:red" in _compact(out)
    assert "header" not in out.lower()


def test_media_range_comparisons_are_kept():
    out = sanitize_css("@media (width <= 640px){p{color:red}}")
    compact = _compact(out)
    assert "@media" in compact
    assert "width<=640px" in compact
    assert "color:red" in compact


def test_deeply_nested_media_returns_without_raising():
    css = "@media screen{" * 3000 + "p{color:red}" + "}" * 3000
    assert sanitize_css(css) is not None


def test_content_string_kept_attr_and_counter_dropped():
    out = sanitize_css('p{content:"•";color:red}')
    compact = _compact(out)
    assert "content:" in compact
    assert "color:red" in compact
    out = sanitize_css("p{content:attr(href);color:red}")
    compact = _compact(out)
    assert "content" not in compact
    assert "color:red" in compact
    out = sanitize_css("p{content:counter(section);color:red}")
    compact = _compact(out)
    assert "content" not in compact
    assert "color:red" in compact


def test_background_image_none_and_var_kept():
    out = sanitize_css("p{background-image:none;color:red}")
    compact = _compact(out)
    assert "background-image:none" in compact
    out = sanitize_css("p{background-image:var(--hero);color:red}")
    compact = _compact(out)
    assert "background-image:var(--hero)" in compact


def test_keyframes_and_animation_and_important_kept():
    css = "@keyframes fade{from{opacity:0}to{opacity:1}}p{animation:fade 200ms ease;color:red !important}"
    out = sanitize_css(css)
    compact = _compact(out)
    assert "@keyframes" in compact and "fade" in compact
    assert "opacity:0" in compact
    assert "animation:" in compact
    assert "color:red!important" in compact


_INLINE_FETCH_CASES: tuple[str, ...] = (
    "background:url(https://track.example.net/pixel.png) red",
    "background-image:url(https://track.example.net/a.png)",
    "list-style:url(https://track.example.net/b.png) disc",
    "cursor:url(https://track.example.net/c.png), pointer",
    "content:url(https://track.example.net/d.png)",
    "background:image-set('https://track.example.net/e.png' 1x)",
    "background:image-set(url(https://track.example.net/f.png) 1x)",
    r"background:u\72l(https://track.example.net/esc.png)",
    "background:&#117;rl(https://track.example.net/ent.png)",
    "background:url&lpar;https://track.example.net/lpar.png)",
    "background:URL(https://track.example.net/case.png)",
    "background:var(--missing,url(https://track.example.net/var.png))",
    "background:var(--missing, &#117;rl(https://track.example.net/varent.png))",
    r"background:var(--missing,u\72l(https://track.example.net/varesc.png))",
    "background:url&#40;https://track.example.net/n40.png)",
    "list-style:&#117;rl(https://track.example.net/ls.png)",
    "cursor:&#117;rl(https://track.example.net/cur.png),pointer",
    "content:&#117;rl(https://track.example.net/cnt.png)",
    "background:linear-gradient(red,url(https://track.example.net/grad.png))",
    "font:url(https://track.example.net/x.woff)",
    "background:u/**/rl(https://track.example.net/cmt.png)",
    "background:url/**/(https://track.example.net/cmt2.png)",
    r"background:url\20(https://track.example.net/sp.png)",
)


def _inline_doc(decl: str) -> str:
    if '"' in decl and "'" not in decl:
        return f"<html><body><div style='{decl};color:#111;height:40px'>x</div></body></html>"
    return f'<html><body><div style="{decl};color:#111;height:40px">x</div></body></html>'


def test_inline_style_url_and_image_set_dropped_per_declaration():
    for decl in _INLINE_FETCH_CASES:
        out = sanitize_html(_inline_doc(decl))
        low = out.lower()
        assert "track.example.net" not in low, decl
        assert "url(" not in low, decl
        assert "image-set" not in low, decl
        assert "color:" in low and "height:" in low, (decl, out)


def test_inline_style_f2_obfuscations_dropped():
    """Every F2 fetch obfuscation from the adversarial probe."""
    for decl in _INLINE_FETCH_CASES:
        cleaned = sanitize_inline_style(decl + ";color:#111;height:40px")
        low = cleaned.lower()
        assert "url(" not in low, (decl, cleaned)
        assert "image-set" not in low, (decl, cleaned)
        assert "track.example.net" not in low, (decl, cleaned)
        assert "color:" in low and "height:" in low, (decl, cleaned)


def test_inline_custom_property_survives():
    raw = '<html><body><div style="--w:40%;color:#111;height:40px">x</div></body></html>'
    out = sanitize_html(raw)
    compact = _compact(out)
    assert "--w:40%" in compact
    assert "color:" in compact


def test_sanitize_inline_style_empty_when_only_url():
    assert sanitize_inline_style("background:url(https://track.example.net/x.png)") == ""


def test_regression_builder_share_page_keeps_variable_stylesheet():
    """Production share pages lost most rules because var() emptied each rule."""
    raw = (
        "<!DOCTYPE html><html><head><title>Brief</title>"
        f"<style>{_BUILDER_SHARE_FIXTURE}</style>"
        "</head><body><header><p>Verdict first.</p></header>"
        "<main class='hero'><h1>Keep the tokens</h1></main></body></html>"
    )
    out = sanitize_html_document(raw)
    assert "<style>" in out
    style = out.split("<style>")[1].split("</style>")[0]
    compact = _compact(style)
    assert "--ink:#1a1a1a" in compact
    assert "color:var(--ink)" in compact
    assert "@media" in compact
    assert "linear-gradient" in compact
    assert "</" not in style and "<!" not in style
    assert "verdict first" in out.lower()


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


def test_public_img_becomes_clickable_link():
    raw = '<html><body><img src="https://cdn.example.com/a.png" onerror="alert(1)" alt="chart"></body></html>'
    out = sanitize_html(raw)
    low = out.lower()
    assert "onerror" not in low
    assert "<img" not in low
    assert 'href="https://cdn.example.com/a.png"' in low
    assert "chart" in low
    assert 'rel="noopener noreferrer nofollow"' in low
    assert 'target="_blank"' in low


def test_private_img_src_not_linked():
    raw = '<html><body><img src="http://127.0.0.1/a.png" alt="secret"></body></html>'
    out = sanitize_html(raw).lower()
    assert "<img" not in out
    assert 'href="http://127.0.0.1' not in out
    assert "secret" in out


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


class _ProxyRecorder:
    """Minimal HTTP proxy that records CONNECT/GET targets. Fail-closed network harness."""

    def __init__(self) -> None:
        self.targets: list[str] = []
        self._lock = threading.Lock()
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:  # noqa: A003
                return

            def _record(self, target: str) -> None:
                with recorder._lock:
                    recorder.targets.append(target)

            def do_CONNECT(self) -> None:  # noqa: N802
                self._record("https://" + self.path)
                self.send_error(502, "denied")

            def do_GET(self) -> None:  # noqa: N802
                host = self.headers.get("Host", "")
                self._record(f"http://{host}{self.path}")
                self.send_error(502, "denied")

            def do_HEAD(self) -> None:  # noqa: N802
                self.do_GET()

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        host, port = self._httpd.server_address
        self.url = f"http://{host}:{port}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def __enter__(self) -> _ProxyRecorder:
        self._thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


def _chrome_open_via_proxy(html: str, proxy_url: str) -> str:
    """Serve HTML over local HTTP and open it through ``proxy_url`` (no CSP).

    ``file://`` pages block remote subresources in Chromium, so the document must
    be http(s) for a remote ``<img>`` to attempt a fetch the proxy can record.
    Localhost stays on the default proxy bypass list so the document loads.
    Returns Chrome ``--dump-dom`` stdout so callers can assert the DOM loaded.
    """
    from http.server import SimpleHTTPRequestHandler

    chrome = _chrome()
    assert chrome is not None
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "export.html").write_text(html, encoding="utf-8")
        profile = root / "chrome-profile"
        profile.mkdir()
        directory = str(root)

        class QuietHandler(SimpleHTTPRequestHandler):
            def __init__(self, *args: object, **kwargs: object) -> None:
                super().__init__(*args, directory=directory, **kwargs)  # type: ignore[misc]

            def log_message(self, format: str, *args: object) -> None:  # noqa: A003
                return

        origin = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        host, port = origin.server_address
        thread = threading.Thread(target=origin.serve_forever, daemon=True)
        thread.start()
        try:
            page = f"http://{host}:{port}/export.html"
            proc = subprocess.run(
                [
                    "timeout",
                    "25",
                    chrome,
                    "--headless=old",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--disable-background-networking",
                    "--disable-component-update",
                    "--disable-default-apps",
                    f"--user-data-dir={profile}",
                    f"--proxy-server={proxy_url}",
                    "--virtual-time-budget=3000",
                    "--timeout=5000",
                    "--dump-dom",
                    page,
                ],
                capture_output=True,
                text=True,
                timeout=40,
                check=False,
            )
        finally:
            origin.shutdown()
            origin.server_close()
            thread.join(timeout=5)
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


@pytest.mark.skipif(_chrome() is None, reason="no headless Chrome available")
def test_harness_proxy_records_remote_img_fetch():
    """Positive control: a live remote <img> must produce a proxy hit (harness works)."""
    evil = '<!DOCTYPE html><html><body><img src="https://evil.example/tracker.png" alt="t"></body></html>'
    with _ProxyRecorder() as proxy:
        _chrome_open_via_proxy(evil, proxy.url)
        hits = [t for t in proxy.targets if "evil.example" in t]
        assert hits, f"proxy harness recorded no fetch; targets={proxy.targets!r}"


@pytest.mark.skipif(_chrome() is None, reason="no headless Chrome available")
def test_html_export_with_public_link_does_not_fetch_remote():
    """Opening an export with public links must not phone home (no CSP)."""
    raw = """<!DOCTYPE html><html><body>
    <p>Source: <a href="https://example.com/paper">paper</a></p>
    <img src="https://cdn.example.net/tracker.png" alt="x">
    <p>Also https://example.org/bare</p>
    </body></html>"""
    cleaned = sanitize_html(raw)
    assert 'href="https://example.com/paper"' in cleaned
    # Remote img becomes a clickable link (nothing loads on open).
    assert "<img" not in cleaned.lower()
    assert 'href="https://cdn.example.net/tracker.png"' in cleaned
    assert 'src="https://' not in cleaned.lower()
    watched = ("example.com", "example.org", "cdn.example.net")
    with _ProxyRecorder() as proxy:
        dom = _chrome_open_via_proxy(cleaned, proxy.url)
        assert "<html" in dom.lower()
        assert "paper" in dom.lower()
        remote = [t for t in proxy.targets if any(h in t for h in watched)]
        # Anchors (including rewritten images) must not be fetched on open.
        assert remote == [], remote


@pytest.mark.skipif(_chrome() is None, reason="no headless Chrome available")
def test_html_export_inline_style_url_does_not_fetch_remote():
    parts = []
    for decl in _INLINE_FETCH_CASES:
        parts.append(_inline_doc(decl).split("<body>")[1].split("</body>")[0])
    raw = "<!DOCTYPE html><html><body>" + "".join(parts) + "</body></html>"
    cleaned = sanitize_html(raw)
    assert "track.example.net" not in cleaned.lower()
    assert "url(" not in cleaned.lower()
    with _ProxyRecorder() as proxy:
        dom = _chrome_open_via_proxy(cleaned, proxy.url)
        assert "<html" in dom.lower()
        remote = [t for t in proxy.targets if "track.example.net" in t]
        assert remote == [], remote
