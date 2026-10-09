"""Parser-based HTML allowlist for standalone exports (nh3 / Ammonia).

Hosted preview adds CSP and sandbox headers; downloaded files do not, so the
stored bytes must be safe without those defenses. Regex stripping is not used
as the HTML sanitizer.
"""

from __future__ import annotations

import re
from html import unescape

import nh3

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
    "style",  # CSS extracted and re-sanitized separately
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
    "img": {"alt", "width", "height"},  # no src: remote and data: URIs are rejected
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

# Inline style properties only (no url(), @import, expression).
_STYLE_PROPS: set[str] = {
    "align-items",
    "background",
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
    "list-style",
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
    "position",
    "text-align",
    "text-decoration",
    "vertical-align",
    "white-space",
    "width",
    "word-break",
    "z-index",
}

# Allow style on common layout tags.
for _tag in _ALLOWED_TAGS:
    _ALLOWED_ATTRIBUTES.setdefault(_tag, set()).add("style")

_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.I | re.S)
_STYLE_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.I | re.S)
_BODY_RE = re.compile(r"<body\b[^>]*>(.*?)</body>", re.I | re.S)
_CSS_URL_RE = re.compile(r"url\s*\(\s*[^)]*\)", re.I)
_CSS_IMPORT_RE = re.compile(r"@import\b[^;]*;?", re.I)
_CSS_EXPRESSION_RE = re.compile(r"expression\s*\([^)]*\)", re.I)
_CSS_BEHAVIOR_RE = re.compile(r"behavior\s*:[^;]*;?", re.I)
_CSS_BINDING_RE = re.compile(r"-moz-binding\s*:[^;]*;?", re.I)
_REMOTEISH_RE = re.compile(
    r"(?:https?|ftp|javascript|vbscript|data)\s*:|&#0*58|&#x0*3a|\\\\|/(/+)",
    re.I,
)


def sanitize_css(css: str) -> str:
    """Remove remote fetches and script-like constructs from author CSS."""
    out = unescape(css)
    out = _CSS_IMPORT_RE.sub("", out)
    out = _CSS_URL_RE.sub("none", out)
    out = _CSS_EXPRESSION_RE.sub("none", out)
    out = _CSS_BEHAVIOR_RE.sub("", out)
    out = _CSS_BINDING_RE.sub("", out)
    # Drop any leftover remote-looking tokens.
    if _REMOTEISH_RE.search(out):
        out = re.sub(r"https?://[^\s\"')]+", "", out, flags=re.I)
        out = re.sub(r"//[^\s\"')]+", "", out)
    return out


def _url_attribute_filter(tag: str, attr: str, value: str) -> str | None:
    """Reject remote, protocol-relative, javascript:, and data: URL attributes."""
    if attr not in ("href", "src", "cite", "xlink:href", "action", "formaction", "poster"):
        return value
    raw = unescape(value).strip()
    low = raw.lower()
    if not raw or raw.startswith("#"):
        return raw or None
    if low.startswith("//") or "://" in low:
        return None
    if low.startswith(("javascript:", "vbscript:", "data:", "blob:")):
        return None
    # Relative paths only (no scheme).
    if re.match(r"^[a-z][a-z0-9+.-]*:", low):
        return None
    return raw


def sanitize_html_document(body: str) -> str:
    """Return a complete HTML document with allowlisted markup and sanitized CSS."""
    title_m = _TITLE_RE.search(body)
    title = nh3.clean_text(unescape(title_m.group(1))).strip() if title_m else ""
    css = sanitize_css("\n".join(_STYLE_RE.findall(body)))

    body_m = _BODY_RE.search(body)
    fragment = body_m.group(1) if body_m else body
    # Drop nested document chrome before fragment clean.
    fragment = _STYLE_RE.sub("", fragment)
    fragment = _TITLE_RE.sub("", fragment)

    cleaned = nh3.clean(
        fragment,
        tags=_ALLOWED_TAGS,
        clean_content_tags=_CLEAN_CONTENT_TAGS,
        attributes={k: set(v) for k, v in _ALLOWED_ATTRIBUTES.items()},
        attribute_filter=_url_attribute_filter,
        url_schemes=set(),
        filter_style_properties=_STYLE_PROPS,
        link_rel=None,
        strip_comments=True,
    )
    # Belt: strip any remote URL text that survived entity decoding quirks.
    from .safety import strip_remote_urls

    cleaned = strip_remote_urls(cleaned)

    style_block = f"<style>\n{css}\n</style>\n" if css.strip() else ""
    title_block = f"<title>{title}</title>\n" if title else ""
    return (
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
