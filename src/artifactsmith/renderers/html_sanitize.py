"""Parser-based HTML allowlist for standalone exports (nh3 / Ammonia + tinycss2).

Hosted preview adds CSP and sandbox headers; downloaded files do not, so the
stored bytes must be safe without those defenses.
"""

from __future__ import annotations

import re
from html import unescape

import nh3

from .css_sanitize import _has_breakout, sanitize_css, sanitize_inline_style

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
    "*": {"class", "id", "title", "lang", "dir", "data-label", "aria-label"},
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

for _tag in _ALLOWED_TAGS:
    _ALLOWED_ATTRIBUTES.setdefault(_tag, set()).add("style")

_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.I | re.S)
_STYLE_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.I | re.S)
_BODY_RE = re.compile(r"<body\b[^>]*>(.*?)</body>", re.I | re.S)
_VIEWPORT_META = '<meta name="viewport" content="width=device-width, initial-scale=1">'


def _url_attribute_filter(tag: str, attr: str, value: str) -> str | None:
    """Allow public http(s) on <a href> only; reject other remote resource URLs."""
    if attr == "style":
        return sanitize_inline_style(value) or None
    if attr not in ("href", "src", "cite", "xlink:href", "action", "formaction", "poster"):
        return value
    from .links import classify_href, emit_href, public_href_or_none

    kind = classify_href(value)
    if kind in ("fragment", "relative"):
        # Relative/fragment only — never protocol-relative (classify blocks those).
        # Emit minimally normalized original (do not percent-decode).
        return emit_href(value) or None
    if tag == "a" and attr == "href":
        # Public http(s), or intentional //host upgraded when the host is public.
        return public_href_or_none(value)
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
    if _has_breakout(css):
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
        f"{_VIEWPORT_META}\n"
        f"{title_block}"
        f"{style_block}"
        "</head>\n"
        f"<body>\n{cleaned}\n</body>\n"
        "</html>\n"
    )
    # Final gate: style contents must not contain a markup breakout.
    for m in _STYLE_RE.finditer(doc):
        if _has_breakout(m.group(1)):
            doc = _STYLE_RE.sub("<style></style>\n", doc, count=1)
    return doc
