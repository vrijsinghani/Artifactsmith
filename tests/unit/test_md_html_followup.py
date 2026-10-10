"""Regression tests for post-#8 raw-HTML follow-ups (srcset C0, abrupt comments)."""

from __future__ import annotations

import pytest
from tests.unit.test_reader_corpus import _href_is_private_or_userinfo, _reader_hrefs

from artifactsmith.renderers.md_html import _url_attr_ok, sanitize_raw_html_attrs
from artifactsmith.renderers.md_sanitize import sanitize_markdown


@pytest.mark.parametrize("c0", ["\x1c", "\x1d", "\x1e", "\x1f"])
def test_srcset_c0_separator_blocks_private_candidate(c0: str) -> None:
    val = f"http://example.com{c0}@127.0.0.1/ 1x"
    assert _url_attr_ok("srcset", val) is False
    out = sanitize_raw_html_attrs(f'<img srcset="{val}">')
    assert "srcset" not in out
    assert "127.0.0.1" not in out or "srcset" not in out


def test_srcset_public_candidates_kept() -> None:
    raw = '<img srcset="https://example.com/a.png 1x, https://example.com/b.png 2x">'
    assert sanitize_raw_html_attrs(raw) == raw


@pytest.mark.parametrize(
    "raw",
    [
        '<!--><a href="javascript:alert(1)">x</a>-->\n',
        '<!---><a href="javascript:alert(1)">x</a>-->\n',
        '<!--\n--!><a href="javascript:alert(1)">x</a>-->\n',
        '<!--->\n<a href="javascript:alert(1)">x</a>-->\n',
        '<!--><img src="http://127.0.0.1/x">\n',
    ],
)
def test_abrupt_comment_does_not_hide_bad_attrs(raw: str) -> None:
    once = sanitize_markdown(raw)
    assert sanitize_markdown(once) == once
    for reader in ("markdown-it-linkify", "markdown-it-commonmark"):
        hrefs = _reader_hrefs(once, reader)
        leaks = [h for h in hrefs if _href_is_private_or_userinfo(h)]
        assert leaks == [], (reader, once, leaks)
    assert "javascript:" not in once.lower()
