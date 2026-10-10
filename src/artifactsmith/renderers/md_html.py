"""Raw HTML URL-attribute policy for markdown-embedded and HTML documents.

Uses ``html.parser.HTMLParser`` so attribute boundaries are real, not regex.
Public http(s) start tags are left byte-for-byte when every URL-bearing
attribute is allowed; blocked or confused values drop that attribute.
"""

from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser

from markdown_it import MarkdownIt
from markdown_it.token import Token

from .links import classify_href, public_href_or_none

# Destination-style separators, plus C0 file separators (U+001C–U+001F) that
# browsers keep inside a srcset URL token but ``str.split()`` would treat as WS.
_ATTR_CONFUSION_SEP = r"[\t \x0b\x0c\r\n\u00a0\u200b\u3000\ufeff\x1c-\x1f]"
# Browsers split srcset/ping on ASCII whitespace only (not C0 like U+001F).
_ASCII_WS_RE = re.compile(r"[\t\n\f\r ]+")
_ASCII_WS_STRIP = "\t\n\f\r "

_URL_ATTRS = frozenset(
    {
        "href",
        "src",
        "srcset",
        "action",
        "formaction",
        "poster",
        "cite",
        "background",
        "data",
        "xlink:href",
        "ping",
    }
)


def _attr_value_confused(value: str) -> bool:
    sep = _ATTR_CONFUSION_SEP
    if re.search(rf"https?:{sep}+//", value, re.I):
        return True
    if re.search(rf"{sep}+@", value):
        return True
    if re.search(rf"{sep}+:[^\s]*@", value):
        return True
    return False


def href_src_attr_ok(value: str) -> bool:
    """True when a single URL attribute value may stay as written."""
    if _attr_value_confused(value):
        return False
    kind = classify_href(value)
    if kind == "public":
        return public_href_or_none(value) is not None
    return kind in ("relative", "fragment")


def _normalize_abrupt_comments(html: str) -> str:
    """Make abrupt HTML comment ends parse like browsers (CPython < 3.13.6).

    ``<!-->`` / ``<!--->`` and ``--!>`` otherwise swallow following tags so
    their URL attributes are never inspected.
    """
    # Longer abrupt opener first so ``<!--->`` is not partially matched.
    out = html.replace("<!--->", "<!---->")
    out = out.replace("<!-->", "<!---->")
    out = out.replace("--!>", "-->")
    return out


def _url_attr_ok(name: str, value: str | None) -> bool:
    """True when a URL-bearing attribute (possibly list-valued) may stay."""
    if value is None:
        return False
    lname = name.lower()
    if lname == "srcset":
        for part in value.split(","):
            piece = part.strip(_ASCII_WS_STRIP)
            if not piece:
                continue
            url = _ASCII_WS_RE.split(piece, maxsplit=1)[0]
            if url and not href_src_attr_ok(url):
                return False
        return True
    if lname == "ping":
        for url in _ASCII_WS_RE.split(value):
            if url and not href_src_attr_ok(url):
                return False
        return True
    return href_src_attr_ok(value)


def _is_url_attr(name: str) -> bool:
    return name.lower() in _URL_ATTRS


def _rebuild_start_tag(tag: str, attrs: list[tuple[str, str | None]], *, self_closing: bool) -> str:
    parts = [f"<{tag}"]
    for name, val in attrs:
        if val is None:
            parts.append(f" {name}")
        else:
            parts.append(f' {name}="{escape(val, quote=True)}"')
    parts.append(" />" if self_closing else ">")
    return "".join(parts)


def _filter_attrs(
    attrs: list[tuple[str, str | None]],
) -> tuple[list[tuple[str, str | None]], list[str]]:
    """Return (kept attrs, problem strings for dropped URL attrs)."""
    kept: list[tuple[str, str | None]] = []
    problems: list[str] = []
    for name, val in attrs:
        if _is_url_attr(name) and not _url_attr_ok(name, val):
            shown = "" if val is None else val[:80]
            problems.append(f"blocked or authority-confused HTML {name}: {shown}")
            continue
        kept.append((name, val))
    return kept, problems


class _HtmlAttrParser(HTMLParser):
    """Rewrite or inspect start tags; pass through the rest unchanged."""

    def __init__(self, *, rewrite: bool) -> None:
        super().__init__(convert_charrefs=True)
        self._rewrite = rewrite
        self.parts: list[str] = []
        self.problems: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        original = self.get_starttag_text() or f"<{tag}>"
        kept, problems = _filter_attrs(attrs)
        self.problems.extend(problems)
        if not self._rewrite:
            return
        if not problems:
            self.parts.append(original)
            return
        self_closing = original.rstrip().endswith("/>")
        self.parts.append(_rebuild_start_tag(tag, kept, self_closing=self_closing))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        original = self.get_starttag_text() or f"<{tag} />"
        kept, problems = _filter_attrs(attrs)
        self.problems.extend(problems)
        if not self._rewrite:
            return
        if not problems:
            self.parts.append(original)
            return
        self.parts.append(_rebuild_start_tag(tag, kept, self_closing=True))

    def handle_endtag(self, tag: str) -> None:
        if self._rewrite:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._rewrite:
            self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        if self._rewrite:
            self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._rewrite:
            self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        if self._rewrite:
            self.parts.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        if self._rewrite:
            self.parts.append(f"<!{decl}>")

    def handle_pi(self, data: str) -> None:
        if self._rewrite:
            self.parts.append(f"<?{data}>")

    def unknown_decl(self, data: str) -> None:
        if self._rewrite:
            self.parts.append(f"<![{data}]>")


def sanitize_raw_html_attrs(html: str) -> str:
    """Drop blocked/confused URL-bearing attributes; leave public tags untouched."""
    if not html:
        return html
    html = _normalize_abrupt_comments(html)
    parser = _HtmlAttrParser(rewrite=True)
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 — fail closed: escape nothing; return original
        return html
    return "".join(parser.parts)


def find_bad_html_element_attrs(html: str) -> list[str]:
    """Problems for blocked URL-bearing attrs on real HTML elements (not text)."""
    if not html:
        return []
    html = _normalize_abrupt_comments(html)
    parser = _HtmlAttrParser(rewrite=False)
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001
        return []
    return parser.problems


def find_bad_markdown_raw_html_attrs(md_text: str) -> list[str]:
    """Scan only ``html_inline`` / ``html_block`` tokens — never code or prose."""
    if not md_text:
        return []
    md = MarkdownIt("commonmark", {"linkify": False})
    md.validateLink = lambda _url: True  # type: ignore[assignment]
    tokens = md.parse(md_text)
    problems: list[str] = []

    def walk(children: list[Token] | None) -> None:
        if not children:
            return
        for tok in children:
            if tok.type == "html_inline" and tok.content:
                problems.extend(find_bad_html_element_attrs(tok.content))
            if tok.children:
                walk(tok.children)

    for tok in tokens:
        if tok.type == "html_block" and tok.content:
            problems.extend(find_bad_html_element_attrs(tok.content))
        elif tok.type == "inline" and tok.children:
            walk(tok.children)
    return problems


def find_bad_html_attr_urls(text: str, *, fmt: str | None = None) -> list[str]:
    """Gate helper: real HTML element attrs only (markdown tokens or HTML parse)."""
    if fmt == "html":
        return find_bad_html_element_attrs(text)
    return find_bad_markdown_raw_html_attrs(text)
