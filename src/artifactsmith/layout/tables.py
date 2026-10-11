"""HTML tables: group headers, row labels, signs, totals, and phone stacking."""

from __future__ import annotations

import re

from .chart import _number
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


_ID_HEADER = re.compile(
    r"(?:^|[^a-z0-9])(?:ids?|codes?|zips?|postal|phones?|years?|dates?|ranks?|numbers?|refs?|no)(?:[^a-z0-9]|$)|#",
    re.I,
)
_FACT_LABEL = re.compile(r"^(metrics?|measures?|fields?|items?|keys?)$", re.I)
_QUANTITY_HEADER = re.compile(
    r"amounts?|totals?|revenues?|billed|billing|costs?|prices?|fees?|paid|owed|rebates?|"
    r"counts?|qty|quantit(?:y|ies)|invoices?|members?|sales?|spend|balances?|hours?|"
    r"rates?|taxes|tax|profits?|margins?|percents?|pct|figures?|values?|sums?",
    re.I,
)
# Row labels that name a measure. Two or more means a key-value fact table.
_METRIC_LABEL = re.compile(
    _QUANTITY_HEADER.pattern + r"|months?|days?|weeks?|years?|people|users?|rate|ratio|average|median|count of",
    re.I,
)
_PRIMARY_MONEY = re.compile(r"billed|revenue|sales|totals?|amounts?|spend|costs?|paid|balance", re.I)


def auto_bar_chart(block: Block) -> Block | None:
    """A bar chart for one measured amount column, 3 to 12 data rows.

    A 2-column table uses the second column. A wider table with several amount
    columns charts the one primary money column (billed, revenue, total, and so
    on). Years, IDs, ranks, and key-value fact tables are left as tables. The
    caller skips this when the page already has a chart.
    """
    headers, rows, _clipped = limit_table(list(block.headers), [list(row) for row in block.rows])
    data = rows[:-1] if rows and _is_total_row(rows[-1]) else list(rows)
    if not 3 <= len(data) <= 12:
        return None
    width = _width(headers, data)
    if width < 2:
        return None
    columns = _plain_columns(headers, data, width)
    if columns is None:
        return None
    amounts = [index for index, column in enumerate(columns) if _amount_series(column) is not None]
    amount_at = _amount_column(columns, amounts, width)
    if amount_at is None:
        return None
    label_at = _label_index(columns, amount_at)
    if label_at is None or _FACT_LABEL.fullmatch(columns[label_at][0].strip()):
        return None
    series = _amount_series(columns[amount_at])
    if series is None:
        return None
    values, unit = series
    labels = [cell.strip()[:80] for cell in columns[label_at][1]]
    if any(not label for label in labels) or _mixed_measures(labels):
        return None
    kind = "hbar" if len(labels) > 6 else "bar"
    return Block(kind="chart", plain=kind, html=unit, labels=tuple(labels), numbers=tuple(values))


def _amount_column(columns: list[tuple[str, list[str]]], amounts: list[int], width: int) -> int | None:
    """The one amount column, or the single primary money column when several qualify."""
    if len(amounts) == 1:
        chosen = amounts[0]
        if width == 2 and chosen != 1:
            return None
        return chosen
    primary = [
        index
        for index in amounts
        if (series := _amount_series(columns[index])) is not None
        and series[1] in "$€£¥"
        and _PRIMARY_MONEY.search(columns[index][0])
    ]
    if len(primary) == 1:
        return primary[0]
    return None


def _mixed_measures(labels: list[str]) -> bool:
    """True when the row labels name different measures, as in a fact table."""
    return sum(1 for label in labels if _METRIC_LABEL.search(label)) >= 2


def _plain_columns(headers: list[Cell], rows: list[list[Cell]], width: int) -> list[tuple[str, list[str]]] | None:
    columns: list[tuple[str, list[str]]] = [
        (headers[index].plain.strip() if index < len(headers) else "", []) for index in range(width)
    ]
    for row in rows:
        placed = _place(row, width)
        if any(span != 1 for _cell, _col, span in placed):
            return None
        for cell, col, _span in placed:
            if col < width:
                columns[col][1].append(cell.plain.strip())
    return columns


def _label_index(columns: list[tuple[str, list[str]]], amount_at: int) -> int | None:
    for index, (_header, cells) in enumerate(columns):
        if index != amount_at and all(cell for cell in cells):
            return index
    return None


def _amount_series(column: tuple[str, list[str]]) -> tuple[list[float], str] | None:
    """Values and unit when the column is one measured quantity."""
    header, raws = column
    if _ID_HEADER.search(header):
        return None
    values: list[float] = []
    symbols: list[str] = []
    for raw in raws:
        value = _number(raw)
        if value is None or value < 0:
            return None
        values.append(value)
        symbols.append(_amount_symbol(raw))
    if any(symbols) and len(set(symbols)) != 1:
        return None
    unit = symbols[0] if symbols and symbols[0] else ""
    if _all_years(values) or _id_like(values, unit):
        return None
    if unit == "" and _QUANTITY_HEADER.search(header) is None:
        return None
    return values, unit


def _amount_symbol(raw: str) -> str:
    stripped = raw.lstrip()
    if stripped[:1] in "$€£¥":
        return stripped[:1]
    if stripped.endswith("%"):
        return "%"
    return ""


def _all_years(values: list[float]) -> bool:
    for value in values:
        if value != int(value):
            return False
        year = int(value)
        if not 1900 <= year <= 2100:
            return False
    return bool(values)


def _id_like(values: list[float], unit: str) -> bool:
    """Long integers with no currency, percent, decimal, or round amount."""
    if unit or any(value != int(value) for value in values):
        return False
    numbers = [int(value) for value in values]
    if not numbers or any(len(str(abs(number))) < 5 for number in numbers):
        return False
    return not any(_round_amount(number) for number in numbers)


def _round_amount(number: int) -> bool:
    if number < 100 or number % 100 != 0:
        return False
    core = number
    while core % 10 == 0 and core:
        core //= 10
    return core < 1000
