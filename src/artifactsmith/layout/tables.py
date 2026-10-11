"""HTML tables: group headers, row labels, signs, totals, and phone stacking."""

from __future__ import annotations

import re

from .inline import esc
from .model import Block, Cell, class_attr

_NUMERIC = re.compile(r"^[+−\-]?\s*[$€£¥]?\s*\d[\d,]*(?:\.\d+)?\s*%?$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EMPTY = frozenset({"", "n/a", "na", "—", "-"})


def _is_numeric(plain: str) -> bool:
    text = plain.strip()
    if text.lower() in _EMPTY:
        return False
    return bool(_NUMERIC.fullmatch(text) or _ISO_DATE.fullmatch(text))


def _sign_class(plain: str) -> str:
    text = plain.strip()
    if not any(ch.isdigit() for ch in text):
        return ""
    if text.startswith("+"):
        return "pos"
    if text.startswith(("−", "-")):
        return "neg"
    return ""


def _span_sum(cells: list[Cell]) -> int:
    return sum(max(cell.colspan, 1) for cell in cells)


def _width(headers: list[Cell], rows: list[list[Cell]]) -> int:
    if headers:
        return max(_span_sum(headers), 1)
    if rows:
        return max(_span_sum(row) for row in rows)
    return 1


def _group_at(groups: tuple[tuple[str, int], ...], index: int) -> str:
    cursor = 0
    for label, span in groups:
        if cursor <= index < cursor + span:
            return label
        cursor += span
    return ""


def _place(cells: list[Cell], width: int) -> list[tuple[Cell, int, int]]:
    placed: list[tuple[Cell, int, int]] = []
    col = 0
    for cell in cells:
        if col >= width:
            placed.append((cell, col, max(cell.colspan, 1)))
            col += max(cell.colspan, 1)
            continue
        span = max(1, min(cell.colspan, width - col))
        placed.append((cell, col, span))
        col += span
    while col < width:
        placed.append((Cell("", ""), col, 1))
        col += 1
    return placed


def _numeric_columns(rows: list[list[Cell]], width: int) -> set[int]:
    hits = [0] * width
    seen = [0] * width
    for row in rows:
        for cell, col, span in _place(row, width):
            if span != 1 or col >= width or not cell.plain.strip():
                continue
            seen[col] += 1
            if _is_numeric(cell.plain):
                hits[col] += 1
    return {index for index in range(width) if seen[index] and hits[index] / seen[index] >= 0.6}


def _label(headers: list[Cell], col: int, groups: tuple[tuple[str, int], ...]) -> str:
    name = headers[col].plain.strip() if col < len(headers) else ""
    group = _group_at(groups, col)
    if group and name:
        return f"{group}: {name}"
    return group or name


def _trim_pads(cells: list[Cell], width: int) -> list[Cell]:
    """Drop empty cells a Markdown table added after a ``{N}`` span."""
    trimmed = list(cells)
    while len(trimmed) > 1 and sum(max(cell.colspan, 1) for cell in trimmed) > width:
        last = trimmed[-1]
        if last.plain.strip() or last.html.strip():
            break
        trimmed.pop()
    return trimmed


def render_table(block: Block) -> str:
    """One table. Wide tables stack on a phone unless ``phone=scroll``."""
    headers = list(block.headers)
    width_guess = _width(headers, block.rows)
    rows = [_trim_pads(row, width_guess) for row in block.rows]
    if not rows:
        return ""
    width = _width(headers, rows)
    if len(headers) < width and headers:
        headers.extend(Cell("", "") for _ in range(width - len(headers)))
    body = rows[:-1] if "total" in block.classes and len(rows) > 1 else rows
    foot = rows[-1:] if "total" in block.classes and len(rows) > 1 else []
    numeric = _numeric_columns(body or rows, width)
    phone = block.attrs.get("phone") or ""
    stack = phone != "scroll" and width > 4
    names = ["asl-table"]
    if block.attrs.get("variant"):
        names.append("variant-" + block.attrs["variant"])
    names.extend(name for name in block.classes if name != "total")
    if stack:
        names.append("stack")
    if phone == "scroll":
        names.append("scroll")
    parts = [f"<table{class_attr(*names)}>"]
    if block.groups or headers:
        parts.append("<thead>")
        if block.groups:
            parts.append("<tr>")
            for label, span in block.groups:
                parts.append(f'<th colspan="{span}" scope="colgroup">{esc(label)}</th>')
            parts.append("</tr>")
        if headers:
            parts.append("<tr>")
            for cell, _col, span in _place(headers, width):
                span_attr = f' colspan="{span}"' if span > 1 else ""
                num = " num" if _col in numeric and span == 1 else ""
                parts.append(f'<th scope="col"{span_attr}{class_attr(num.strip())}>{cell.html}</th>')
            parts.append("</tr>")
        parts.append("</thead>")
    rowhead = "rowhead" in block.classes
    parts.append("<tbody>")
    for row in body:
        parts.append(_row(row, width, headers, block.groups, numeric, rowhead, "td"))
    parts.append("</tbody>")
    if foot:
        parts.append("<tfoot>")
        parts.append(_row(foot[0], width, headers, block.groups, numeric, rowhead, "td"))
        parts.append("</tfoot>")
    parts.append("</table>")
    return "".join(parts)


def _row(
    row: list[Cell],
    width: int,
    headers: list[Cell],
    groups: tuple[tuple[str, int], ...],
    numeric: set[int],
    rowhead: bool,
    tag: str,
) -> str:
    parts = ["<tr>"]
    for cell, col, span in _place(row, width):
        use = "th" if rowhead and col == 0 else tag
        scope = ' scope="row"' if use == "th" else ""
        span_attr = f' colspan="{span}"' if span > 1 else ""
        classes = []
        if col in numeric and span == 1:
            classes.append("num")
        sign = _sign_class(cell.plain)
        if sign:
            classes.append(sign)
        label = _label(headers, col, groups)
        data = f' data-label="{esc(label)}"' if label else ""
        parts.append(f"<{use}{scope}{span_attr}{class_attr(*classes)}{data}>{cell.html}</{use}>")
    parts.append("</tr>")
    return "".join(parts)
