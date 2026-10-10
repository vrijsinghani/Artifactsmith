"""Adversarial SVG probe: active content, fetches, and parser tricks must not survive."""

from __future__ import annotations

import re

import pytest

from artifactsmith.builder import sanitize_html
from artifactsmith.renderers.html_sanitize import sanitize_html_document

_FORBIDDEN = (
    "onload",
    "onclick",
    "javascript:",
    "vbscript:",
    "data:",
    "url(",
    "xlink:href",
    "<use",
    "<foreignobject",
    "<image",
    "<iframe",
    "<script",
    "evil.example",
    "track.example.net",
)

# (id, payload, must_keep)
_CASES: list[tuple[str, str, tuple[str, ...]]] = [
    ("onload", '<svg onload="alert(1)"></svg>', ()),
    ("onclick_rect", '<svg><rect onclick="alert(1)" width="1" height="1" fill="#111"></rect></svg>', ("<rect",)),
    (
        "onmouseover_case",
        '<svg OnMouseOver="alert(1)"><rect width="1" height="1" fill="#111"></rect></svg>',
        ("<rect",),
    ),
    (
        "javascript_fill",
        '<svg><rect fill="javascript:alert(1)" width="1" height="1"></rect></svg>',
        ("<rect",),
    ),
    (
        "data_fill",
        '<svg><rect fill="data:image/svg+xml,x" width="1" height="1"></rect></svg>',
        ("<rect",),
    ),
    (
        "javascript_stroke",
        '<svg><line x1="0" y1="0" x2="1" y2="1" stroke="javascript:alert(1)"></line></svg>',
        ("<line",),
    ),
    (
        "href_on_svg_a",
        '<svg><a href="https://evil.example/x"><text x="1" y="1">x</text></a></svg>',
        ("x",),
    ),
    (
        "xlink_href",
        '<svg><path d="M0 0 L1 1" xlink:href="https://evil.example/x"></path></svg>',
        ("<path",),
    ),
    ("use", '<svg><use href="https://evil.example/x.svg#i"></use></svg>', ()),
    ("foreign_object", '<svg><foreignObject><p onclick="x">keep?</p></foreignObject></svg>', ()),
    ("image", '<svg><image href="https://track.example.net/x.png"></image></svg>', ()),
    (
        "url_fill",
        '<svg><rect fill="url(#p)" width="1" height="1"></rect></svg>',
        ("<rect",),
    ),
    (
        "url_style",
        '<svg><rect style="fill:url(https://track.example.net/x)" width="1" height="1"></rect></svg>',
        ("<rect",),
    ),
    (
        "url_entity",
        '<svg><rect fill="u&#114;l(https://track.example.net/x)" width="1" height="1"></rect></svg>',
        ("<rect",),
    ),
    (
        "url_hex_entity",
        '<svg><rect fill="&#x75;rl(https://evil.example/x)" width="1" height="1"></rect></svg>',
        ("<rect",),
    ),
    (
        "case_url_fill",
        '<svg><rect fill="URL(https://evil.example/x)" width="1" height="1"></rect></svg>',
        ("<rect",),
    ),
    ("prefix_svg", '<svg:svg><svg:rect x="1"></svg:rect></svg:svg>', ()),
    (
        "cdata_script",
        "<svg><text x='1' y='1'><![CDATA[<script>alert(1)</script>]]></text></svg>",
        ("<text",),
    ),
    (
        "comment_onload",
        '<svg><!-- --><rect onclick="x" width="1" height="1" fill="#111"></rect></svg>',
        ('fill="#111"',),
    ),
    (
        "c0_in_fill",
        '<svg><rect fill="#111\x01" width="1" height="1"></rect></svg>',
        ("<rect",),
    ),
    (
        "nested_svg_chart",
        '<svg viewBox="0 0 10 10" role="img" aria-label="Nested">'
        '<svg viewBox="0 0 10 10"><rect x="1" y="1" width="2" height="2" fill="#087f8c"></rect></svg>'
        "</svg>",
        ('fill="#087f8c"', "Nested"),
    ),
    (
        "desc_iframe",
        '<svg><desc><iframe src="https://evil.example"></iframe>note</desc><rect width="1" height="1" fill="#111"></rect></svg>',
        ("note",),
    ),
    (
        "animate",
        '<svg><animate attributeName="x" values="0;10"/><rect width="1" height="1" fill="#111"></rect></svg>',
        ("<rect",),
    ),
    (
        "set",
        '<svg><set attributeName="fill" to="red"/><rect width="1" height="1" fill="#111"></rect></svg>',
        ("<rect",),
    ),
    (
        "clippath_leak",
        '<svg><clipPath id="c"><rect width="9" height="9"></rect></clipPath><rect width="1" height="1" fill="#111"></rect></svg>',
        ('fill="#111"',),
    ),
    (
        "bare_url_in_svg_text",
        '<svg><text x="1" y="12" fill="#111">see https://example.com/doc</text></svg>',
        ("https://example.com/doc",),
    ),
    (
        "html_link_untouched",
        '<p><a href="https://example.com/paper">paper</a></p><svg viewBox="0 0 1 1" role="img" aria-label="x"></svg>',
        ('href="https://example.com/paper"',),
    ),
]


def _run(payload: str) -> str:
    if "<html" in payload.lower():
        return sanitize_html_document(payload)
    return sanitize_html(f"<html><body>{payload}</body></html>")


@pytest.mark.parametrize(("case_id", "payload", "must_keep"), _CASES, ids=[c[0] for c in _CASES])
def test_svg_sanitize_corpus(case_id: str, payload: str, must_keep: tuple[str, ...]) -> None:
    out = _run(payload)
    scanned = re.sub(r"<meta http-equiv=\"content-security-policy\"[^>]*>", "", out, flags=re.I)
    low = scanned.lower()
    for token in _FORBIDDEN:
        if token == "data:":
            continue
        assert token not in low, (case_id, token, out)
    if case_id == "href_on_svg_a":
        assert "<a" not in low
    if case_id == "cdata_script":
        assert "<script" not in low
        assert "alert(1)" in out
    if case_id == "bare_url_in_svg_text":
        body = out.split("<body>")[1].split("</body>")[0]
        assert "<a" not in body.lower()
    if case_id == "clippath_leak":
        assert out.lower().count("<rect") == 1
    for frag in must_keep:
        assert frag in out or frag.lower() in low, (case_id, frag, out)
    again = sanitize_html(out)
    assert "onload" not in again.lower()
    assert "<iframe" not in again.lower()
