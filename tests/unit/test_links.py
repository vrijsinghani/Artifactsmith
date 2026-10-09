"""Public-link policy: allow global http(s); block private and dangerous schemes."""

from __future__ import annotations

import pytest

from artifactsmith.renderers.links import classify_href, is_public_http_url, sanitize_markdown
from artifactsmith.renderers.md_sanitize import collect_link_destinations
from artifactsmith.renderers.safety import find_private_links, sanitize_text


def _assert_destinations_allowed(md: str) -> None:
    """Every re-parsed destination must be public http(s), relative, or fragment."""
    for dest in collect_link_destinations(md):
        kind = classify_href(dest)
        assert kind in ("public", "relative", "fragment"), (dest, kind)
        assert kind != "public" or is_public_http_url(dest), dest


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


# Table-driven CommonMark link/image forms (token-level sanitizer).
# Each row: (name, input, must_contain, must_not_contain_substrings)
_COMMONMARK_CASES: list[tuple[str, str, list[str], list[str]]] = [
    (
        "titled_image_becomes_link",
        '![alt](https://example.com/x.png "My Title")',
        ["[alt](https://example.com/x.png)"],
        ["![alt]", "!["],
    ),
    (
        "titled_image_whitespace_in_parens",
        '![a]( https://example.com/x.png "t" )',
        ["[a](https://example.com/x.png)"],
        ["![a]", "!["],
    ),
    (
        "inline_js_leading_whitespace",
        "[x]( javascript:alert(1) )",
        ["x"],
        ["javascript:", "]("],
    ),
    (
        "inline_js_with_title",
        '[x](javascript:alert(1) "t")',
        ["x"],
        ["javascript:", "]("],
    ),
    (
        "inline_js_angle_dest",
        "[x](<javascript:alert(1)>)",
        ["x"],
        ["javascript:"],
    ),
    (
        "ref_def_js",
        "[lab][ref]\n\n[ref]: javascript:alert(1)\n",
        ["lab"],
        ["javascript:", "][ref]"],
    ),
    (
        "ref_def_js_whitespace_and_title",
        '[lab][ref]\n\n[ref]:  javascript:alert(1)  "title"\n',
        ["lab"],
        ["javascript:"],
    ),
    (
        "autolink_js_as_inline_code",
        "go <javascript:alert(1)> now",
        ["go", "`javascript:alert(1)`", "now"],
        ["<javascript", "](javascript:"],
    ),
    (
        "autolink_https_kept",
        "see <https://example.com/a>",
        ["[https://example.com/a](https://example.com/a)"],
        [],
    ),
    (
        "empty_angle_dest",
        "[x](<>)",
        ["x"],
        ["]("],
    ),
    (
        "empty_parens_dest",
        "[x]()",
        ["x"],
        ["]("],
    ),
    (
        "stray_parens_not_a_link",
        "text (not a link)",
        ["text", "not a link"],
        ["]("],
    ),
    (
        "escaped_image_marker_stays_escaped",
        r"\![alt](https://example.com/x.png)",
        [r"\![alt](https://example.com/x.png)"],
        [],  # destination check below ensures it is a link, not an image token
    ),
    (
        "escaped_bracket_js_stays_inert",
        r"\[x](javascript:alert(1))",
        ["x"],
        [],
    ),
    (
        "percent_encoded_zwsp_js_neutralized",
        "[x](java%E2%80%8Bscript:alert(1))",
        ["x"],
        [],
    ),
    (
        "protocol_relative_upgraded",
        "[NSF](//www.nsf.gov/)",
        ["[NSF](https://www.nsf.gov/)"],
        ["](//"],
    ),
    (
        "public_https_survives",
        "[National Science Foundation](https://www.nsf.gov/)",
        ["[National Science Foundation](https://www.nsf.gov/)"],
        ["](//"],
    ),
    (
        "reference_image_public",
        '![alt][r]\n\n[r]: https://example.com/x.png "title"',
        ["[alt](https://example.com/x.png)"],
        ["![alt]", "!["],
    ),
    (
        "vbscript_inline",
        "[x](vbscript:msgbox(1))",
        ["x"],
        ["vbscript:"],
    ),
    (
        "data_inline",
        "[x](data:text/html,hi)",
        ["x"],
        ["data:"],
    ),
    (
        "file_inline",
        "[x](file:///etc/passwd)",
        ["x"],
        ["file:"],
    ),
]


