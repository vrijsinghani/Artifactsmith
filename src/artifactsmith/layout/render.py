"""Render a Markdown layout tree to one HTML document in a named style."""

from __future__ import annotations

import re
from html import escape

from ..renderers.html_sanitize import HtmlSanitizeError, sanitize_html_document
from ..styles import parse_style
from .chart import aria_label, chart_svg
from .css import grid_class, grid_rule, stylesheet
from .design import design_page
from .inline import esc
from .model import Block, LayoutError, class_attr
from .parse import parse_markdown
from .tables import auto_bar_chart, render_table

_HTML_DOC = re.compile(r"<!doctype\s+html|<html\b", re.I)
_DEK_LIMIT = 100
_LABEL_VALUE = re.compile(r"^([^:]{1,32}):\s+(\S.{0,47})$")


class _Ctx:
    def __init__(self, style: str) -> None:
        self.style = style
        self.notes: list[str] = []
        self.grids: list[str] = []
        self.heading = ""
        self.section_index = 0


def render_layout(source: str, *, style: str, title: str) -> tuple[str, list[str]]:
    """Return a complete HTML document and soft notes. Raises ``LayoutError`` when unusable."""
    chosen = parse_style(style)
    blocks, notes = parse_markdown(source or "")
    extra = _one_emph(blocks)
    if extra:
        notes = [*notes, extra]
    if not blocks:
        raise LayoutError("empty layout")
    title_block, dek, rest = _split_title(blocks)
    lead, sections = _sections(rest)
    lead = _design_lead(lead)
    lead, sections = design_page(chosen, lead, sections)
    if not _contains_chart(lead) and not any(_contains_chart(section) for section in sections):
        _offer_charts(lead)
        for section in sections:
            _offer_charts(section)
    ctx = _Ctx(chosen)
    main = "".join(_render(block, ctx) for block in lead)
    main += "".join(_section(section, ctx) for section in sections)
    page_title = (title_block.plain if title_block else "") or title or "Page"
    css = stylesheet(chosen, extra="".join(dict.fromkeys(ctx.grids)))
    doc = _document(page_title, css, _title_html(title_block, dek, title), main)
    return doc, [*notes, *ctx.notes]


def _contains_chart(blocks: list[Block]) -> bool:
    return any(block.kind == "chart" or _contains_chart(block.children) for block in blocks)


def _offer_charts(blocks: list[Block]) -> None:
    index = 0
    while index < len(blocks):
        block = blocks[index]
        if block.kind == "table":
            chart = auto_bar_chart(block)
            if chart is not None:
                blocks.insert(index + 1, chart)
                index += 2
                continue
        elif block.children:
            _offer_charts(block.children)
        index += 1


def _one_emph(blocks: list[Block]) -> str:
    state = {"seen": False, "extra": False}

    def walk(nodes: list[Block]) -> None:
        for block in nodes:
            if "emph" in block.classes:
                if state["seen"]:
                    block.classes = tuple(name for name in block.classes if name != "emph")
                    state["extra"] = True
                else:
                    state["seen"] = True
            walk(block.children)

    walk(blocks)
    return "ignored extra emph" if state["extra"] else ""


def _is_dek(block: Block) -> bool:
    if block.kind != "paragraph":
        return False
    if "<strong" in block.html or "<a " in block.html or "<em" in block.html:
        return False
    return 0 < len(block.plain) <= _DEK_LIMIT


def _split_title(blocks: list[Block]) -> tuple[Block | None, Block | None, list[Block]]:
    if blocks and blocks[0].kind == "heading" and blocks[0].level == 1:
        rest = blocks[1:]
        dek = rest[0] if rest and _is_dek(rest[0]) else None
        return blocks[0], dek, rest[1:] if dek else rest
    return None, None, blocks


