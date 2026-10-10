"""Real-reader regression for sanitized markdown destinations.

Three corpora:

1. **Authority** — WS × SHAPES × FORMS (11 whitespace/odd separators inside
   destinations × 5 attack URL shapes × 11 Markdown forms) plus EXTRA cases
   from the probe_ws / test_links_authority generator (610 cases). After
   sanitize, readers must emit **no private / userinfo anchors** (leaks).
   Bare forms terminated by SP/TAB/LF/CR may keep a public prefix link; that
   is not a leak.

2. **Raw HTML** — same WS × SHAPES in ``<a href>`` (inline and block). After
   sanitize: idempotent; no private/userinfo anchors under all readers.

3. **Hosts** — private-host shapes in common Markdown forms (kept from the
   earlier host corpus). Also known-blocked (no private anchors).

Readers: markdown-it (commonmark, and commonmark+linkify) always; pandoc
``markdown`` / ``commonmark`` / ``gfm`` / ``commonmark_x`` when pandoc is on
PATH (those parameters skip if missing). Sanitize is idempotent over both.
"""

from __future__ import annotations

import itertools
import shutil
import subprocess
from html.parser import HTMLParser
from urllib.parse import urlparse

import pytest
from markdown_it import MarkdownIt

from artifactsmith.renderers.md_sanitize import sanitize_markdown
from artifactsmith.renderers.safety import _host_is_private

# --- Authority corpus: WS × SHAPES × FORMS (+ EXTRA) -------------------------

_WS: list[str] = [
    "\t",
    " ",
    "  ",
    "\u00a0",
    "\x0b",
    "\x0c",
    "\n",
    "\r",
    "\u200b",
    "\u3000",
    "",
]
_WS_NAMES = ["TAB", "SP", "SP2", "NBSP", "VT", "FF", "LF", "CR", "ZWSP", "U+3000", "none"]

_SHAPES: list[str] = [
    "https://example.com{w}@127.0.0.1/x",
    "https://example.com%23{w}@127.0.0.1/x",
    "https:{w}//127.0.0.1/x",
    "https://user{w}:pass@example.com/x",
    "https://example.com{w}@192.168.30.4:8006/",
]

_FORMS: list[tuple[str, str]] = [
    ("inline", "See [L]({u}) end.\n"),
    ("inline<>", "See [L](<{u}>) end.\n"),
    ("inline-titled", 'See [L]({u} "t") end.\n'),
    ("refdef", "[r]: {u}\n\nSee [x][r].\n"),
    ("refdef<>", "[r]: <{u}>\n\nSee [x][r].\n"),
    ("refdef-titled", '[r]: {u} "t"\n\nSee [x][r].\n'),
    ("autolink<>", "See <{u}> end.\n"),
    ("bare-url", "Bare {u} end.\n"),
    ("image-in-link", "See [![i](data:,x)]({u}) end.\n"),
    ("list-item", "- item [L]({u})\n"),
    ("nested-brackets", "See [a [b] c]({u}) end.\n"),
]

_EXTRA: list[str] = [
    "See <https://user@example.com/x> and <https://example.com%23@127.0.0.1/x>.\n",
    "See <https://example.com\t@127.0.0.1/x> end.\n",
    "See [L](https://example.com\r@127.0.0.1/x) end.\r\nNext\r",
    "Bare https://example.com\u3000@127.0.0.1/x and https://example.com\u00a0@192.168.30.4/ end.\n",
    "- item https://example.com\u3000@127.0.0.1/x\n",
]

_AUTHORITY_CASES: list[tuple[str, str, str, str]] = [
    (wn, fn, s.format(w=w), ft.format(u=s.format(w=w)))
    for s, (w, wn), (fn, ft) in itertools.product(_SHAPES, zip(_WS, _WS_NAMES, strict=True), _FORMS)
]
for i, src in enumerate(_EXTRA):
    _AUTHORITY_CASES.append(("extra", f"extra{i}", "-", src))

assert len(_AUTHORITY_CASES) == 610

# --- Raw HTML probe: WS × SHAPES × (inline | block) -------------------------

_HTML_FORMS: list[tuple[str, str]] = [
    ("html-a-inline", '<a href="{u}">L</a>\n'),
    ("html-a-block", '<p><a href="{u}">L</a></p>\n'),
]

_HTML_RAW_CASES: list[tuple[str, str, str, str]] = [
    (wn, fn, s.format(w=w), ft.format(u=s.format(w=w)))
    for s, (w, wn), (fn, ft) in itertools.product(_SHAPES, zip(_WS, _WS_NAMES, strict=True), _HTML_FORMS)
]

