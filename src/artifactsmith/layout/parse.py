"""Parse Markdown + ::: blocks with markdown-it-py. Unknown blocks stay as content."""

from __future__ import annotations

import re

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.attrs import attrs_block_plugin, attrs_plugin
from mdit_py_plugins.attrs.parse import ParseError
from mdit_py_plugins.attrs.parse import parse as parse_attrs
from mdit_py_plugins.container import container_plugin
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.tasklists import tasklists_plugin

from .chart import parse_chart
from .inline import cell_html, plain_text, render_inlines
from .model import ALLOWED_ATTRS, CALLOUTS, Block, Cell, classify
from .tables import limit_table

_TRAIL = re.compile(r"\s*\{([^{}]*)\}\s*$")
_ONLY_ATTRS = re.compile(r"\s*\{.*\}\s*$", re.S)
_LANG = re.compile(r"^[A-Za-z0-9_+-]{1,20}$")
_BLOCK_NAME = re.compile(r"^[a-z0-9-]{1,24}$")
_HTML_CAP = 6 * 1024
_HTML_MAX = 2
_NOTE_ALIAS = {"callout": "note", "tip": "note", "info": "note"}


def _parser(source: str) -> MarkdownIt:
    """Enable the extra plugins only when the source uses them.

    A long run of ``[`` with no attributes, fences, or containers stays on the
    plain CommonMark parser, which does not rescan those brackets.
    """
    md = MarkdownIt("commonmark", {"html": False})
    md.enable("table")
    if ":::" in source:
        md.use(container_plugin, "block", validate=lambda params, _markup: bool(params.strip()))
    if "{" in source:
        allowed = sorted(ALLOWED_ATTRS)
        md.use(attrs_plugin, spans=True, allowed=allowed)
        md.use(attrs_block_plugin, allowed=allowed)
    if source.startswith(":") or "\n:" in source:
        md.use(deflist_plugin)
    if "[ ]" in source or "[x]" in source or "[X]" in source:
        md.use(tasklists_plugin)
    return md


def take_braces(text: str) -> tuple[str, dict[str, str]]:
    """Split a trailing ``{...}`` attribute group off ``text``. Disallowed keys are kept for filtering."""
    match = _TRAIL.search(text)
    if not match:
        return text, {}
    blob = "{" + match.group(1) + "}"
    try:
        _used, raw = parse_attrs(blob)
    except ParseError:
        return text[: match.start()].rstrip(), {}
    attrs = {str(key): str(value) for key, value in raw.items()}
    return text[: match.start()].rstrip(), attrs


def parse_groups(raw: str) -> tuple[tuple[str, int], ...]:
    groups: list[tuple[str, int]] = []
    for part in raw.split("|"):
        label, sep, count = part.rpartition(":")
        if not sep or not count.isdigit():
            continue
        span = int(count)
        if 1 <= span <= 24:
            groups.append((label.strip(), span))
    return tuple(groups)