def _design_lead(lead: list[Block]) -> list[Block]:
    """Turn an opening summary, status-free fact list, or callout into designed blocks.

    Samples that already use ::: blocks keep their notes empty: this only adds
    classes and wrappers, never a note.
    """
    if not lead:
        return lead
    out: list[Block] = []
    promoted = False
    lifted = False
    for block in lead:
        if not promoted and block.kind == "callout":
            if "verdict" not in block.classes:
                block.classes = (*block.classes, "verdict")
            promoted = True
            out.append(block)
            continue
        if not promoted and block.kind == "paragraph" and ("<strong>" in block.html or len(block.plain) > _DEK_LIMIT):
            out.append(Block(kind="hero", html=block.html, plain=block.plain))
            promoted = True
            continue
        if not lifted and block.kind in {"list", "olist"}:
            strip = _stat_strip(block)
            if strip is not None:
                out.append(strip)
                lifted = True
                continue
        out.append(block)
    return out


def _stat_strip(block: Block) -> Block | None:
    if not block.children or block.children[0].kind != "item":
        return None
    stats: list[Block] = []
    for item in block.children:
        if "<strong>" not in item.html:
            return None
        match = _LABEL_VALUE.match(item.plain.strip())
        if match is None:
            return None
        label = match.group(1).strip()
        value = match.group(2).strip()
        if not label or not value:
            return None
        stats.append(Block(kind="stat", plain=f"{label} | {value}"))
    if len(stats) < 2:
        return None
    spec = str(min(len(stats), 4))
    return Block(kind="grid", plain=spec, children=stats)


def _sections(blocks: list[Block]) -> tuple[list[Block], list[list[Block]]]:
    lead: list[Block] = []
    sections: list[list[Block]] = []
    current: list[Block] | None = None
    for block in blocks:
        if block.kind == "heading" and block.level == 2:
            current = [block]
            sections.append(current)
        elif current is None:
            lead.append(block)
        else:
            current.append(block)
    return lead, sections


def _classes(block: Block, *base: str) -> str:
    names = [part for part in base if part]
    tone = block.attrs.get("tone")
    if tone:
        names.append(f"tone-{tone}")
    size = block.attrs.get("size")
    if size:
        names.append(f"size-{size}")
    variant = block.attrs.get("variant")
    if variant:
        names.append(f"variant-{variant}")
    cols = block.attrs.get("cols")
    if cols:
        names.append(f"cols-{cols}")
    names.extend(block.classes)
    return class_attr(*names)


def _document(title: str, css: str, header: str, main: str) -> str:
    safe_title = escape(title, quote=True)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{safe_title}</title>\n<style>\n{css}\n</style>\n</head>\n"
        f"<body>\n{header}\n<main>\n{main}</main>\n</body>\n</html>\n"
    )


def _title_html(title: Block | None, dek: Block | None, fallback: str) -> str:
    if title is None:
        shown = esc(fallback or "Page")
        cls = ' class="asl-title"'
    else:
        shown = title.html
        cls = _classes(title, "asl-title")
    dek_html = f'<p class="dek">{dek.html}</p>' if dek else ""
    return f"<header{cls}>\n<h1>{shown}</h1>{dek_html}\n</header>"


def _section(blocks: list[Block], ctx: _Ctx) -> str:
    head, *rest = blocks
    ctx.heading = head.plain
    ctx.section_index += 1
    body = "".join(_render(block, ctx) for block in rest)
    if ctx.style == "editorial" and _long_section(rest):
        body = f'<div class="cols-2">{body}</div>'
    level = min(max(head.level, 2), 6)
    heading = _numbered(head.html, ctx)
    return f"<section{_classes(head, 'asl-section')}><h{level}>{heading}</h{level}>{body}</section>"


def _numbered(html: str, ctx: _Ctx) -> str:
    number = f"{ctx.section_index:02d}"
    if ctx.style == "editorial":
        return f'<span class="sec">{number}</span>{html}'
    if ctx.style == "swiss":
        return f'<span class="idx">{number}</span>{html}'
    return html


def _long_section(blocks: list[Block]) -> bool:
    """A text section, not a table, that is long enough for two desktop columns."""
    if any(block.kind in {"table", "chart", "grid"} for block in blocks):
        return False
    paragraphs = sum(1 for block in blocks if block.kind == "paragraph")
    items = 0
    for block in blocks:
        if block.kind in {"list", "olist"} and block.children and block.children[0].kind == "item":
            items += len(block.children)
    text = " ".join(block.plain for block in blocks)
    return paragraphs >= 2 or items >= 6 or len(text) >= 500