# Quote-confusion + leading C0 / DEL (tokenizer must see the real href/src).
_HTML_BYPASS_URLS: list[str] = [
    "javascript:alert(1)",
    "data:text/html,x",
    "vbscript:msg",
    "file:///etc/passwd",
    "http://example.com&#64;127.0.0.1/",
    "http://127.0.0.1\\@example.com/",
    "http:&#9;//127.0.0.1/x",
    "//127.0.0.1/x",
    "\\\\127.0.0.1/x",
]
_HTML_BYPASS_CASES: list[tuple[str, str]] = []
for u in _HTML_BYPASS_URLS:
    _HTML_BYPASS_CASES.append(("quote-conf", f'See <a data-x="a href=\'" href="{u}" y=\'z\'>here</a>\n'))
    _HTML_BYPASS_CASES.append(("quote-conf-block", f'<p><a data-x="a href=\'" href="{u}" y=\'z\'>here</a></p>\n'))
for c0 in [chr(i) for i in range(0x00, 0x20)] + ["\x7f"]:
    _HTML_BYPASS_CASES.append(("c0-href", f'<a href="{c0}javascript:alert(1)">x</a>\n'))
    _HTML_BYPASS_CASES.append(("c0-src", f'<img src="{c0}javascript:alert(1)">\n'))
    _HTML_BYPASS_CASES.append(("c0-href-unquoted", f"<a href={c0}javascript:alert(1)>x</a>\n"))

assert len(_HTML_RAW_CASES) == len(_WS) * len(_SHAPES) * len(_HTML_FORMS)

# --- Host corpus (private hosts × forms) ------------------------------------

_HOSTS: list[str] = [
    "127.0.0.1",
    "localhost",
    "[::1]",
    "10.0.0.1",
    "192.168.1.1",
    "172.16.0.1",
    "169.254.1.1",
    "100.64.0.1",
    "0.0.0.0",
    "[::ffff:127.0.0.1]",
    "metadata.google.internal",
    "printer.home.arpa",
    "files.lan",
    "app.corp",
    "wiki.internal",
    "thing.local",
    "svc.localhost",
    "127.0.0.1:8080",
    "1.sslip.io",
    "169.254.169.254",
]

_HOST_FORMS: list[tuple[str, str]] = [
    ("bare", "{url}"),
    ("autolink", "<{url}>"),
    ("inline", "[lab]({url})"),
    ("image", "![lab]({url})"),
    ("ref", "[x][r]\n\n[r]: {url}\n"),
]

_HOST_CASES: list[tuple[str, str, str]] = [
    (fn, f"http://{h}/x", ft.format(url=f"http://{h}/x")) for h, (fn, ft) in itertools.product(_HOSTS, _HOST_FORMS)
]


class _HrefCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for key, val in attrs:
            if key == "href" and val:
                self.hrefs.append(val)


def _html_hrefs(html: str) -> list[str]:
    parser = _HrefCollector()
    parser.feed(html)
    return parser.hrefs


def _markdown_it_hrefs(text: str, *, linkify: bool) -> list[str]:
    md = MarkdownIt("commonmark", {"linkify": linkify})
    if linkify:
        md.enable("linkify")
    md.validateLink = lambda _url: True  # type: ignore[assignment]
    return _html_hrefs(md.render(text))


