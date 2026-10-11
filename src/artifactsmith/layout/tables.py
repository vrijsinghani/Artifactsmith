"""HTML tables: group headers, row labels, signs, totals, and phone stacking."""

from __future__ import annotations

import re

from .inline import MAX_COLSPAN, esc
from .model import Block, Cell, class_attr

_NUMERIC = re.compile(r"^[+−\-]?\s*[$€£¥]?\s*\d[\d,]*(?:\.\d+)?\s*%?$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EMPTY = frozenset({"", "n/a", "na", "—", "-"})
_STATUS_HEADER = re.compile(r"status|result|state|decision", re.I)
_TOTAL_LABEL = frozenset({"total", "totals"})
MAX_TABLE_WIDTH = 24
MAX_TABLE_CELLS = 5000
# Exact cell text. Ambiguous words (open, yes, no) only count under a status header.
_STATUS_TONE: dict[str, str] = {
    "confirmed": "green",
    "agreed": "green",
    "approved": "green",
    "holding": "green",
    "top pick": "green",
    "rejected": "red",
    "declined": "red",
    "no entry": "red",
    "scan error": "red",
    "candidate": "amber",
    "estimated": "amber",
}
_STATUS_IF_HEADER: dict[str, str] = {
    "open": "amber",
    "pending": "amber",
    "warning": "amber",
    "done": "green",
    "yes": "green",
    "pass": "green",
    "active": "green",
    "closed": "gray",
    "no": "red",
    "fail": "red",
}


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
    width = max(1, min(width, MAX_TABLE_WIDTH))
    for cell in cells:
        if col >= width:
            break
        span = max(1, min(cell.colspan, MAX_COLSPAN, width - col))
        placed.append((cell, col, span))
        col += span
    while col < width:
        placed.append((Cell("", ""), col, 1))
        col += 1
    return placed


def limit_table(headers: list[Cell], rows: list[list[Cell]]) -> tuple[list[Cell], list[list[Cell]], bool]:
    """Clamp colspan, width, and cell count. The third value is true when anything was cut."""
    clipped = False

    def clip_row(row: list[Cell]) -> list[Cell]:
        nonlocal clipped
        out: list[Cell] = []
        used = 0
        for cell in row:
            span = max(1, min(cell.colspan, MAX_COLSPAN))
            if span != cell.colspan:
                clipped = True
                cell = Cell(cell.html, cell.plain, span)
            if used + span > MAX_TABLE_WIDTH:
                clipped = True
                break
            out.append(cell)
            used += span
        return out

    headers = clip_row(headers)
    clipped_rows = [clip_row(row) for row in rows]
    width = _width(headers, clipped_rows)
    width = max(1, min(width, MAX_TABLE_WIDTH))
    kept: list[list[Cell]] = []
    placed = len(headers)
    for index, row in enumerate(clipped_rows):
        if index > 0 and placed + width > MAX_TABLE_CELLS:
            clipped = True
            break
        kept.append(row)
        placed += width
    if len(kept) < len(rows):
        clipped = True
    return headers, kept, clipped


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


def _status_html(cell: Cell, header: str) -> str:
    if "asl-badge" in cell.html:
        return cell.html
    plain = cell.plain.strip()
    if not plain or len(plain) > 24:
        return cell.html
    key = plain.lower()
    tone = _STATUS_TONE.get(key)
    if tone is None and _STATUS_HEADER.search(header):
        tone = _STATUS_IF_HEADER.get(key)
    if tone is None:
        return cell.html
    return f'<span class="asl-badge tone-{tone}">{esc(plain)}</span>'


def _is_total_row(row: list[Cell]) -> bool:
    return bool(row) and row[0].plain.strip().lower() in _TOTAL_LABEL


def render_table(block: Block) -> str:
    """One table. Wide tables stack on a phone unless ``phone=scroll``."""
    headers, rows, _clipped = limit_table(list(block.headers), [list(row) for row in block.rows])
    width_guess = _width(headers, rows)
    rows = [_trim_pads(row, width_guess) for row in rows]
    if not rows:
        return ""
    width = _width(headers, rows)
    if len(headers) < width and headers:
        headers.extend(Cell("", "") for _ in range(width - len(headers)))
    marked_total = "total" in block.classes or (len(rows) > 1 and _is_total_row(rows[-1]))
    body = rows[:-1] if marked_total and len(rows) > 1 else rows
    foot = rows[-1:] if marked_total and len(rows) > 1 else []
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
        parts.append(_row(foot[0], width, headers, block.groups, numeric, rowhead, "td", total_line=True))
        parts.append("</tfoot>")
    parts.append("</table>")
    return f'<div class="asl-table-wrap">{"".join(parts)}</div>'


def _row(
    row: list[Cell],
    width: int,
    headers: list[Cell],
    groups: tuple[tuple[str, int], ...],
    numeric: set[int],
    rowhead: bool,
    tag: str,
    *,
    total_line: bool = False,
) -> str:
    parts = ['<tr class="total-line">' if total_line else "<tr>"]
    for cell, col, span in _place(row, width):
        if total_line and not cell.plain.strip() and not cell.html.strip():
            continue
        use = "th" if rowhead and col == 0 else tag
        scope = ' scope="row"' if use == "th" else ""
        span_attr = f' colspan="{span}"' if span > 1 else ""
        classes = []
        if col in numeric and span == 1:
            classes.append("num")
        sign = _sign_class(cell.plain)
        if sign:
            classes.append(sign)
        header = headers[col].plain if col < len(headers) else ""
        shown = cell.html if total_line else _status_html(cell, header)
        label = "" if total_line else _label(headers, col, groups)
        data = f' data-label="{esc(label)}"' if label else ""
        parts.append(f"<{use}{scope}{span_attr}{class_attr(*classes)}{data}>{shown}</{use}>")
    parts.append("</tr>")
    return "".join(parts)
