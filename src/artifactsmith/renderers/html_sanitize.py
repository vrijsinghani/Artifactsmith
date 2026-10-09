"""Parser-based HTML allowlist for standalone exports (nh3 / Ammonia + tinycss2).

Hosted preview adds CSP and sandbox headers; downloaded files do not, so the
stored bytes must be safe without those defenses.
"""

from __future__ import annotations

import re
from html import unescape

import nh3
import tinycss2  # type: ignore[import-untyped]
from tinycss2 import ast as css_ast

# Document structure is rebuilt after fragment cleaning (Ammonia drops html/head/body).
_ALLOWED_TAGS: set[str] = {
    "a",
    "abbr",
    "address",
    "article",
    "aside",
    "b",
    "bdi",
    "bdo",
    "blockquote",
    "br",
    "caption",
    "cite",
    "code",
    "col",
    "colgroup",
    "dd",
    "del",
    "details",
    "dfn",
    "div",
    "dl",
    "dt",
    "em",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "i",
    "img",
    "ins",
    "kbd",
    "li",
    "main",
    "mark",
    "nav",
    "ol",
    "p",
    "pre",
    "q",
    "rp",
    "rt",
    "ruby",
    "s",
    "samp",
    "section",
    "small",
    "span",
    "strong",
    "sub",
    "summary",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "time",
    "tr",
    "u",
    "ul",
    "var",
    "wbr",
}

_CLEAN_CONTENT_TAGS: set[str] = {
    "script",
    "style",
    "iframe",
    "object",
    "embed",
    "svg",
    "math",
    "noscript",
    "template",
    "form",
    "input",
    "button",
    "textarea",
    "select",
    "option",
    "base",
    "link",
    "meta",
    "title",
    "head",
    "html",
    "body",
}

_ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "*": {"class", "id", "title", "lang", "dir"},
    "a": {"href", "title"},
    "img": {"alt", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
    "col": {"span"},
    "colgroup": {"span"},
    "ol": {"start", "type"},
    "time": {"datetime"},
    "blockquote": {"cite"},
    "q": {"cite"},
    "del": {"cite", "datetime"},
    "ins": {"cite", "datetime"},
}

_STYLE_PROPS: set[str] = {
    "align-items",
    "background-color",
    "border",
    "border-bottom",
    "border-color",
    "border-left",
    "border-radius",
    "border-right",
    "border-style",
    "border-top",
    "border-width",
    "box-sizing",
    "color",
    "display",
    "flex",
    "flex-direction",
    "flex-wrap",
    "font-family",
    "font-size",
    "font-style",
    "font-weight",
    "gap",
    "grid-template-columns",
    "height",
    "justify-content",
    "letter-spacing",
    "line-height",
    "list-style-type",
    "margin",
    "margin-bottom",
    "margin-left",
    "margin-right",
    "margin-top",
    "max-height",
    "max-width",
    "min-height",
    "min-width",
    "opacity",
    "overflow",
    "padding",
    "padding-bottom",
    "padding-left",
    "padding-right",
    "padding-top",
    "text-align",
    "text-decoration",
    "vertical-align",
    "white-space",
    "width",
    "word-break",
    "z-index",
}

# Rejected CSS function names (after tinycss2 escape resolution).
_BAD_FUNCTIONS: frozenset[str] = frozenset(
    {
        "url",
        "image-set",
        "-webkit-image-set",
        "image",
        "cross-fade",
        "src",
        "element",
        "expression",
        "var",  # custom props can smuggle urls; keep CSS simple
    }
)

for _tag in _ALLOWED_TAGS:
    _ALLOWED_ATTRIBUTES.setdefault(_tag, set()).add("style")

_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.I | re.S)
_STYLE_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.I | re.S)
_BODY_RE = re.compile(r"<body\b[^>]*>(.*?)</body>", re.I | re.S)


def _tokens_safe(tokens: list[object]) -> bool:
    """False if any token can fetch remote content or break out of a style element."""
    for tok in tokens:
        if isinstance(tok, css_ast.URLToken):
            return False
        if isinstance(tok, css_ast.FunctionBlock):
            name = (tok.lower_name or "").lower()
            if name in _BAD_FUNCTIONS:
                return False
            if not _tokens_safe(list(tok.arguments)):
                return False
        if isinstance(tok, css_ast.SquareBracketsBlock):
            if not _tokens_safe(list(tok.content)):
                return False
        if isinstance(tok, css_ast.ParenthesesBlock):
            if not _tokens_safe(list(tok.content)):
                return False
        if isinstance(tok, css_ast.CurlyBracketsBlock):
            if not _tokens_safe(list(tok.content)):
                return False
        if isinstance(tok, (css_ast.StringToken, css_ast.IdentToken)):
            value = getattr(tok, "value", "") or ""
            if "<" in value or ">" in value:
                return False
            low = value.lower()
            if "://" in low or low.startswith("//"):
                return False
    return True


def _serialize_safe(nodes: list[object]) -> str:
    text = str(tinycss2.serialize(nodes))
    if "<" in text or ">" in text:
        return ""
    return text


