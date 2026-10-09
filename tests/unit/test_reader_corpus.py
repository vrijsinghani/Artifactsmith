"""Real-reader regression: sanitized markdown must not revive private destinations.

Corpus is WS × FORMS × SHAPES (610 cases). Each sanitized string is rendered with
markdown-it (commonmark and linkify) and pandoc (gfm, commonmark, markdown).
No href may resolve to a private host or carry userinfo. Sanitize is idempotent.
"""

from __future__ import annotations

import shutil
import subprocess
from html.parser import HTMLParser
from urllib.parse import urlparse

import pytest
from markdown_it import MarkdownIt

from artifactsmith.renderers.md_sanitize import sanitize_markdown
from artifactsmith.renderers.safety import _host_is_private

pytestmark = pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")

# --- corpus dimensions (2 × 5 × 61 = 610) ------------------------------------

_WS: list[tuple[str, str]] = [
    ("plain", "{body}"),
    ("sentence", "See {body} now."),
]

_FORMS: list[tuple[str, str]] = [
    ("bare", "{url}"),
    ("autolink", "<{url}>"),
    ("inline", "[lab]({url})"),
    ("inline_ws", "[lab]( {url} )"),
    ("image", "![lab]({url})"),
]

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
    "[::ffff:10.0.0.1]",
    "metadata.google.internal",
    "printer.home.arpa",
    "files.lan",
    "app.corp",
    "wiki.internal",
    "mail.intranet",
    "db.private",
    "box.localdomain",
    "thing.local",
    "svc.localhost",
    "127.1",
    "0x7f.0.0.1",
    "2130706433",
    "127.0.0.1:8080",
    "localhost.localdomain",
    "[fe80::1]",
    "192.168.0.1",
    "10.255.255.254",
    "172.31.255.255",
    "127.0.0.2",
    "[::ffff:192.168.0.1]",
    "[::10.0.0.1]",
    "localhost:9",
    "[::1]:8080",
    "0x7f000001",
    "0177.0.0.1",
    "127.0.1",
    "test.local",
    "x.localhost",
    "y.localdomain",
    "z.home.arpa",
    "a.lan",
    "b.corp",
    "c.internal",
    "d.intranet",
    "e.private",
    "1.sslip.io",
    "1.nip.io",
    "1.xip.io",
    "1.localtest.me",
    "1.lvh.me",
    "1.vcap.me",
    "1.traefik.me",
    "169.254.169.254",
    "10.0.0.2",
    "10.0.0.3",
    "192.168.100.1",
    "172.16.5.5",
    "127.0.0.3",
    "10.1.2.3",
]

assert len(_HOSTS) == 61

_SHAPES: list[str] = [f"http://{h}/x" for h in _HOSTS]
# Mix schemes and userinfo while keeping length 61.
_SHAPES[1] = "https://127.0.0.1/x"
_SHAPES[2] = "http://user@127.0.0.1/x"
_SHAPES[3] = "http://user:pass@10.0.0.1/x"
_SHAPES[4] = "https://localhost/x"

_CORPUS: list[tuple[str, str, str, str]] = [
    (wname, fname, url, wt.format(body=ft.format(url=url)))
    for wname, wt in _WS
    for fname, ft in _FORMS
    for url in _SHAPES
]
assert len(_CORPUS) == 610


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
    low = href.lower()
    if low.startswith("mailto:"):
        addr = href.split(":", 1)[1]
        host = addr.rsplit("@", 1)[-1] if "@" in addr else ""
        return (not host) or _host_is_private(host)
    try:
        parsed = urlparse(href)
    except Exception:  # noqa: BLE001
        return True
    if parsed.username is not None or parsed.password is not None:
        return True
    if "@" in (parsed.netloc or ""):
        return True
    host = parsed.hostname
    if host is not None and _host_is_private(host):
        return True
    return False


@pytest.mark.parametrize(
    ("wname", "fname", "url", "raw"),
    _CORPUS,
    ids=[f"{w}/{f}/{i}" for i, (w, f, _, _) in enumerate(_CORPUS)],
)
def test_reader_corpus_no_private_href_and_idempotent(wname: str, fname: str, url: str, raw: str) -> None:
    once = sanitize_markdown(raw)
    twice = sanitize_markdown(once)
    assert twice == once, (wname, fname, url, once, twice)

    readers: list[tuple[str, list[str]]] = [
        ("markdown-it-commonmark", _markdown_it_hrefs(once, linkify=False)),
        ("markdown-it-linkify", _markdown_it_hrefs(once, linkify=True)),
        ("pandoc-gfm", _pandoc_hrefs(once, "gfm")),
        ("pandoc-commonmark", _pandoc_hrefs(once, "commonmark")),
        ("pandoc-markdown", _pandoc_hrefs(once, "markdown")),
    ]
    for reader, hrefs in readers:
        for href in hrefs:
            assert not _href_is_private_or_userinfo(href), (reader, wname, fname, url, once, href)


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
        # Still a single ordered list after re-parse (continuation not ejected).
        md = MarkdownIt("commonmark")
        types = [t.type for t in md.parse(once)]
        assert types.count("ordered_list_open") == 1
        assert types.count("paragraph_open") >= 2