@pytest.mark.parametrize(
    "name,raw,must_contain,must_not",
    _COMMONMARK_CASES,
    ids=[c[0] for c in _COMMONMARK_CASES],
)
def test_commonmark_link_image_forms(name, raw, must_contain, must_not):
    out = sanitize_markdown(raw)
    for needle in must_contain:
        assert needle in out, f"{name}: expected {needle!r} in {out!r}"
    for banned in must_not:
        assert banned not in out, f"{name}: banned {banned!r} in {out!r}"
        assert banned.lower() not in out.lower(), f"{name}: banned {banned!r} in {out!r}"
    _assert_destinations_allowed(out)
    # Escaped javascript forms must not revive as destinations.
    if "javascript" in raw.lower() or "%e2%80%8b" in raw.lower() or "\u200b" in raw:
        assert collect_link_destinations(out) == [], (name, out)


def test_dangerous_markdown_destinations_neutralized():
    cases = [
        "[x](javascript:alert(1))",
        "[x](vbscript:msgbox(1))",
        "[x](data:text/html,hi)",
        "[x](file:///etc/passwd)",
        "[x](java\u200bscript:alert(1))",
        "[x](java%E2%80%8Bscript:alert(1))",
        "[x](https:\\\\127.0.0.1\\a)",
        r"\[x](javascript:alert(1))",
    ]
    for raw in cases:
        out = sanitize_markdown(raw)
        assert "x" in out, raw
        dests = collect_link_destinations(out)
        assert dests == [], (raw, out, dests)
    # Protocol-relative public hosts upgrade (not neutralized).
    upgraded = sanitize_markdown("[x](//evil.example/a)")
    assert collect_link_destinations(upgraded) == ["https://evil.example/a"]


def test_reference_and_autolink_dangerous_neutralized():
    md = "[click][r]\n\n[r]: javascript:alert(1)\n"
    out = sanitize_markdown(md)
    assert "javascript:" not in out.lower()
    assert "click" in out
    assert "][r]" not in out or "javascript" not in out.lower()

    auto = sanitize_markdown("go <javascript:alert(1)> now")
    assert "`javascript:alert(1)`" in auto
    assert "<javascript" not in auto.lower()
    assert "](javascript:" not in auto.lower()
    assert collect_link_destinations(auto) == []

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
    # Bare private URL stays visible as text (not a destination).
    assert "http://127.0.0.1/x" in private


def test_blocked_angle_autolinks_become_inline_code():
    """Blocked <url> autolinks must not vanish; render as inline code (not links)."""
    out = sanitize_markdown("See <javascript:alert(1)> and <http://127.0.0.1/x>.")
    assert out == "See `javascript:alert(1)` and `http://127.0.0.1/x`."
    assert collect_link_destinations(out) == []
    assert "<javascript" not in out
    assert "](javascript:" not in out
    assert "](http://127.0.0.1" not in out


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
    assert collect_link_destinations(out) == ["https://www.nsf.gov/"]


def test_percent_encoded_format_char_scheme_blocked():
    """markdown-it percent-encodes Zwsp; classifier must still see javascript:."""
    assert classify_href("java%E2%80%8Bscript:alert(1)") == "blocked"
    assert classify_href("java\u200bscript:alert(1)") == "blocked"
    out = sanitize_markdown("[click](java%E2%80%8Bscript:alert(1))")
    assert collect_link_destinations(out) == []


_TRICKY_CORPUS = [
    '![alt](https://example.com/x.png "t")',
    r"\![alt](https://example.com/x.png)",
    r"\[x](javascript:alert(1))",
    "[x]( javascript:alert(1) )",
    "[x](java%E2%80%8Bscript:alert(1))",
    "[x](java\u200bscript:alert(1))",
    "[lab][r]\n\n[r]: javascript:alert(1)\n",
    "go <javascript:alert(1)> now",
    "See <javascript:alert(1)> and <http://127.0.0.1/x>.",
    "see https://example.com/a and [NSF](https://www.nsf.gov/)",
    "[NSF](//www.nsf.gov/)",
    "Use `https://example.com/inline` and\n\n```\nhttps://example.com/fence\n```\n",
    "text (not a link)",
    "[x](<>)",
    "[x]()",
    '![a]( https://example.com/x.png "t" )',
]


@pytest.mark.parametrize("raw", _TRICKY_CORPUS, ids=[f"c{i}" for i in range(len(_TRICKY_CORPUS))])
def test_sanitize_reparsed_is_policy_clean_and_idempotent(raw):
    once = sanitize_markdown(raw)
    _assert_destinations_allowed(once)
    # No image tokens after sanitize.
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark", {"linkify": True})
    md.enable("linkify")
    md.validateLink = lambda _url: True  # type: ignore[assignment]
    for tok in md.parse(once):
        if tok.type == "image":
            raise AssertionError(f"image survived: {once!r}")
        if tok.children:
            for ch in tok.children:
                assert ch.type != "image", once
    twice = sanitize_markdown(once)
    assert twice == once