class _Walker:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.i = 0
        self.notes: list[str] = []
        self.html_blocks = 0

    def blocks(self) -> list[Block]:
        found: list[Block] = []
        while self.i < len(self.tokens):
            block = self.one()
            if block is not None:
                found.append(block)
        return found

    def _tok(self) -> Token | None:
        if self.i >= len(self.tokens):
            return None
        return self.tokens[self.i]

    def one(self) -> Block | None:
        tok = self._tok()
        if tok is None:
            return None
        kind = tok.type
        if kind == "heading_open":
            return self._heading()
        if kind == "paragraph_open":
            return self._paragraph()
        if kind in ("bullet_list_open", "ordered_list_open"):
            return self._list()
        if kind == "table_open":
            return self._table()
        if kind == "container_block_open":
            return self._container()
        if kind == "dl_open":
            return self._deflist()
        if kind == "blockquote_open":
            return self._quote()
        if kind in ("fence", "code_block"):
            return self._fence()
        if kind == "hr":
            self.i += 1
            return Block(kind="hr")
        self.i += 1
        return None

    def _inline(self) -> Token | None:
        tok = self._tok()
        if tok is not None and tok.type == "inline":
            self.i += 1
            return tok
        return None

    def _close(self, name: str) -> None:
        tok = self._tok()
        if tok is not None and tok.type == name:
            self.i += 1

    def _heading(self) -> Block:
        tok = self.tokens[self.i]
        self.i += 1
        inline = self._inline()
        self._close("heading_close")
        content = inline.content if inline else ""
        _stripped, extra = take_braces(content)
        attrs, classes = classify({**(tok.attrs or {}), **extra})
        children = list(inline.children or []) if inline else []
        html = render_inlines(children)
        plain = plain_text(children)
        if extra:
            html = _TRAIL.sub("", html).rstrip()
            plain = _TRAIL.sub("", plain).rstrip()
        level = int(tok.tag[1]) if len(tok.tag) > 1 and tok.tag[1:].isdigit() else 2
        return Block(kind="heading", html=html, plain=plain.strip(), level=min(level, 6), attrs=attrs, classes=classes)

    def _paragraph(self) -> Block | None:
        self.i += 1
        inline = self._inline()
        self._close("paragraph_close")
        content = inline.content if inline else ""
        if _ONLY_ATTRS.fullmatch(content or ""):
            return None
        children = list(inline.children or []) if inline else []
        return Block(kind="paragraph", html=render_inlines(children), plain=plain_text(children).strip())

    def _list(self) -> Block:
        tok = self.tokens[self.i]
        ordered = tok.type == "ordered_list_open"
        self.i += 1
        close = "ordered_list_close" if ordered else "bullet_list_close"
        items: list[Block] = []
        while self.i < len(self.tokens) and self.tokens[self.i].type != close:
            if self.tokens[self.i].type == "list_item_open":
                items.append(self._item())
            else:
                self.i += 1
        self._close(close)
        attrs, classes = classify(tok.attrs)
        return Block(kind="olist" if ordered else "list", attrs=attrs, classes=classes, children=items)

    def _item(self) -> Block:
        tok = self.tokens[self.i]
        raw_class = str((tok.attrs or {}).get("class") or "")
        task = "task-list-item" in raw_class.split()
        self.i += 1
        children: list[Block] = []
        checked = False
        while self.i < len(self.tokens) and self.tokens[self.i].type != "list_item_close":
            if task:
                checked = checked or "checked" in (self.tokens[self.i].content or "")
            child = self.one()
            if child is not None:
                children.append(child)
        self._close("list_item_close")
        if task and children:
            mark = "Done" if checked else "Not done"
            children[0].html = f'<span class="asl-check">{mark}</span> ' + children[0].html
        html = "".join(child.html for child in children if child.kind == "paragraph") or ""
        plain = " ".join(child.plain for child in children).strip()
        return Block(kind="item", html=html, plain=plain, children=children)

    def _table(self) -> Block:
        tok = self.tokens[self.i]
        attrs, classes = classify(tok.attrs)
        self.i += 1
        headers: list[Cell] = []
        rows: list[list[Cell]] = []
        while self.i < len(self.tokens) and self.tokens[self.i].type != "table_close":
            name = self.tokens[self.i].type
            if name in ("thead_open", "tbody_open"):
                bucket = "head" if name == "thead_open" else "body"
                self.i += 1
                close = "thead_close" if bucket == "head" else "tbody_close"
                while self.i < len(self.tokens) and self.tokens[self.i].type != close:
                    if self.tokens[self.i].type == "tr_open":
                        row = self._row()
                        if bucket == "head" and not headers:
                            headers = row
                        else:
                            rows.append(row)
                    else:
                        self.i += 1
                self._close(close)
            elif name == "tr_open":
                rows.append(self._row())
            else:
                self.i += 1
        self._close("table_close")
        groups = parse_groups(attrs.get("groups", ""))
        headers, rows, clipped = limit_table(headers, rows)
        if not rows:
            self.notes.append("table dropped")
            return Block(kind="skipped")
        if clipped:
            self.notes.append("table clipped")
        return Block(kind="table", attrs=attrs, classes=classes, headers=headers, rows=rows, groups=groups)

    def _row(self) -> list[Cell]:
        self.i += 1
        cells: list[Cell] = []
        while self.i < len(self.tokens) and self.tokens[self.i].type != "tr_close":
            if self.tokens[self.i].type in ("td_open", "th_open"):
                cells.append(self._cell())
            else:
                self.i += 1
        self._close("tr_close")
        return cells

    def _cell(self) -> Cell:
        self.i += 1
        inline = self._inline()
        self._close("td_close")
        self._close("th_close")
        if inline is None:
            return Cell("", "")
        html, plain, span = cell_html(list(inline.children or []), inline.content or "")
        return Cell(html, plain, span)

    def _container(self) -> Block:
        info = self.tokens[self.i].info.strip()
        self.i += 1
        children: list[Block] = []
        while self.i < len(self.tokens) and self.tokens[self.i].type != "container_block_close":
            child = self.one()
            if child is not None and child.kind != "skipped":
                children.append(child)
        self._close("container_block_close")
        info, extra = take_braces(info)
        attrs, classes = classify(extra)
        name, _, rest = info.partition(" ")
        name = _NOTE_ALIAS.get(name.lower(), name.lower())
        if name in CALLOUTS:
            return Block(kind="callout", plain=name, attrs=attrs, classes=classes, children=children)
        if name == "grid":
            return Block(kind="grid", plain=rest.strip(), attrs=attrs, classes=classes, children=children)
        if name == "stat":
            return Block(kind="stat", attrs=attrs, classes=classes, children=children)
        if name in {"timeline", "steps", "compare", "list", "quote"}:
            return Block(kind=name, attrs=attrs, classes=classes, children=children)
        if name == "html":
            return self._raw_html("\n".join(child.plain for child in children))
        shown = name if _BLOCK_NAME.fullmatch(name or "") else "block"
        self.notes.append(f"unknown block {shown}")
        return Block(kind="flow", children=children)

    def _quote(self) -> Block:
        self.i += 1
        children: list[Block] = []
        while self.i < len(self.tokens) and self.tokens[self.i].type != "blockquote_close":
            child = self.one()
            if child is not None:
                children.append(child)
        self._close("blockquote_close")
        return Block(kind="quote", children=children)

    def _deflist(self) -> Block:
        self.i += 1
        pairs: list[Block] = []
        while self.i < len(self.tokens) and self.tokens[self.i].type != "dl_close":
            if self.tokens[self.i].type != "dt_open":
                self.i += 1
                continue
            self.i += 1
            inline = self._inline()
            self._close("dt_close")
            body: list[Block] = []
            if self._tok() is not None and self.tokens[self.i].type == "dd_open":
                self.i += 1
                while self.i < len(self.tokens) and self.tokens[self.i].type != "dd_close":
                    child = self.one()
                    if child is not None:
                        body.append(child)
                self._close("dd_close")
            term = render_inlines(list(inline.children or [])) if inline else ""
            plain = plain_text(list(inline.children or [])).strip() if inline else ""
            pairs.append(Block(kind="pair", html=term, plain=plain, children=body))
        self._close("dl_close")
        return Block(kind="kv", children=pairs)

    def _fence(self) -> Block | None:
        tok = self.tokens[self.i]
        self.i += 1
        info = (tok.info or "").strip()
        if info.lower().startswith("chart"):
            parsed = parse_chart(info, tok.content or "")
            if parsed is None:
                self.notes.append("chart rendered as a table")
                return _chart_as_table(tok.content or "")
            kind, unit, labels, series = parsed
            rows = [
                [Cell(label, label)] + [Cell(str(value), str(value)) for value in values]
                for label, *values in _zip_series(labels, series)
            ]
            names = [Cell(name or "Value", name or "Value") for name, _vals in series]
            return Block(
                kind="chart",
                plain=kind,
                html=unit,
                headers=names,
                rows=rows,
                labels=tuple(labels),
                numbers=tuple(series[0][1]),
            )
        lang = info.split()[0] if info else ""
        attrs = {"lang": lang} if _LANG.fullmatch(lang) else {}
        return Block(kind="code", html=tok.content or "", attrs=attrs)

    def _raw_html(self, source: str) -> Block:
        if self.html_blocks >= _HTML_MAX or len(source.encode("utf-8")) > _HTML_CAP:
            self.notes.append("html block dropped")
            return Block(kind="skipped")
        self.html_blocks += 1
        return Block(kind="html", html=source)


