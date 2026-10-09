"""Minimal markdown → structure helpers shared by PDF, DOCX and XLSX renderers.

No network, no plugins. Supports headings, paragraphs, lists, fenced code, and pipe tables.
Public http(s) links become clickable anchors in the PDF HTML path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .links import iter_inline_segments


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


def _inline_html(text: str) -> str:
    """Escape text and turn public http(s) segments into <a href> (PDF annotations)."""
    parts: list[str] = []
    for display, href in iter_inline_segments(text):
        if href is None:
            parts.append(_esc_text(display))
        else:
            parts.append(f'<a href="{_esc_href(href)}">{_esc_text(display)}</a>')
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
        "h1,h2,h3{color:#0b3d91} pre{background:#f4f4f4;padding:0.75rem;overflow:auto}",
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
