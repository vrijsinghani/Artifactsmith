"""Markdown export: keep sanitizer-allowed links as ``[text](url)``.

HTML anchors that the shared link policy already allows become Markdown links.
Blocked or private anchors stay plain text (or inert code when the label is
itself a blocked URL) so a later linkify pass cannot restore them.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from markdown_it import MarkdownIt
from markdown_it.token import Token

from .links import classify_href, public_href_or_none
from .md_sanitize import _md_inline_code
from .md_serialize import escape_md_text

_HREF_QUOTED_RE = re.compile(
    r"""href\s*=\s*(?P<q>["'])(?P<val>.*?)(?P=q)""",
    re.I | re.S,
)
_HREF_BARE_RE = re.compile(r"""href\s*=\s*(?P<val>[^\s"'=<>`]+)""", re.I)
_ANCHOR_CLOSE_RE = re.compile(r"</a\s*>", re.I)


def _parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"linkify": False})
    md.validateLink = lambda _url: True  # type: ignore[assignment]
    return md


def _is_anchor_open(raw: str) -> bool:
    s = raw.lstrip()
    if len(s) < 3 or not s.lower().startswith("<a"):
        return False
    return s[2] in " \t\n\r/>"


def _is_anchor_close(raw: str) -> bool:
    return raw.lstrip().lower().startswith("</a")


def _scan_href(start_tag: str) -> str | None:
    quoted = _HREF_QUOTED_RE.search(start_tag)
    if quoted:
        return quoted.group("val")
    bare = _HREF_BARE_RE.search(start_tag)
    if bare:
        return bare.group("val")
    return None


def _decoded_href(start_tag: str) -> str | None:
    found: list[str | None] = []

    class _Probe(HTMLParser):
        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag.lower() != "a":
                return
            for key, val in attrs:
                if key.lower() == "href":
                    found.append(val)

    probe = _Probe(convert_charrefs=False)
    try:
        probe.feed(start_tag)
        probe.close()
    except Exception:  # noqa: BLE001 — fail closed: no href
        return None
    return found[0] if found else None


def _allowed_href(start_tag: str) -> str | None:
    """Emit the raw attribute when both the raw and decoded views are public."""
    raw = _scan_href(start_tag)
    decoded = _decoded_href(start_tag)
    if raw is None or decoded is None:
        return None
    safe_raw = public_href_or_none(raw)
    safe_decoded = public_href_or_none(decoded)
    if safe_raw is None or safe_decoded is None:
        return None
    return safe_raw


def _format_link(label: str, href: str) -> str:
    shown = escape_md_text(label).strip() or escape_md_text(href)
    dest = f"<{href}>" if re.search(r"[\s()]", href) else href
    return f"[{shown}]({dest})"


def _inert_label(label: str) -> str:
    raw = label.strip()
    if raw and classify_href(raw) == "blocked" and re.search(r"[a-z][a-z0-9+.-]*:", raw, re.I):
        return _md_inline_code(raw)
    return escape_md_text(label)


