"""Compact regression corpus for CSS / inline-style sanitizer classes."""

from __future__ import annotations

import pytest

from artifactsmith.builder import sanitize_html
from artifactsmith.renderers.css_sanitize import sanitize_inline_style
from artifactsmith.renderers.html_sanitize import sanitize_css, sanitize_html_document

_FORBIDDEN = ("url(", "image-set", "expression(", "evil.example", "track.example.net")

# (id, kind, payload, must_keep)
# kind: css | inline | html
_CASES: list[tuple[str, str, str, tuple[str, ...]]] = [
    ("inline_background", "inline", "background:url(https://evil.example/x.png);color:#111", ("color:#111",)),
    (
        "inline_background_image",
        "inline",
        "background-image:url(https://evil.example/x.png);height:40px",
        ("height:40px",),
    ),
    ("inline_list_style", "inline", "list-style:url(https://evil.example/x.png) disc;color:#111", ("color:#111",)),
    ("inline_cursor", "inline", "cursor:url(https://evil.example/x.png),pointer;color:#111", ("color:#111",)),
    ("inline_content", "inline", "content:url(https://evil.example/x.png);color:#111", ("color:#111",)),
    ("inline_font", "inline", "font:url(https://evil.example/x.woff);color:#111", ("color:#111",)),
    ("inline_filter", "inline", "filter:url(https://evil.example/x.svg);color:#111", ("color:#111",)),
    (
        "entity_117",
        "html",
        '<div style="background:&#117;rl(https://evil.example/x.png);color:#111">x</div>',
        ("color:",),
    ),
    (
        "entity_x75",
        "html",
        '<div style="background:&#x75;rl(https://evil.example/x.png);color:#111">x</div>',
        ("color:",),
    ),
    (
        "entity_lpar",
        "html",
        '<div style="background:url&lpar;https://evil.example/x.png);color:#111">x</div>',
        ("color:",),
    ),
    (
        "entity_40",
        "html",
        '<div style="background:url&#40;https://evil.example/x.png);color:#111">x</div>',
        ("color:",),
    ),
    (
        "entity_double",
        "html",
        '<div style="background:&amp;#117;rl(https://evil.example/x.png);color:#111">x</div>',
        ("color:",),
    ),
    ("escape_u72l", "css", r"p{background:u\72l(https://evil.example/x.png);color:red}", ("color:red",)),
    ("escape_6hex", "css", r"p{background:\75\72\6c(https://evil.example/x.png);color:red}", ("color:red",)),
    ("case_URL", "inline", "background:URL(https://evil.example/x.png);color:#111", ("color:#111",)),
    ("case_Url", "css", "p{background:Url(https://evil.example/x.png);color:red}", ("color:red",)),
    ("comment_split_block", "css", "p{background:u/**/rl(https://evil.example/x.png);color:red}", ("color:red",)),
    ("comment_split_url_paren", "css", "p{background:url/**/(https://evil.example/x.png);color:red}", ("color:red",)),
    ("comment_split_space", "css", "p{background:url /*x*/ (https://evil.example/x.png);color:red}", ("color:red",)),
    ("ws_escape_url20", "css", r"p{background:url\20(https://evil.example/x.png);color:red}", ("color:red",)),
    ("comment_split_inline", "inline", "background:u/**/rl(https://evil.example/x.png);color:#111", ("color:#111",)),
    ("comment_space_inline", "inline", "background:url /*x*/ (https://evil.example/x.png);color:#111", ("color:#111",)),
    ("ws_escape_inline", "inline", r"background:url\20(https://evil.example/x.png);color:#111", ("color:#111",)),
    ("bad_url_custom_prop", "css", ":root{--ink:#111;--bg:url(a b)} p{color:red}", ("--ink:#111", "color:red")),
    (
        "bad_url_var_fallback",
        "css",
        "p{color:red;background:var(--missing,url(a b))}",
        ("color:red",),
    ),
    (
        "nested_var_fallback",
        "css",
        "p{color:red;background:var(--a,var(--b,url(https://evil.example/x.png)))}",
        ("color:red",),
    ),
    ("image_set", "css", 'p{background:image-set("https://evil.example/x.png" 1x);color:red}', ("color:red",)),
    (
        "webkit_image_set",
        "css",
        "p{background:-webkit-image-set(url(https://evil.example/x.png) 1x);color:red}",
        ("color:red",),
    ),
    ("cross_fade", "css", "p{background:cross-fade(url(https://evil.example/x.png));color:red}", ("color:red",)),
    ("element_fn", "css", "p{background:element(#evil);color:red}", ("color:red",)),
    ("expression_fn", "inline", "background:expression(alert(1));color:#111", ("color:#111",)),
    (
        "url_in_gradient",
        "css",
        "p{color:red;background-image:linear-gradient(red,url(https://evil.example/x.png))}",
        ("color:red",),
    ),
    (
        "attr_selector_url",
        "css",
        'a[href^="https://evil.example"]{color:red;background:url(https://evil.example/x.png)} p{color:blue}',
        ("color:blue",),
    ),
    (
        "safe_block_plus_inline_url",
        "html",
        '<style>p{color:red}</style><div style="background:url(https://evil.example/x.png);height:40px">x</div>',
        (
            "color:red",
            "height:",
        ),
    ),
    (
        "import_fontface_in_media",
        "css",
        "@media all{@import url(https://evil.example/x.css);@font-face{src:url(https://evil.example/x.woff)}p{color:red}}",
        ("color:red", "@media"),
    ),
    ("deep_media", "css", "@media all{" * 3000 + "p{color:red}" + "}" * 3000, ()),
    ("deep_supports", "css", "@supports (display:grid){" * 3000 + "p{color:red}" + "}" * 3000, ()),
    ("media_depth_8", "css", "@media all{" * 8 + "p{color:red}" + "}" * 8, ("color:red", "@media")),
    (
        "media_depth_9",
        "css",
        "@media all{" * 8 + "q{color:red}@media all{p{color:lime}}" + "}" * 8,
        ("color:red",),
    ),
    ("comment_breakout_markers", "css", "/* </header> <!doctype */ body{color:red}", ("color:red",)),
    ("media_lt", "css", "@media (width < 640px){p{color:red}}", ("color:red", "@media", "width<640px")),
    ("media_lte", "css", "@media (width <= 640px){p{color:red}}", ("color:red", "@media", "width<=640px")),
    ("comment_note", "css", "/* <note> */ h1{font-size:2rem} p{color:red}", ("font-size:2rem", "color:red")),
    ("legit_custom_props", "css", ":root{--ink:#111;--paper:#fff} p{color:var(--ink)}", ("--ink:#111", "var(--ink)")),
    (
        "legit_var_fallback",
        "css",
        "p{color:var(--muted,#5c5c5c)}",
        ("var(--muted,#5c5c5c)",),
    ),
    ("legit_media", "css", "@media (max-width:640px){p{color:red}}", ("@media", "color:red")),
    (
        "legit_keyframes",
        "css",
        "@keyframes fade{from{opacity:0}to{opacity:1}}p{animation:fade 200ms ease}",
        ("@keyframes", "animation:", "opacity:0"),
    ),
    (
        "legit_gradient",
        "css",
        "p{background:linear-gradient(180deg,red,blue);color:#111}",
        ("linear-gradient", "color:#111"),
    ),
    (
        "legit_grid_slashes",
        "css",
        ".x{grid-area:1 / 2 / 3 / 4;grid-row:1 / 3;color:red}",
        ("grid-area:1/2/3/4", "grid-row:1/3"),
    ),
    ("legit_child_combinator", "css", "header > p{color:red}", ("header>p", "color:red")),
    (
        "legit_https_link",
        "html",
        '<p><a href="https://example.com/paper">paper</a></p>',
        ('href="https://example.com/paper"',),
    ),
    (
        "legit_remote_img_to_link",
        "html",
        '<img src="https://cdn.example.net/a.png" alt="chart">',
        ('href="https://cdn.example.net/a.png"', "chart"),
    ),
]


def _run(kind: str, payload: str) -> str:
    if kind == "css":
        return sanitize_css(payload)
    if kind == "inline":
        return sanitize_inline_style(payload)
    if "<html" in payload.lower():
        return sanitize_html_document(payload)
    return sanitize_html(f"<html><body>{payload}</body></html>")


@pytest.mark.parametrize(("case_id", "kind", "payload", "must_keep"), _CASES, ids=[c[0] for c in _CASES])
def test_css_sanitize_corpus(case_id: str, kind: str, payload: str, must_keep: tuple[str, ...]) -> None:
    out = _run(kind, payload)
    compact = "".join(out.split()).lower()
    low = out.lower()
    for token in _FORBIDDEN:
        assert token not in low, (case_id, token, out)
    if case_id == "media_depth_9":
        assert "lime" not in compact
    if case_id == "legit_remote_img_to_link":
        assert "<img" not in low
    for frag in must_keep:
        assert frag.lower() in compact or frag.lower() in low, (case_id, frag, out)
