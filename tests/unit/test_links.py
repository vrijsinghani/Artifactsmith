"""Public-link policy: allow global http(s); block private and dangerous schemes."""

from __future__ import annotations

import pytest

from artifactsmith.renderers.links import classify_href, is_public_http_url, linkify_markdown


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/paper",
        "http://example.com/old",
        "https://cdn.example.org/a?x=1",
    ],
)
def test_public_http_https_allowed(url):
    assert classify_href(url) == "public"
    assert is_public_http_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JAVASCRIPT:alert(1)",
        "vbscript:msgbox(1)",
        "data:text/html,x",
        "file:///etc/passwd",
        "//example.com/x",
        "https://127.0.0.1/",
        "http://localhost/x",
        "http://192.168.0.1/",
        "java\u200bscript:alert(1)",  # Zwsp format char
        "https:\\\\127.0.0.1\\x",
        "\\\\evil.example\\x",
    ],
)
def test_dangerous_and_private_blocked(url):
    assert classify_href(url) == "blocked"
    assert not is_public_http_url(url)


def test_relative_and_fragment():
    assert classify_href("#top") == "fragment"
    assert classify_href("/local/path") == "relative"
    assert classify_href("docs/page.html") == "relative"


def test_linkify_markdown_wraps_bare_public_urls():
    out = linkify_markdown("see https://example.com/a and [x](https://example.org/b)")
    assert "[https://example.com/a](https://example.com/a)" in out
    assert "[x](https://example.org/b)" in out
    # private bare URL stays plain text (not turned into a markdown link)
    private = linkify_markdown("go http://127.0.0.1/x now")
    assert "](http://127.0.0.1" not in private