def _zip_series(labels: list[str], series: list[tuple[str, list[float]]]) -> list[list[str]]:
    rows: list[list[str]] = []
    for index, label in enumerate(labels):
        row = [label]
        for _name, values in series:
            row.append(str(values[index]) if index < len(values) else "")
        rows.append(row)
    return rows


def _chart_as_table(content: str) -> Block:
    rows: list[list[Cell]] = []
    for line in content.splitlines():
        raw = line.strip()
        if not raw:
            continue
        if raw.startswith("|"):
            cells = [Cell(part.strip(), part.strip()) for part in raw.strip("|").split("|")]
        elif ":" in raw:
            label, _, val = raw.lstrip("-").strip().partition(":")
            cells = [Cell(label.strip(), label.strip()), Cell(val.strip(), val.strip())]
        else:
            cells = [Cell(raw, raw)]
        rows.append(cells)
    if not rows:
        return Block(kind="skipped")
    return Block(kind="table", headers=rows[0], rows=rows[1:] or rows)


def _tame_brackets(source: str) -> str:
    """Escape long ``[`` runs so Markdown's link scanner stays linear.

    A run of brackets with no ``]`` nearby is not a link or a badge. The parser
    otherwise retries the tail at every bracket.
    """
    if source.count("[") < 32:
        return source
    lines = source.split("\n")
    fenced = False
    tamed: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fenced = not fenced
            tamed.append(line)
            continue
        if fenced or "[" not in line:
            tamed.append(line)
            continue
        tamed.append(_tame_line(line))
    return "\n".join(tamed)


def _tame_line(line: str) -> str:
    parts: list[str] = []
    i = 0
    n = len(line)
    while i < n:
        if line[i] == "`":
            ticks = i
            while ticks < n and line[ticks] == "`":
                ticks += 1
            mark = line[i:ticks]
            end = line.find(mark, ticks)
            if end == -1:
                parts.append(line[i:])
                break
            parts.append(line[i : end + len(mark)])
            i = end + len(mark)
            continue
        if line[i] != "[":
            parts.append(line[i])
            i += 1
            continue
        j = i + 1
        while j < n and line[j] == "[":
            j += 1
        run = j - i
        closer = line.find("]", j, min(n, j + 80))
        if run >= 32 or (run >= 8 and closer == -1):
            parts.append("\\[" * run)
        else:
            parts.append(line[i:j])
        i = j
    return "".join(parts)


def parse_markdown(source: str) -> tuple[list[Block], list[str]]:
    """Parse ``source``. Raises nothing for soft problems; those are notes."""
    source = _tame_brackets(source or "")
    tokens = _parser(source).parse(source)
    walker = _Walker(list(tokens))
    blocks = [block for block in walker.blocks() if block.kind != "skipped"]
    return blocks, walker.notes