def _pandoc_hrefs(text: str, fmt: str) -> list[str]:
    proc = subprocess.run(
        ["pandoc", "-f", fmt, "-t", "html"],
        input=text,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return _html_hrefs(proc.stdout)


def _href_is_private_or_userinfo(href: str) -> bool:
    """True for private/userinfo destinations; WHATWG-invalid hrefs are not leaks."""
    from artifactsmith.renderers.href_host import urllib_host, whatwg_host
    from artifactsmith.renderers.links import deobfuscate_href

    low = href.lower()
    if low.startswith(("javascript:", "vbscript:", "data:", "file:", "blob:")):
        return True
    if low.startswith("mailto:"):
        addr = href.split(":", 1)[1]
        host = addr.rsplit("@", 1)[-1] if "@" in addr else ""
        return (not host) or _host_is_private(host)
    # Navigable http(s) only: if neither urllib nor WHATWG-style parse yields a
    # host, the href is not browser-navigable (e.g. pandoc-gfm autolink quirks).
    hosts = [h for h in (urllib_host(href), whatwg_host(href), urllib_host(deobfuscate_href(href))) if h]
    if not hosts:
        try:
            parsed = urlparse(href)
        except Exception:  # noqa: BLE001
            return False
        if parsed.scheme in ("http", "https") and (parsed.username is not None or "@" in (parsed.netloc or "")):
            return True
        return False
    try:
        parsed = urlparse(href)
    except Exception:  # noqa: BLE001
        return False
    if parsed.username is not None or parsed.password is not None:
        return True
    if "@" in (parsed.netloc or ""):
        return True
    return any(_host_is_private(h) for h in hosts)


_MDIT_READERS = ("markdown-it-commonmark", "markdown-it-linkify")
_PANDOC_READERS = ("markdown", "commonmark", "gfm", "commonmark_x")


def _reader_hrefs(text: str, reader: str) -> list[str]:
    if reader == "markdown-it-commonmark":
        return _markdown_it_hrefs(text, linkify=False)
    if reader == "markdown-it-linkify":
        return _markdown_it_hrefs(text, linkify=True)
    return _pandoc_hrefs(text, reader)


@pytest.mark.parametrize(
    ("ws", "form", "shape", "raw"),
    _AUTHORITY_CASES,
    ids=[f"auth/{w}/{f}/{i}" for i, (w, f, _, _) in enumerate(_AUTHORITY_CASES)],
)
def test_authority_corpus_idempotent(ws: str, form: str, shape: str, raw: str) -> None:
    once = sanitize_markdown(raw)
    assert sanitize_markdown(once) == once


@pytest.mark.parametrize(
    ("ws", "form", "shape", "raw"),
    _AUTHORITY_CASES,
    ids=[f"auth/{w}/{f}/{i}" for i, (w, f, _, _) in enumerate(_AUTHORITY_CASES)],
)
@pytest.mark.parametrize("reader", _MDIT_READERS)
def test_authority_corpus_mdit_no_leaks(ws: str, form: str, shape: str, raw: str, reader: str) -> None:
    out = sanitize_markdown(raw)
    hrefs = _reader_hrefs(out, reader)
    leaks = [h for h in hrefs if _href_is_private_or_userinfo(h)]
    assert leaks == [], (reader, ws, form, shape, out, leaks)


@pytest.mark.parametrize(
    ("ws", "form", "shape", "raw"),
    _AUTHORITY_CASES,
    ids=[f"auth/{w}/{f}/{i}" for i, (w, f, _, _) in enumerate(_AUTHORITY_CASES)],
)
@pytest.mark.parametrize("reader", _PANDOC_READERS)
def test_authority_corpus_pandoc_no_leaks(ws: str, form: str, shape: str, raw: str, reader: str) -> None:
    if shutil.which("pandoc") is None:
        pytest.skip("pandoc not installed")
    out = sanitize_markdown(raw)
    hrefs = _reader_hrefs(out, reader)
    leaks = [h for h in hrefs if _href_is_private_or_userinfo(h)]
    assert leaks == [], (reader, ws, form, shape, out, leaks)


@pytest.mark.parametrize(
    ("form", "url", "raw"),
    _HOST_CASES,
    ids=[f"host/{f}/{i}" for i, (f, _, _) in enumerate(_HOST_CASES)],
)
def test_host_corpus_idempotent(form: str, url: str, raw: str) -> None:
    once = sanitize_markdown(raw)
    assert sanitize_markdown(once) == once


@pytest.mark.parametrize(
    ("form", "url", "raw"),
    _HOST_CASES,
    ids=[f"host/{f}/{i}" for i, (f, _, _) in enumerate(_HOST_CASES)],
)
@pytest.mark.parametrize("reader", _MDIT_READERS)
def test_host_corpus_mdit_no_leaks(form: str, url: str, raw: str, reader: str) -> None:
    out = sanitize_markdown(raw)
    hrefs = _reader_hrefs(out, reader)
    leaks = [h for h in hrefs if _href_is_private_or_userinfo(h)]
    assert leaks == [], (reader, form, url, out, leaks)


@pytest.mark.parametrize(
    ("form", "url", "raw"),
    _HOST_CASES,
    ids=[f"host/{f}/{i}" for i, (f, _, _) in enumerate(_HOST_CASES)],
)
@pytest.mark.parametrize("reader", _PANDOC_READERS)
def test_host_corpus_pandoc_no_leaks(form: str, url: str, raw: str, reader: str) -> None:
    if shutil.which("pandoc") is None:
        pytest.skip("pandoc not installed")
    out = sanitize_markdown(raw)
    hrefs = _reader_hrefs(out, reader)
    leaks = [h for h in hrefs if _href_is_private_or_userinfo(h)]
    assert leaks == [], (reader, form, url, out, leaks)


@pytest.mark.parametrize(
    ("ws", "form", "shape", "raw"),
    _HTML_RAW_CASES,
    ids=[f"html/{w}/{f}/{i}" for i, (w, f, _, _) in enumerate(_HTML_RAW_CASES)],
)
def test_html_raw_corpus_idempotent(ws: str, form: str, shape: str, raw: str) -> None:
    once = sanitize_markdown(raw)
    assert sanitize_markdown(once) == once


@pytest.mark.parametrize(
    ("ws", "form", "shape", "raw"),
    _HTML_RAW_CASES,
    ids=[f"html/{w}/{f}/{i}" for i, (w, f, _, _) in enumerate(_HTML_RAW_CASES)],
)
@pytest.mark.parametrize("reader", _MDIT_READERS)
def test_html_raw_corpus_mdit_no_leaks(ws: str, form: str, shape: str, raw: str, reader: str) -> None:
    out = sanitize_markdown(raw)
    hrefs = _reader_hrefs(out, reader)
    leaks = [h for h in hrefs if _href_is_private_or_userinfo(h)]
    assert leaks == [], (reader, ws, form, shape, out, leaks)


@pytest.mark.parametrize(
    ("ws", "form", "shape", "raw"),
    _HTML_RAW_CASES,
    ids=[f"html/{w}/{f}/{i}" for i, (w, f, _, _) in enumerate(_HTML_RAW_CASES)],
)
@pytest.mark.parametrize("reader", _PANDOC_READERS)
def test_html_raw_corpus_pandoc_no_leaks(ws: str, form: str, shape: str, raw: str, reader: str) -> None:
    if shutil.which("pandoc") is None:
        pytest.skip("pandoc not installed")
    out = sanitize_markdown(raw)
    hrefs = _reader_hrefs(out, reader)
    leaks = [h for h in hrefs if _href_is_private_or_userinfo(h)]
    assert leaks == [], (reader, ws, form, shape, out, leaks)


@pytest.mark.parametrize("ws", _WS, ids=_WS_NAMES)
def test_html_raw_anchor_idempotent_all_separators(ws: str) -> None:
    """Raw HTML anchors stay stable across a second pass for every separator."""
    raw = f'<a href="https://example.com{ws}@127.0.0.1/x">x</a>\n'
    once = sanitize_markdown(raw)
    assert sanitize_markdown(once) == once
    assert "`" not in once


@pytest.mark.parametrize(
    ("kind", "raw"),
    _HTML_BYPASS_CASES,
    ids=[f"bypass/{k}/{i}" for i, (k, _) in enumerate(_HTML_BYPASS_CASES)],
)
def test_html_bypass_corpus_idempotent(kind: str, raw: str) -> None:
    once = sanitize_markdown(raw)
    assert sanitize_markdown(once) == once


@pytest.mark.parametrize(
    ("kind", "raw"),
    _HTML_BYPASS_CASES,
    ids=[f"bypass/{k}/{i}" for i, (k, _) in enumerate(_HTML_BYPASS_CASES)],
)
@pytest.mark.parametrize("reader", _MDIT_READERS)
def test_html_bypass_corpus_mdit_no_leaks(kind: str, raw: str, reader: str) -> None:
    out = sanitize_markdown(raw)
    hrefs = _reader_hrefs(out, reader)
    leaks = [h for h in hrefs if _href_is_private_or_userinfo(h)]
    assert leaks == [], (reader, kind, out, leaks)


@pytest.mark.parametrize(
    ("kind", "raw"),
    _HTML_BYPASS_CASES,
    ids=[f"bypass/{k}/{i}" for i, (k, _) in enumerate(_HTML_BYPASS_CASES)],
)
@pytest.mark.parametrize("reader", _PANDOC_READERS)
def test_html_bypass_corpus_pandoc_no_leaks(kind: str, raw: str, reader: str) -> None:
    if shutil.which("pandoc") is None:
        pytest.skip("pandoc not installed")
    out = sanitize_markdown(raw)
    hrefs = _reader_hrefs(out, reader)
    leaks = [h for h in hrefs if _href_is_private_or_userinfo(h)]
    assert leaks == [], (reader, kind, out, leaks)


def test_list_continuation_ordered_loose_idempotent() -> None:
    """Ordered loose list items keep marker-width indent across sanitize passes."""
    for raw in (
        "1. item\n\n   continued",
        "1. first\n\n   second para",
    ):
        once = sanitize_markdown(raw)
        twice = sanitize_markdown(once)
        assert once == twice
        assert "continued" in once or "second para" in once
        md = MarkdownIt("commonmark")
        types = [t.type for t in md.parse(once)]
        assert types.count("ordered_list_open") == 1
        assert types.count("paragraph_open") >= 2


def test_authority_corpus_size() -> None:
    assert len(_WS) * len(_SHAPES) * len(_FORMS) + len(_EXTRA) == 610
    assert len(_AUTHORITY_CASES) == 610
    assert len(_HTML_RAW_CASES) == 110