def sanitize_css(css: str) -> str:
    """Keep only allowlisted declarations; drop at-rules and fetch-capable values.

    Does not HTML-unescape the input: entity-encoded ``</style>`` must stay inert text
    that tinycss2 will not turn into markup.
    """
    if not css or "<" in css:
        # Raw '<' in a style block is always treated as a breakout attempt.
        return ""

    rules = tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True)
    kept: list[str] = []
    for rule in rules:
        if isinstance(rule, css_ast.AtRule):
            # Drop @import, @font-face, @namespace, @media, …
            continue
        if isinstance(rule, css_ast.ParseError):
            continue
        if not isinstance(rule, css_ast.QualifiedRule):
            continue
        prelude = list(rule.prelude)
        content = list(rule.content)
        if not _tokens_safe(prelude) or not _tokens_safe(content):
            continue
        decls = tinycss2.parse_declaration_list(content, skip_comments=True, skip_whitespace=True)
        safe_decls: list[object] = []
        for decl in decls:
            if not isinstance(decl, css_ast.Declaration):
                continue
            name = (decl.lower_name or "").lower()
            if name not in _STYLE_PROPS:
                continue
            if not _tokens_safe(list(decl.value)):
                continue
            safe_decls.append(decl)
        if not safe_decls:
            continue
        body = _serialize_safe(safe_decls)
        if not body.strip():
            continue
        prelude_text = _serialize_safe(prelude).strip()
        if not prelude_text or "<" in prelude_text:
            continue
        kept.append(f"{prelude_text}{{{body}}}")
    out = "".join(kept)
    if "<" in out or ">" in out:
        return ""
    return out


def _url_attribute_filter(tag: str, attr: str, value: str) -> str | None:
    """Allow public http(s) on <a href> only; reject other remote resource URLs."""
    if attr not in ("href", "src", "cite", "xlink:href", "action", "formaction", "poster"):
        return value
    from .links import classify_href, normalize_href, public_href_or_none

    raw = normalize_href(value)
    kind = classify_href(raw)
    if kind == "fragment" or kind == "relative":
        # Relative/fragment only — never protocol-relative (classify blocks those).
        return raw or None
    if tag == "a" and attr == "href" and kind == "public":
        return public_href_or_none(raw)
    # img src, cite, and every other URL-bearing attribute stay local-only.
    return None


_IMG_TAG_RE = re.compile(r"<img\b([^>]*)/?>", re.I)


def _attr(attrs: str, name: str) -> str | None:
    m = re.search(rf"""\b{name}\s*=\s*("([^"]*)"|'([^']*)'|([^\s>]+))""", attrs, re.I)
    if not m:
        return None
    return m.group(2) if m.group(2) is not None else (m.group(3) if m.group(3) is not None else m.group(4))


def rewrite_remote_images_to_links(html: str) -> str:
    """Turn remote ``<img src=https://…>`` into a clickable ``<a href>`` (nothing loads on open)."""
    from html import escape

    from .links import public_href_or_none

    def repl(m: re.Match[str]) -> str:
        attrs = m.group(1)
        src = _attr(attrs, "src") or ""
        alt = (_attr(attrs, "alt") or "").strip() or "image"
        href = public_href_or_none(src)
        if not href:
            # Drop non-public remote images; keep a text label when alt was set.
            return escape(alt) if (_attr(attrs, "alt") or "").strip() else ""
        return (
            f'<a href="{escape(href, quote=True)}" rel="noopener noreferrer nofollow" target="_blank">{escape(alt)}</a>'
        )

    return _IMG_TAG_RE.sub(repl, html)


def sanitize_html_document(body: str) -> str:
    """Return a complete HTML document with allowlisted markup and sanitized CSS.

    Public http(s) ``<a href>`` links are kept (with rel/target hardening). Remote
    ``<img src>`` becomes a clickable link to the image URL. CSS ``url()``, fonts,
    and iframes stay self-contained — no remote subresource fetch on open.
    """
    title_m = _TITLE_RE.search(body)
    title = nh3.clean_text(unescape(title_m.group(1))).strip() if title_m else ""
    # Do not unescape style contents before sanitizing (entity-encoded tags stay inert).
    css = sanitize_css("\n".join(_STYLE_RE.findall(body)))
    if "<" in css or ">" in css:
        css = ""

    body_m = _BODY_RE.search(body)
    fragment = body_m.group(1) if body_m else body
    fragment = _STYLE_RE.sub("", fragment)
    fragment = _TITLE_RE.sub("", fragment)
    # Before nh3 drops remote img src, rewrite public ones to anchors.
    fragment = rewrite_remote_images_to_links(fragment)

    cleaned = nh3.clean(
        fragment,
        tags=_ALLOWED_TAGS,
        clean_content_tags=_CLEAN_CONTENT_TAGS,
        attributes={k: set(v) for k, v in _ALLOWED_ATTRIBUTES.items()},
        attribute_filter=_url_attribute_filter,
        url_schemes={"http", "https"},
        filter_style_properties=_STYLE_PROPS,
        link_rel="noopener noreferrer nofollow",
        strip_comments=True,
    )
    from .links import harden_external_anchors, linkify_html_text

    # Linkify bare public URLs in text nodes; harden rel/target on external <a href>.
    # Private-host URLs left as text fail check_content; attribute filter already
    # dropped them from href/src. Do not run strip_remote_urls here — it would
    # also erase allowed href values.
    cleaned = linkify_html_text(cleaned)
    cleaned = harden_external_anchors(cleaned)

    style_block = f"<style>\n{css}\n</style>\n" if css.strip() else ""
    title_block = f"<title>{title}</title>\n" if title else ""
    doc = (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        f"{title_block}"
        f"{style_block}"
        "</head>\n"
        f"<body>\n{cleaned}\n</body>\n"
        "</html>\n"
    )
    # Final gate: style contents must never contain a tag-open character.
    for m in _STYLE_RE.finditer(doc):
        if "<" in m.group(1):
            doc = _STYLE_RE.sub("<style></style>\n", doc, count=1)
    return doc