def _render(block: Block, ctx: _Ctx) -> str:
    html = _render_kind(block, ctx)
    span = block.attrs.get("span")
    if span and block.kind not in {"grid", "heading"}:
        return f'<div class="sp-{span}">{html}</div>'
    return html


def _render_kind(block: Block, ctx: _Ctx) -> str:
    kind = block.kind
    if kind == "hero":
        return _hero(block, ctx)
    if kind == "band":
        inner = "".join(_render(child, ctx) for child in block.children)
        return f'<div class="asl-hero-band">{inner}</div>'
    if kind == "paragraph":
        cls = ' class="drop"' if "drop" in block.classes else ""
        return f"<p{cls}>{block.html}</p>"
    if kind == "heading":
        ctx.heading = block.plain
        level = min(max(block.level, 2), 6)
        return f"<h{level}{_classes(block)}>{block.html}</h{level}>"
    if kind in {"list", "olist"} and _is_item_list(block):
        return _item_list(block, ctx, ordered=kind == "olist")
    if kind == "list":
        return _wrap_list(block, ctx)
    if kind == "table":
        return render_table(block)
    if kind == "callout":
        return _callout(block, ctx)
    if kind == "grid":
        return _grid(block, ctx)
    if kind == "stat":
        return _stat(block)
    if kind == "timeline":
        return _sequence(block, ctx, "asl-timeline")
    if kind == "steps":
        return _sequence(block, ctx, "asl-steps")
    if kind == "compare":
        inner = "".join(_render(child, ctx) for child in block.children)
        return f"<div{_classes(block, 'asl-compare')}>{inner}</div>"
    if kind == "quote":
        inner = "".join(_render(child, ctx) for child in block.children)
        return f"<blockquote{_classes(block, 'asl-quote')}>{inner}</blockquote>"
    if kind == "kv":
        return _kv(block, ctx)
    if kind == "chart":
        return _chart(block, ctx)
    if kind == "code":
        return _code(block, ctx)
    if kind == "html":
        return _raw_html(block.html, ctx)
    if kind == "hr":
        return '<hr class="asl-rule">'
    if kind == "flow":
        return "".join(_render(child, ctx) for child in block.children)
    return ""


def _hero(block: Block, ctx: _Ctx) -> str:
    names = ["asl-hero"]
    if ctx.style == "playful":
        names.append("sticker")
    if ctx.style == "swiss":
        names.append("swiss")
    if ctx.style == "terminal":
        names.append("term")
    inner = block.html
    if ctx.style == "terminal":
        inner = f'<span class="prompt">$</span> {inner}'
    return f'<div class="{" ".join(names)}"><p>{inner}</p></div>'


def _is_item_list(block: Block) -> bool:
    return bool(block.children) and block.children[0].kind == "item"


def _item_list(block: Block, ctx: _Ctx, *, ordered: bool, skin: Block | None = None) -> str:
    tag = "ol" if ordered else "ul"
    shown = block if skin is None else _skinned(block, skin)
    base = "asl-risks" if "risks" in shown.classes else "asl-list"
    items = "".join(f"<li{class_attr(*item.classes)}>{_item_body(item, ctx)}</li>" for item in block.children)
    return f"<{tag}{_classes(shown, base)}>{items}</{tag}>"


def _skinned(block: Block, skin: Block) -> Block:
    attrs = {**block.attrs, **skin.attrs}
    attrs.pop("span", None)
    classes = tuple(dict.fromkeys((*skin.classes, *block.classes)))
    return Block(kind=block.kind, attrs=attrs, classes=classes, children=block.children)


def _item_body(item: Block, ctx: _Ctx) -> str:
    if len(item.children) == 1 and item.children[0].kind == "paragraph":
        return item.children[0].html
    return "".join(_render(child, ctx) for child in item.children) or item.html


def _wrap_list(block: Block, ctx: _Ctx) -> str:
    if len(block.children) == 1 and _is_item_list(block.children[0]):
        child = block.children[0]
        return _item_list(child, ctx, ordered=child.kind == "olist", skin=block)
    inner = "".join(_render(child, ctx) for child in block.children)
    return f"<div{_classes(block, 'asl-list-block')}>{inner}</div>"


