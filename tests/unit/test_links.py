"""Public-link policy: allow global http(s); block private and dangerous schemes."""

from __future__ import annotations

import pytest

from artifactsmith.renderers.links import classify_href, is_public_http_url, sanitize_markdown
from artifactsmith.renderers.safety import find_private_links, sanitize_text


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


def test_markdown_remote_image_becomes_clickable_link():
    out = sanitize_text("See ![chart](https://example.com/chart.png) please")
    assert "![chart]" not in out
    assert "[chart](https://example.com/chart.png)" in out
    empty_alt = sanitize_text("x ![](https://example.com/a.png) y")
    assert "[image](https://example.com/a.png)" in empty_alt
    assert "![" not in empty_alt


def test_dangerous_markdown_destinations_neutralized():
    cases = [
        ("[x](javascript:alert(1))", "x", "javascript:"),
        ("[x](vbscript:msgbox(1))", "x", "vbscript:"),
        ("[x](data:text/html,hi)", "x", "data:"),
        ("[x](file:///etc/passwd)", "x", "file:"),
        ("[x](//evil.example/a)", "x", "](//"),
        ("[x](java\u200bscript:alert(1))", "x", "javascript:"),
        ("[x](https:\\\\127.0.0.1\\a)", "x", "127.0.0.1"),
    ]
    for raw, label, banned in cases:
        out = sanitize_markdown(raw)
        assert label in out, raw
        assert f"]({banned}" not in out.lower().replace("\u200b", ""), raw
        assert "javascript:" not in out.lower().replace("\u200b", "")


def test_reference_and_autolink_dangerous_neutralized():
    md = "[click][r]\n\n[r]: javascript:alert(1)\n"
    out = sanitize_markdown(md)
    assert "javascript:" not in out.lower()
    assert "click" in out
    assert "][r]" not in out or "javascript" not in out.lower()

    auto = sanitize_markdown("go <javascript:alert(1)> now")
    assert "javascript:" not in auto.lower()
    assert "<javascript" not in auto.lower()

    good_auto = sanitize_markdown("see <https://example.com/a>")
    assert "[https://example.com/a](https://example.com/a)" in good_auto


def test_no_linkify_inside_code_fences_or_inline_code():
    md = (
        "Intro https://example.com/out\n\n"
        "```\nhttps://example.com/in-fence\n```\n\n"
        "Use `https://example.com/inline` in code.\n"
    )
    out = sanitize_markdown(md)
    assert "[https://example.com/out](https://example.com/out)" in out
    # Fence and inline code keep the raw URL, not a markdown link wrapper.
    assert "```\nhttps://example.com/in-fence\n```" in out
    assert "`https://example.com/inline`" in out
    assert "](https://example.com/in-fence)" not in out
    assert "](https://example.com/inline)" not in out


@pytest.mark.parametrize(
    "host",
    [
        "http://printer.home.arpa/",
        "http://files.lan/x",
        "http://app.corp/",
        "http://wiki.internal/",
        "http://mail.intranet/",
        "http://db.private/",
        "http://box.localdomain/",
        "http://thing.local/",
        "http://svc.localhost/",
    ],
)
def test_reserved_private_dns_suffixes(host):
    assert find_private_links(f"see {host}")


def test_ipv4_embedded_in_ipv6_treated_private():
    for url in (
        "http://[::ffff:10.0.0.1]/",
        "http://[::10.0.0.1]/",
        "http://[::ffff:192.168.1.5]/path",
    ):
        assert find_private_links(f"see {url}"), url


def test_linkify_markdown_wraps_bare_public_urls():
    out = sanitize_markdown("see https://example.com/a and [x](https://example.org/b)")
    assert "[https://example.com/a](https://example.com/a)" in out
    assert "[x](https://example.org/b)" in out
    private = sanitize_markdown("go http://127.0.0.1/x now")
    assert "](http://127.0.0.1" not in private


def test_https_markdown_link_survives_byte_for_byte():
    """Public https destinations must not become protocol-relative in MD export."""
    from artifactsmith.renderers import get_renderer
    from artifactsmith.renderers.safety import sanitize_text

    src = "Cite [National Science Foundation](https://www.nsf.gov/) here."
    cleaned = sanitize_text(src)
    assert "(https://www.nsf.gov/)" in cleaned
    assert "](//" not in cleaned
    out = get_renderer("markdown").render(title="Sources", body=cleaned)
    text = out.files["document.md"].decode()
    assert "(https://www.nsf.gov/)" in text
    assert "](//" not in text


def test_protocol_relative_upgraded_to_https():
    out = sanitize_markdown("[NSF](//www.nsf.gov/)")
    assert out == "[NSF](https://www.nsf.gov/)"
