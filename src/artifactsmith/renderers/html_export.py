"""Export-policy tag and fail-closed reparse of the assembled HTML document."""

from __future__ import annotations

from xml.etree.ElementTree import Element

import tinyhtml5

from .svg_sanitize import SVG_TAGS, svg_attr_allowed

HTML_NS = "http://www.w3.org/1999/xhtml"
SVG_NS = "http://www.w3.org/2000/svg"
EXPORT_CSP = (
    "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; "
    "img-src data:; font-src data:; object-src 'none'; base-uri 'none'; form-action 'none'"
)
EXPORT_CSP_META = f'<meta http-equiv="Content-Security-Policy" content="{EXPORT_CSP}">'
_DOC_TAGS = frozenset({"html", "head", "body", "title", "style", "meta"})


def _split_tag(tag: str) -> tuple[str, str]:
    if tag.startswith("{") and "}" in tag:
        ns, name = tag[1:].split("}", 1)
        return ns, name.lower()
    return "", tag.lower()


def verify_export_document(doc: str, html_tags: set[str]) -> None:
    """Reparse the assembled page; unexpected namespaces or tags fail closed."""
    tree = tinyhtml5.parse(doc)
    html_el = None
    for el in tree.iter():
        ns, name = _split_tag(str(el.tag))
        if ns == HTML_NS and name == "html":
            html_el = el
            break
    if html_el is None:
        raise ValueError("sanitized HTML failed the export safety check")
    head = next((c for c in html_el if _split_tag(str(c.tag)) == (HTML_NS, "head")), None)
    if head is None:
        raise ValueError("sanitized HTML failed the export safety check")
    first = next((c for c in head if isinstance(c.tag, str)), None)
    if first is None or _split_tag(str(first.tag)) != (HTML_NS, "meta"):
        raise ValueError("sanitized HTML failed the export safety check")
    if (first.get("http-equiv") or "") != "Content-Security-Policy" or (first.get("content") or "") != EXPORT_CSP:
        raise ValueError("sanitized HTML failed the export safety check")
    allowed = html_tags | set(_DOC_TAGS)
    _walk(html_el, in_svg=False, html_ok=allowed)


def _walk(el: Element, *, in_svg: bool, html_ok: set[str]) -> None:
    ns, name = _split_tag(str(el.tag))
    if ns == SVG_NS:
        if name not in SVG_TAGS or (not in_svg and name != "svg"):
            raise ValueError("sanitized HTML failed the export safety check")
        for key in el.keys():
            if key.lower().startswith("on") or key.lower() in {"href", "xlink:href"}:
                raise ValueError("sanitized HTML failed the export safety check")
            if not svg_attr_allowed(name, key) and key.lower() != "style":
                raise ValueError("sanitized HTML failed the export safety check")
        next_svg = True
    elif ns == HTML_NS:
        if name not in html_ok or in_svg:
            raise ValueError("sanitized HTML failed the export safety check")
        next_svg = False
    else:
        raise ValueError("sanitized HTML failed the export safety check")
    for child in el:
        if isinstance(child.tag, str):
            _walk(child, in_svg=next_svg, html_ok=html_ok)
