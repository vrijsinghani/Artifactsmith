"""Phone cards for wide tables in model-written HTML.

Fast mode already stacks some tables. Full HTML does not. This pass adds the
same card treatment to tables of four or more columns, and only inside a
phone-width query, so a desktop table stays a table.
"""

from __future__ import annotations

from html import unescape
from html.parser import HTMLParser

_VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
)
_CARD_CSS = (
    "<style>"
    "@media (max-width:40rem){"
    "table.stack{--line:#ddd6ee;--muted:#3b3550;width:100%;max-width:100%;min-width:0}"
    "table.stack thead{display:none}"
    "table.stack tr{display:block;margin:0 0 .75rem;border:1px solid var(--line);padding:.45rem .65rem}"
    "table.stack td,table.stack th{display:block;border:0;text-align:left;padding:.2rem 0;white-space:normal}"
    "table.stack td::before,table.stack th::before{content:attr(data-label);display:block;"
    "font-size:.75rem;color:var(--muted);font-weight:600}"
    "table.stack tfoot{display:block}"
    "}"
    "</style>"
)


class _Node:
    def __init__(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list[_Node | str] = []

    def attr(self, name: str) -> str:
        for key, value in self.attrs:
            if key.lower() == name:
                return value or ""
        return ""

    def set_attr(self, name: str, value: str) -> None:
        for index, (key, _old) in enumerate(self.attrs):
            if key.lower() == name:
                self.attrs[index] = (key, value)
                return
        self.attrs.append((name, value))


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.root = _Node("document", [])
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, list(attrs))
        self._stack[-1].children.append(node)
        if tag.lower() not in _VOID:
            self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag.lower() == name:
                del self._stack[index:]
                return

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._stack[-1].children.append(_Node(tag, list(attrs)))

    def handle_data(self, data: str) -> None:
        self._stack[-1].children.append(data)

    def handle_comment(self, data: str) -> None:
        self._stack[-1].children.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self._stack[-1].children.append(f"<!{decl}>")

    def handle_entityref(self, name: str) -> None:
        self._stack[-1].children.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self._stack[-1].children.append(f"&#{name};")


def _elements(node: _Node, tag: str) -> list[_Node]:
    found: list[_Node] = []
    for child in node.children:
        if isinstance(child, _Node):
            if child.tag.lower() == tag:
                found.append(child)
            found.extend(_elements(child, tag))
    return found


def _direct(node: _Node, tag: str) -> list[_Node]:
    return [child for child in node.children if isinstance(child, _Node) and child.tag.lower() == tag]


def _plain(node: _Node) -> str:
    parts: list[str] = []
    for child in node.children:
        if isinstance(child, str):
            if child.startswith("<!"):
                continue
            parts.append(child)
        else:
            parts.append(_plain(child))
    return "".join(parts).strip()


def _count_attr(node: _Node, name: str) -> int:
    raw = node.attr(name).strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return 1


def _span(node: _Node) -> int:
    return _count_attr(node, "colspan")


def _rows(section: _Node) -> list[_Node]:
    rows = _direct(section, "tr")
    if rows:
        return rows
    for child in section.children:
        if isinstance(child, _Node) and child.tag.lower() in {"thead", "tbody", "tfoot"}:
            rows.extend(_direct(child, "tr"))
    return rows


def _cells(row: _Node) -> list[_Node]:
    return [child for child in row.children if isinstance(child, _Node) and child.tag.lower() in {"td", "th"}]


def _width(row: _Node) -> int:
    return sum(_span(cell) for cell in _cells(row))


def _labels(row: _Node) -> list[str]:
    labels: list[str] = []
    for cell in _cells(row):
        text = unescape(_plain(cell))
        labels.extend([text] * _span(cell))
    return labels


def _add_class(node: _Node, name: str) -> None:
    current = node.attr("class").split()
    if name not in current:
        current.append(name)
        node.set_attr("class", " ".join(current))


def _already_stacked(table: _Node) -> bool:
    if "stack" not in table.attr("class").split():
        return False
    body = _direct(table, "tbody")
    rows = _rows(body[0]) if body else [row for row in _direct(table, "tr")]
    cells = [cell for row in rows for cell in _cells(row)]
    return bool(cells) and all(cell.attr("data-label") for cell in cells)


def _detach(parent: _Node, node: _Node) -> None:
    parent.children = [child for child in parent.children if child is not node]
    for child in parent.children:
        if isinstance(child, _Node):
            _detach(child, node)


def _ensure_head(table: _Node) -> _Node | None:
    heads = _direct(table, "thead")
    if heads:
        rows = _rows(heads[0])
        return rows[-1] if rows else None
    rows = _rows(table)
    if not rows:
        return None
    first = rows[0]
    if not _cells(first) or any(cell.tag.lower() != "th" for cell in _cells(first)):
        return None
    _detach(table, first)
    head = _Node("thead", [])
    head.children.append(first)
    table.children.insert(0, head)
    return first


def _label_body(table: _Node, labels: list[str]) -> None:
    sections = _direct(table, "tbody") + _direct(table, "tfoot")
    rows: list[_Node] = []
    for section in sections:
        rows.extend(_rows(section))
    if not sections:
        rows = _direct(table, "tr")
    covered = [0] * len(labels)
    for row in rows:
        column = 0
        for cell in _cells(row):
            while column < len(covered) and covered[column] > 0:
                covered[column] -= 1
                column += 1
            span = _span(cell)
            if not cell.attr("data-label") and column < len(labels) and labels[column]:
                cell.set_attr("data-label", labels[column])
            down = _count_attr(cell, "rowspan")
            if down > 1:
                for slot in range(column, min(column + span, len(covered))):
                    covered[slot] = down - 1
            column += span


def _stack_table(table: _Node) -> bool:
    rows = _rows(table)
    if not rows or _width(rows[0]) < 4 or _already_stacked(table):
        return False
    header = _ensure_head(table)
    if header is None:
        header = rows[0]
    _label_body(table, _labels(header))
    _add_class(table, "stack")
    return True


def _serialize(node: _Node | str) -> str:
    if isinstance(node, str):
        return node
    if node.tag == "document":
        return "".join(_serialize(child) for child in node.children)
    attrs = []
    for key, value in node.attrs:
        if value is None:
            attrs.append(f" {key}")
        else:
            safe = value.replace("&", "&amp;").replace('"', "&quot;")
            attrs.append(f' {key}="{safe}"')
    open_tag = f"<{node.tag}{''.join(attrs)}>"
    if node.tag.lower() in _VOID:
        return open_tag
    inner = "".join(_serialize(child) for child in node.children)
    return f"{open_tag}{inner}</{node.tag}>"


def stack_wide_tables(html: str) -> str:
    """Return ``html`` with phone cards on tables of four or more columns."""
    parser = _Tree()
    parser.feed(html)
    parser.close()
    changed = False
    for table in reversed(_elements(parser.root, "table")):
        if _stack_table(table):
            changed = True
    rendered = _serialize(parser.root)
    if not changed or "table.stack{--line:" in rendered:
        return rendered
    if "</head>" in rendered:
        return rendered.replace("</head>", _CARD_CSS + "</head>", 1)
    return _CARD_CSS + rendered
