"""Minimal markdown → structure helpers shared by PDF, DOCX and XLSX renderers.

No network, no plugins. Supports headings, paragraphs, lists, fenced code, and pipe tables.
Public http(s) links become clickable anchors in the PDF HTML path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from markdown_it.token import Token

from .links import public_href_or_none
from .safety import URL_RE


@dataclass
class Block:
    kind: str  # heading | paragraph | list | code | table
    text: str = ""
    level: int = 0
    items: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)


_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_UL = re.compile(r"^[-*+]\s+(.*)$")
_OL = re.compile(r"^\d+[.)]\s+(.*)$")
_FENCE = re.compile(r"^```")
_TABLE_ROW = re.compile(r"^\|(.+)\|$")
_TABLE_SEP = re.compile(r"^\|[\s\-:|]+\|$")


def _split_row(line: str) -> list[str]:
    inner = line.strip().strip("|")
    return [c.strip() for c in inner.split("|")]


def parse_blocks(text: str) -> list[Block]:
    lines = text.replace("\r\n", "\n").split("\n")
    blocks: list[Block] = []
    i = 0
    para: list[str] = []

    def flush_para() -> None:
        nonlocal para
        if para:
            blocks.append(Block(kind="paragraph", text=" ".join(para).strip()))
            para = []

    while i < len(lines):
        line = lines[i]
        if _FENCE.match(line.strip()):
            flush_para()
            i += 1
            buf: list[str] = []
            while i < len(lines) and not _FENCE.match(lines[i].strip()):
                buf.append(lines[i])
                i += 1
            i += 1  # closing fence
            blocks.append(Block(kind="code", text="\n".join(buf)))
            continue
        if _TABLE_ROW.match(line.strip()) and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1].strip()):
            flush_para()
            headers = _split_row(line)
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and _TABLE_ROW.match(lines[i].strip()):
                rows.append(_split_row(lines[i]))
                i += 1
            blocks.append(Block(kind="table", headers=headers, rows=rows))
            continue
        m = _HEADING.match(line)
        if m:
            flush_para()
            blocks.append(Block(kind="heading", level=len(m.group(1)), text=m.group(2).strip()))
            i += 1
            continue
        um = _UL.match(line)
        om = _OL.match(line)
        if um or om:
            flush_para()
            items: list[str] = []
            while i < len(lines):
                um2 = _UL.match(lines[i])
                om2 = _OL.match(lines[i])
                if not (um2 or om2):
                    break
                items.append((um2 or om2).group(1).strip())  # type: ignore[union-attr]
                i += 1
            blocks.append(Block(kind="list", items=items))
            continue
        if not line.strip():
            flush_para()
            i += 1
            continue
        para.append(line.strip())
        i += 1
    flush_para()
    return blocks


def _esc_text(s: str) -> str:
    from html import escape, unescape

    # Unescape first so labels are not double-escaped when entities were already present.
    return escape(unescape(s), quote=True)


def _esc_href(s: str) -> str:
    """Escape a URL for an HTML attribute. Never entity-decode (``&section=`` stays)."""
    from html import escape

    return escape(s, quote=True)


@dataclass(frozen=True)
class InlineSpan:
    """One inline run after markdown emphasis, code, and public-link parse."""

    text: str
    href: str | None = None
    bold: bool = False
    italic: bool = False
    code: bool = False


def _attr_str(tok: Token, name: str) -> str:
    val = tok.attrGet(name)
    return "" if val is None else str(val)


def _label_text(tokens: list[Token]) -> str:
    parts: list[str] = []
    for t in tokens:
        if t.type in ("text", "code_inline"):
            parts.append(t.content or "")
        elif t.children:
            parts.append(_label_text(t.children))
    return "".join(parts)


def _emit_text_urls(
    raw: str,
    *,
    bold: bool,
    italic: bool,
    href: str | None,
    parts: list[InlineSpan],
) -> None:
    if not raw:
        return
    if href:
        parts.append(InlineSpan(raw, href=href, bold=bold, italic=italic))
        return
    pos = 0
    for m in URL_RE.finditer(raw):
        if m.start() > pos:
            parts.append(InlineSpan(raw[pos : m.start()], bold=bold, italic=italic))
        core = m.group(0).rstrip(".,;:)")
        trailing = m.group(0)[len(core) :]
        public = public_href_or_none(core)
        if public:
            parts.append(InlineSpan(core, href=public, bold=bold, italic=italic))
            if trailing:
                parts.append(InlineSpan(trailing, bold=bold, italic=italic))
        else:
            parts.append(InlineSpan(m.group(0), bold=bold, italic=italic))
        pos = m.end()
    if pos < len(raw):
        parts.append(InlineSpan(raw[pos:], bold=bold, italic=italic))


def iter_styled_spans(text: str) -> list[InlineSpan]:
    """Flatten inline markdown to styled spans for PDF/DOCX.

    Emphasis markers are not emitted. URLs inside inline code stay plain text.
    """
    if not text:
        return []
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark", {"linkify": True})
    md.enable("linkify")
    md.validateLink = lambda _url: True  # type: ignore[assignment]
    tokens = md.parse(text)
    parts: list[InlineSpan] = []

    def walk(children: list[Token] | None, bold: bool, italic: bool, href: str | None) -> None:
        if not children:
            return
        i = 0
        while i < len(children):
            tok = children[i]
            kind = tok.type
            if kind == "strong_open":
                bold = True
            elif kind == "strong_close":
                bold = False
            elif kind == "em_open":
                italic = True
            elif kind == "em_close":
                italic = False
            elif kind == "code_inline":
                if tok.content:
                    parts.append(InlineSpan(tok.content, bold=bold, italic=italic, code=True))
            elif kind == "text":
                _emit_text_urls(tok.content or "", bold=bold, italic=italic, href=href, parts=parts)
            elif kind in ("softbreak", "hardbreak"):
                parts.append(InlineSpan("\n", href=href, bold=bold, italic=italic))
            elif kind == "link_open":
                dest = public_href_or_none(_attr_str(tok, "href"))
                inner: list[Token] = []
                i += 1
                while i < len(children) and children[i].type != "link_close":
                    inner.append(children[i])
                    i += 1
                walk(inner, bold, italic, dest)
            elif kind == "image":
                label = (_label_text(tok.children or []) or "image").strip() or "image"
                dest = public_href_or_none(_attr_str(tok, "src"))
                parts.append(InlineSpan(label, href=dest, bold=bold, italic=italic))
            elif tok.children:
                walk(tok.children, bold, italic, href)
            i += 1

    for tok in tokens:
        if tok.type == "inline":
            walk(tok.children, False, False, None)
        elif tok.type in ("fence", "code_block") and tok.content:
            parts.append(InlineSpan(tok.content, code=True))
    return parts


def visible_text(text: str) -> str:
    """Plain text of inline markdown (no emphasis markers)."""
    return "".join(span.text for span in iter_styled_spans(text)) or text


def _style_html(inner: str, span: InlineSpan) -> str:
    if span.code:
        inner = f"<code>{inner}</code>"
    if span.italic:
        inner = f"<em>{inner}</em>"
    if span.bold:
        inner = f"<strong>{inner}</strong>"
    return inner


def _inline_html(text: str) -> str:
    """Escape text; render emphasis/code; turn public http(s) into <a href>."""
    parts: list[str] = []
    for span in iter_styled_spans(text):
        inner = _style_html(_esc_text(span.text), span)
        if span.href:
            inner = f'<a href="{_esc_href(span.href)}">{inner}</a>'
        parts.append(inner)
    return "".join(parts)


def blocks_to_simple_html(title: str, blocks: list[Block]) -> str:
    """Self-contained HTML for PDF (WeasyPrint). No scripts, no remote resources.

    Public http(s) links become ``<a href>`` so WeasyPrint emits link annotations
    without fetching the destination.
    """
    parts = [
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">',
        f"<title>{_esc_text(title)}</title>",
        "<style>",
        "body{font-family:Helvetica,Arial,sans-serif;margin:2rem;max-width:48rem;",
        "line-height:1.5;color:#111;background:#fff}",
        "h1,h2,h3{color:#0b3d91}",
        "pre{background:#f4f4f4;padding:0.75rem;white-space:pre-wrap;",
        "overflow-wrap:anywhere;word-break:break-all}",
        "pre code{white-space:inherit;overflow-wrap:inherit;word-break:inherit}",
        "table{border-collapse:collapse;width:100%} th,td{border:1px solid #ccc;padding:0.4rem 0.6rem}",
        "th{background:#e8eef8;text-align:left}",
        "</style></head><body>",
        f"<h1>{_esc_text(title)}</h1>",
    ]
    for b in blocks:
        if b.kind == "heading":
            lvl = min(max(b.level, 1), 6)
            parts.append(f"<h{lvl}>{_inline_html(b.text)}</h{lvl}>")
        elif b.kind == "paragraph":
            parts.append(f"<p>{_inline_html(b.text)}</p>")
        elif b.kind == "list":
            parts.append("<ul>" + "".join(f"<li>{_inline_html(it)}</li>" for it in b.items) + "</ul>")
        elif b.kind == "code":
            parts.append(f"<pre><code>{_esc_text(b.text)}</code></pre>")
        elif b.kind == "table":
            parts.append("<table><thead><tr>")
            parts.extend(f"<th>{_inline_html(h)}</th>" for h in b.headers)
            parts.append("</tr></thead><tbody>")
            for row in b.rows:
                parts.append("<tr>" + "".join(f"<td>{_inline_html(c)}</td>" for c in row) + "</tr>")
            parts.append("</tbody></table>")
    parts.append("</body></html>")
    return "".join(parts)