def _code_ranges(src: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    i = 0
    n = len(src)
    while i < n:
        if src[i] != "`":
            i += 1
            continue
        j = i
        while j < n and src[j] == "`":
            j += 1
        ticks = src[i:j]
        end = src.find(ticks, j)
        if end < 0:
            break
        ranges.append((i, end + len(ticks)))
        i = end + len(ticks)
    return ranges


def _find_outside_code(src: str, span: str, start: int, code: list[tuple[int, int]]) -> int:
    idx = start
    while True:
        idx = src.find(span, idx)
        if idx < 0:
            return -1
        if any(a <= idx < b for a, b in code):
            idx += 1
            continue
        return idx


def _label_from(tokens: list[Token], *, escape_html: bool = False) -> str:
    parts: list[str] = []
    for tok in tokens:
        content = tok.content or ""
        if tok.type == "text":
            parts.append(content)
        elif tok.type == "html_inline":
            parts.append(escape_md_text(content) if escape_html else content)
        elif tok.type == "softbreak":
            parts.append("\n")
        elif tok.type == "code_inline":
            parts.append(content)
    return "".join(parts)


def _inert_from_tokens(tokens: list[Token]) -> str:
    """Blocked-anchor label. Raw ``html_inline`` fragments are escaped once."""
    raw = _label_from(tokens)
    stripped = raw.strip()
    if stripped and classify_href(stripped) == "blocked" and re.search(r"[a-z][a-z0-9+.-]*:", stripped, re.I):
        safe = _label_from(tokens, escape_html=True).strip() or stripped
        return _md_inline_code(safe)
    parts: list[str] = []
    for tok in tokens:
        content = tok.content or ""
        if tok.type == "html_inline":
            parts.append(escape_md_text(content))
        elif tok.type in ("text", "code_inline"):
            parts.append(escape_md_text(content))
        elif tok.type == "softbreak":
            parts.append("\n")
    return "".join(parts)


def _rewrite_inline(src: str, children: list[Token]) -> str:
    code = _code_ranges(src)
    replacements: list[tuple[int, int, str]] = []
    search_from = 0
    i = 0
    while i < len(children):
        tok = children[i]
        if tok.type != "html_inline" or not _is_anchor_open(tok.content or ""):
            i += 1
            continue
        open_html = tok.content or ""
        inner: list[Token] = []
        j = i + 1
        closed = False
        while j < len(children):
            ch = children[j]
            if ch.type == "html_inline" and _is_anchor_close(ch.content or ""):
                closed = True
                j += 1
                break
            if ch.type == "html_inline" and _is_anchor_open(ch.content or ""):
                break  # nested or unclosed opener: stop here so the scan stays linear
            if ch.type not in ("text", "html_inline", "code_inline", "softbreak"):
                break
            inner.append(ch)
            j += 1
        if not closed:
            i = max(j, i + 1)
            continue
        idx = _find_outside_code(src, open_html, search_from, code)
        if idx < 0:
            i = j
            continue
        close = _ANCHOR_CLOSE_RE.search(src, idx + len(open_html))
        if close is None:
            i = j
            continue
        href = _allowed_href(open_html)
        label = _label_from(inner)
        repl = _format_link(label, href) if href else _inert_from_tokens(inner)
        replacements.append((idx, close.end(), repl))
        search_from = close.end()
        i = j
    result = src
    for lo, hi, repl in reversed(replacements):
        result = result[:lo] + repl + result[hi:]
    return result


class _BlockToMarkdown(HTMLParser):
    """Turn a raw HTML block into text, keeping only allowed anchors as links."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0
        self._anchor: list[str] | None = None
        self._href: str | None = None
        self.changed = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        if name in ("script", "style"):
            self._skip += 1
            return
        if self._skip or name != "a" or self._anchor is not None:
            return
        raw = self.get_starttag_text() or ""
        self._anchor = []
        self._href = _allowed_href(raw)
        self.changed = True

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in ("script", "style") and self._skip:
            self._skip -= 1
            return
        if self._skip or name != "a" or self._anchor is None:
            return
        label = "".join(self._anchor)
        href = self._href
        self.out.append(_format_link(label, href) if href else _inert_label(label))
        self._anchor = None
        self._href = None

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._anchor is not None:
            self._anchor.append(data)
        else:
            self.out.append(data)


def _rewrite_html_block(html: str) -> str:
    if "<a" not in html.lower():
        return html
    parser = _BlockToMarkdown()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 — leave the block unchanged rather than drop text
        return html
    if parser._anchor is not None:  # anchor left open (block ended at a blank line): keep the block as is
        return html
    if not parser.changed:
        return html
    trailing = "\n" if html.endswith("\n") else ""
    body = "".join(parser.out).strip()
    return (body + trailing) if body else html


def _line_bounds(text: str) -> list[tuple[int, int]]:
    bounds: list[tuple[int, int]] = []
    start = 0
    for i, ch in enumerate(text):
        if ch == "\n":
            bounds.append((start, i + 1))
            start = i + 1
    if start < len(text) or not bounds:
        bounds.append((start, len(text)))
    return bounds


def export_markdown(text: str) -> str:
    """Rewrite allowed HTML anchors to Markdown links. Other text stays put."""
    if not text or "<a" not in text.lower():
        return text
    md = _parser()
    tokens = md.parse(text)
    bounds = _line_bounds(text)
    edits: list[tuple[int, int, str]] = []
    for tok in tokens:
        if not tok.map:
            continue
        start_line, end_line = tok.map
        if start_line >= len(bounds) or end_line <= 0:
            continue
        lo = bounds[start_line][0]
        chunk = text[lo : bounds[min(end_line, len(bounds)) - 1][1]]
        source = tok.content or ""
        if tok.type == "inline" and tok.children and "<a" in source.lower():
            new_inline = _rewrite_inline(source, tok.children)
            if new_inline != source and source in chunk:
                at = chunk.find(source)
                edits.append((lo + at, lo + at + len(source), new_inline))
            continue
        if tok.type == "html_block":
            new_block = _rewrite_html_block(source)
            if new_block != source and source in chunk:
                at = chunk.find(source)
                edits.append((lo + at, lo + at + len(source), new_block))
    if not edits:
        return text
    out = text
    for lo, hi, repl in sorted(edits, key=lambda item: item[0], reverse=True):
        out = out[:lo] + repl + out[hi:]
    return out
