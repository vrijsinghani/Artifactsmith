"""Server-drawn charts. Inline SVG only: no fetches, no script, sanitizer-safe paths."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

from .chart_draw import draw_chart
from .chart_scale import format_value, prefers_horizontal

_NUM = re.compile(r"^[+−\-]?\s*[$€£¥]?\s*\d[\d,]*(?:\.\d+)?\s*%?$")
_MISSING = frozenset({"", "-", "—", "–", "n/a", "na", "null", "none", ".", "…", "nan"})
CHART_KINDS = frozenset({"bar", "hbar", "line", "pie", "donut"})


@dataclass(frozen=True)
class ChartSpec:
    kind: str
    unit: str
    labels: list[str]
    series: list[tuple[str, list[float | None]]]
    x_title: str = ""
    y_title: str = ""


def _number(raw: str) -> float | None:
    text = raw.strip().replace(",", "").replace(" ", "")
    text = text.replace("−", "-")
    for sym in ("$", "€", "£", "¥"):
        text = text.replace(sym, "")
    text = text.rstrip("%")
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return value


def _cell(raw: str) -> float | None:
    """A number, or None for a gap. ``ValueError`` when the cell is other text."""
    text = raw.strip()
    if text.lower() in _MISSING or text in {"—", "–", "…"}:
        return None
    value = _number(text)
    if value is None:
        raise ValueError(text)
    return value


def _row_cells(raw: str) -> list[str] | None:
    """A ``label: value`` row, or a pipe row with or without a leading pipe."""
    if "|" in raw:
        body = raw.strip()
        if body.startswith("|"):
            body = body[1:]
        if body.endswith("|"):
            body = body[:-1]
        cells = [cell.strip() for cell in body.split("|")]
        if len(cells) >= 2:
            return cells
    if ":" in raw:
        label, _, val = raw.lstrip("-").strip().partition(":")
        return [label.strip(), val.strip()]
    return None


def _year_names(cells: list[str]) -> bool:
    """``Month | 2024 | 2025`` is a header, not a point at the year 2024."""
    if len(cells) < 3 or _number(cells[0]) is not None:
        return False
    for cell in cells[1:]:
        if not re.fullmatch(r"\d{4}", cell):
            return False
        year = int(cell)
        if year < 1900 or year > 2100:
            return False
    return True


def _option(part: str, key: str) -> str | None:
    prefix = key + "="
    if not part.startswith(prefix):
        return None
    text = part[len(prefix) :].strip().strip("\"'").replace("_", " ")
    text = "".join(ch for ch in text if ch >= " " or ch == " ")
    return text[:40]


def parse_chart(info: str, content: str) -> ChartSpec | None:
    """Return a chart, or None when the values are not numeric.

    A blank cell, ``—``, or ``n/a`` is a missing point. The label stays so a
    line can break there instead of joining the neighbours.
    """
    parts = info.split()
    kind = parts[1].lower() if len(parts) > 1 else "bar"
    if kind not in CHART_KINDS:
        kind = "bar"
    unit = ""
    x_title = ""
    y_title = ""
    for part in parts[2:]:
        if (opt := _option(part, "unit")) is not None:
            unit = opt[:8]
        elif (opt := _option(part, "x")) is not None:
            x_title = opt
        elif (opt := _option(part, "y")) is not None:
            y_title = opt
    labels: list[str] = []
    columns: list[list[float | None]] = []
    names: list[str] = []
    numeric_rows = 0
    other_rows = 0
    for line in content.splitlines():
        raw = line.strip()
        if not raw or set(raw) <= set("|-: "):
            continue
        cells = _row_cells(raw)
        if cells is None or len(cells) < 2:
            other_rows += 1
            continue
        try:
            values = [_cell(cell) for cell in cells[1:]]
        except ValueError:
            if names == [] and numeric_rows == 0 and not _NUM.match(cells[1]):
                names = [cell[:40] for cell in cells[1:]]
                continue
            other_rows += 1
            continue
        if names == [] and numeric_rows == 0 and _year_names(cells):
            names = [cell[:40] for cell in cells[1:]]
            continue
        numeric_rows += 1
        labels.append(cells[0][:80])
        for index, value in enumerate(values):
            if index >= len(columns):
                columns.append([None] * (numeric_rows - 1))
            columns[index].append(value)
        for column in columns[len(values) :]:
            column.append(None)
    if numeric_rows == 0 or other_rows > numeric_rows:
        return None
    if not any(value is not None for column in columns for value in column):
        return None
    if not names:
        names = [""] * len(columns)
    width = len(labels)
    series = [
        (names[i] if i < len(names) else "", cols[:width]) for i, cols in enumerate(columns) if len(cols) >= width
    ]
    if not series:
        return None
    if kind == "bar" and prefers_horizontal(labels):
        kind = "hbar"
    return ChartSpec(kind, unit, labels, series[:6], x_title, y_title)


def aria_label(
    kind: str,
    heading: str,
    labels: list[str],
    values: Sequence[float | None],
    unit: str,
) -> str:
    bits = []
    for name, value in zip(labels, values, strict=False):
        if value is None:
            bits.append(f"{name} missing")
        else:
            bits.append(f"{name} {format_value(value, unit)}")
    title = {"bar": "Bar", "hbar": "Bar", "line": "Line", "pie": "Pie", "donut": "Donut"}.get(kind, "Bar")
    text = f"{title} chart"
    if heading:
        text += f", {heading}"
    text += ": " + ", ".join(bits)
    text = "".join(ch for ch in text if ch >= " " or ch == " ")
    if len(text) > 280:
        text = text[:277].rstrip() + "..."
    return text


def chart_svg(
    kind: str,
    labels: list[str],
    values: Sequence[float | None],
    unit: str,
    label: str,
    *,
    series: Sequence[tuple[str, Sequence[float | None]]] | None = None,
    x_title: str = "",
    y_title: str = "",
) -> str:
    """One SVG element. Colors come from stylesheet classes, not from the model."""
    return draw_chart(
        kind,
        labels,
        values,
        unit,
        label,
        series=series,
        x_title=x_title,
        y_title=y_title,
    )