def _callout(block: Block, ctx: _Ctx) -> str:
    from .model import CALL_LABELS

    name = block.plain if block.plain in CALL_LABELS else "note"
    label = CALL_LABELS[name]
    inner = "".join(_render(child, ctx) for child in block.children)
    return f'<aside{_classes(block, "asl-callout", name)}><p class="asl-callout-label">{label}</p>{inner}</aside>'


def _grid(block: Block, ctx: _Ctx) -> str:
    spec = block.plain or "1"
    rule = grid_rule(spec)
    if rule:
        ctx.grids.append(rule)
    stats = bool(block.children) and all(child.kind == "stat" for child in block.children)
    inner = "".join(_render(child, ctx) for child in block.children)
    extra = " stats" if stats else ""
    return f'<div class="asl-grid {grid_class(spec)}{extra}">{inner}</div>'


def _stat(block: Block) -> str:
    text = block.children[0].plain if block.children else block.plain
    parts = [part.strip() for part in text.split("|")]
    label = parts[0] if parts else ""
    value = parts[1] if len(parts) > 1 else ""
    note = " | ".join(parts[2:]) if len(parts) > 2 else ""
    long = " long" if len(value) > 14 else ""
    note_html = f'<p class="note">{esc(note)}</p>' if note else ""
    cls = _classes(block, "asl-stat")
    if long:
        cls = cls[:-1] + ' long"' if cls else ' class="long"'
    return f'<div{cls}><p class="label">{esc(label)}</p><p class="value">{esc(value)}</p>{note_html}</div>'


def _sequence(block: Block, ctx: _Ctx, base: str) -> str:
    items = _sequence_items(block)
    rows = []
    for item in items:
        when, _, what = item.plain.partition("|")
        if what.strip():
            body = f'<p class="when">{esc(when.strip())}</p><p class="what">{esc(what.strip())}</p>'
        else:
            body = _item_body(item, ctx) if item.kind == "item" else _render(item, ctx)
        rows.append(f"<li>{body}</li>")
    tag = "ol"
    return f"<{tag}{_classes(block, base)}>{''.join(rows)}</{tag}>"


def _sequence_items(block: Block) -> list[Block]:
    if len(block.children) == 1 and _is_item_list(block.children[0]):
        return list(block.children[0].children)
    return [child for child in block.children if child.kind == "item"] or list(block.children)


def _kv(block: Block, ctx: _Ctx) -> str:
    rows = []
    for pair in block.children:
        value = "".join(_render(child, ctx) for child in pair.children) or esc(pair.plain)
        rows.append(f'<div class="kv"><dt>{pair.html}</dt><dd>{value}</dd></div>')
    return f"<dl{_classes(block, 'asl-kv')}>{''.join(rows)}</dl>"


def _chart(block: Block, ctx: _Ctx) -> str:
    if not block.numbers:
        return ""
    label = aria_label(block.plain, ctx.heading, list(block.labels), list(block.numbers), block.html)
    svg = chart_svg(block.plain, list(block.labels), list(block.numbers), block.html, label)
    return f'<figure class="asl-chart">{svg}</figure>'


def _code(block: Block, ctx: _Ctx) -> str:
    lang = block.attrs.get("lang") or ""
    lang_html = f'<span class="lang">{esc(lang)}</span>' if lang else ""
    if ctx.style == "bold":
        dots = '<span class="dots"><span></span><span></span><span></span></span>'
        return f'<pre class="asl-code panel">{dots}{lang_html}<code>{esc(block.html)}</code></pre>'
    return f'<pre class="asl-code">{lang_html}<code>{esc(block.html)}</code></pre>'


def _raw_html(source: str, ctx: _Ctx) -> str:
    if _HTML_DOC.search(source):
        wrapped = source
    else:
        wrapped = f"<!DOCTYPE html><html><head><title>block</title></head><body>{source}</body></html>"
    try:
        doc = sanitize_html_document(wrapped)
    except HtmlSanitizeError:
        ctx.notes.append("html block dropped")
        return ""
    match = re.search(r"<body[^>]*>(.*)</body>", doc, re.S | re.I)
    return match.group(1).strip() if match else ""
