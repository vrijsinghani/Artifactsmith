"""Server-drawn charts. Inline SVG only: no fetches, no script, sanitizer-safe paths."""

from __future__ import annotations

import math
import re
from html import escape

_NUM = re.compile(r"^[+−\-]?\s*[$€£¥]?\s*\d[\d,]*(?:\.\d+)?\s*%?$")
CHART_KINDS = frozenset({"bar", "hbar", "line", "pie", "donut"})


def esc(text: str) -> str:
    return escape(text, quote=True)


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


def parse_chart(info: str, content: str) -> tuple[str, str, list[str], list[tuple[str, list[float]]]] | None:
    """Return kind, unit, labels, series. None when the values are not numeric."""
    parts = info.split()
    kind = parts[1].lower() if len(parts) > 1 else "bar"
    if kind not in CHART_KINDS:
        kind = "bar"
    unit = ""
    for part in parts[2:]:
        if part.startswith("unit="):
            unit = part.split("=", 1)[1][:8]
    labels: list[str] = []
    columns: list[list[float]] = []
    names: list[str] = []
    numeric_rows = 0
    other_rows = 0
    for line in content.splitlines():
        raw = line.strip()
        if not raw or set(raw) <= set("|-: "):
            continue
        if raw.startswith("|"):
            cells = [cell.strip() for cell in raw.strip("|").split("|")]
        elif ":" in raw:
            label, _, val = raw.lstrip("-").strip().partition(":")
            cells = [label.strip(), val.strip()]
        else:
            other_rows += 1
            continue
        if len(cells) < 2:
            continue
        values = [_number(cell) for cell in cells[1:]]
        if names == [] and any(value is None for value in values) and not _NUM.match(cells[1]):
            names = cells[1:]
            continue
        if any(value is None for value in values):
            other_rows += 1
            continue
        numeric_rows += 1
        labels.append(cells[0][:80])
        if not columns:
            columns = [[] for _ in values]
        for index, value in enumerate(values):
            if index >= len(columns):
                columns.append([])
            assert value is not None
            columns[index].append(value)
    if numeric_rows == 0 or other_rows > numeric_rows:
        return None
    if not names:
        names = [""] * len(columns)
    width = len(labels)
    series = [
        (names[i] if i < len(names) else "", cols[:width]) for i, cols in enumerate(columns) if len(cols) >= width
    ]
    if not series:
        return None
    if kind == "bar" and len(labels) > 6:
        kind = "hbar"
    return kind, unit, labels, series[:6]


def _fmt(value: float, unit: str) -> str:
    if value == int(value) and abs(value) < 1e15:
        shown = f"{int(value):,}"
    else:
        shown = f"{value:,.2f}"
    if unit in {"$", "€", "£", "¥"}:
        return f"{unit}{shown}"
    if unit:
        return f"{shown}{unit}"
    return shown


def aria_label(kind: str, heading: str, labels: list[str], values: list[float], unit: str) -> str:
    bits = [f"{label} {_fmt(value, unit)}" for label, value in zip(labels, values, strict=False)]
    title = {"bar": "Bar", "hbar": "Bar", "line": "Line", "pie": "Pie", "donut": "Donut"}.get(kind, "Bar")
    text = f"{title} chart"
    if heading:
        text += f", {heading}"
    text += ": " + ", ".join(bits)
    text = "".join(ch for ch in text if ch >= " " or ch == " ")
    if len(text) > 280:
        text = text[:277].rstrip() + "..."
    return text


def _xy(cx: float, cy: float, radius: float, angle: float) -> tuple[float, float]:
    theta = -math.pi / 2 + angle
    return cx + radius * math.cos(theta), cy + radius * math.sin(theta)


def _slice(cx: float, cy: float, radius: float, start: float, end: float) -> str:
    sweep = end - start
    if sweep >= math.tau - 1e-3:
        x1, y1 = _xy(cx, cy, radius, 0)
        x2, y2 = _xy(cx, cy, radius, math.pi)
        return (
            f"M {x1:.2f} {y1:.2f} A {radius:.2f} {radius:.2f} 0 1 1 {x2:.2f} {y2:.2f} "
            f"A {radius:.2f} {radius:.2f} 0 1 1 {x1:.2f} {y1:.2f} Z"
        )
    large = 1 if sweep > math.pi else 0
    x0, y0 = _xy(cx, cy, radius, start)
    x1, y1 = _xy(cx, cy, radius, end)
    return f"M {cx:.2f} {cy:.2f} L {x0:.2f} {y0:.2f} A {radius:.2f} {radius:.2f} 0 {large} 1 {x1:.2f} {y1:.2f} Z"


def _donut(cx: float, cy: float, outer: float, inner: float, start: float, end: float) -> str:
    sweep = end - start
    large = 1 if sweep > math.pi else 0
    if sweep >= math.tau - 1e-3:
        return _slice(cx, cy, outer, 0, math.tau)  # full disc; inner ring is the next series' concern
    x0, y0 = _xy(cx, cy, outer, start)
    x1, y1 = _xy(cx, cy, outer, end)
    i0, j0 = _xy(cx, cy, inner, start)
    i1, j1 = _xy(cx, cy, inner, end)
    return (
        f"M {x0:.2f} {y0:.2f} A {outer:.2f} {outer:.2f} 0 {large} 1 {x1:.2f} {y1:.2f} "
        f"L {i1:.2f} {j1:.2f} A {inner:.2f} {inner:.2f} 0 {large} 0 {i0:.2f} {j0:.2f} Z"
    )


def _short(label: str, limit: int) -> str:
    if len(label) <= limit:
        return label
    return label[: limit - 1].rstrip() + "…"


def chart_svg(kind: str, labels: list[str], values: list[float], unit: str, label: str) -> str:
    """One SVG element. Colors come from stylesheet classes, not from the model."""
    safe = esc(label)
    if kind in {"pie", "donut"}:
        return _pie_svg(kind, labels, values, safe)
    if kind == "line":
        return _line_svg(labels, values, safe)
    if kind == "hbar":
        return _hbar_svg(labels, values, safe)
    return _bar_svg(labels, values, safe)


def _bar_svg(labels: list[str], values: list[float], label: str) -> str:
    width, height = 640, 280
    left, right, top, bottom = 28, 12, 16, 36
    plot_w = width - left - right
    plot_h = height - top - bottom
    peak = max(values) or 1
    count = max(len(values), 1)
    gap = 10
    bar_w = max((plot_w - gap * (count + 1)) / count, 4)
    parts = [
        f'<svg role="img" aria-label="{label}" viewBox="0 0 {width} {height}" width="640" height="280">',
        f'<line x1="{left}" y1="{top + plot_h}" x2="{width - right}" y2="{top + plot_h}" class="axis"/>',
    ]
    for index, (name, value) in enumerate(zip(labels, values, strict=False)):
        bar_h = 0 if peak == 0 else plot_h * max(value, 0) / peak
        x = left + gap + index * (bar_w + gap)
        y = top + plot_h - bar_h
        parts.append(f'<rect class="bar" x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{max(bar_h, 0):.1f}"/>')
        cx = x + bar_w / 2
        parts.append(
            f'<text x="{cx:.1f}" y="{height - 12}" text-anchor="middle" font-size="12" font-family="sans-serif">'
            f"{esc(_short(name, 14))}</text>"
        )
    parts.append("</svg>")
    return "".join(parts)


def _hbar_svg(labels: list[str], values: list[float], label: str) -> str:
    row = 32
    height = 20 + row * len(labels)
    width = 640
    peak = max(values) or 1
    parts = [f'<svg role="img" aria-label="{label}" viewBox="0 0 {width} {height}" width="640" height="{height}">']
    for index, (name, value) in enumerate(zip(labels, values, strict=False)):
        y = 12 + index * row
        bar_w = 0 if peak == 0 else 420 * max(value, 0) / peak
        parts.append(f'<text x="8" y="{y + 16}" font-size="12" font-family="sans-serif">{esc(_short(name, 18))}</text>')
        parts.append(f'<rect class="bar" x="150" y="{y}" width="{bar_w:.1f}" height="18"/>')
    parts.append("</svg>")
    return "".join(parts)


def _line_svg(labels: list[str], values: list[float], label: str) -> str:
    width, height = 640, 280
    left, top, plot_w, plot_h = 36, 16, 580, 210
    peak = max(values) or 1
    count = max(len(values) - 1, 1)
    coords: list[tuple[float, float]] = []
    for index, value in enumerate(values):
        x = left + plot_w * index / count
        y = top + plot_h - (plot_h * max(value, 0) / peak)
        coords.append((x, y))
    points = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    parts = [
        f'<svg role="img" aria-label="{label}" viewBox="0 0 {width} {height}" width="640" height="280">',
        f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" class="axis"/>',
        f'<polyline class="line" points="{points}"/>',
    ]
    for (x, y), name in zip(coords, labels, strict=False):
        parts.append(f'<circle class="dot" cx="{x:.1f}" cy="{y:.1f}" r="3"/>')
        parts.append(
            f'<text x="{x:.1f}" y="{height - 14}" text-anchor="middle" font-size="12" font-family="sans-serif">'
            f"{esc(_short(name, 12))}</text>"
        )
    parts.append("</svg>")
    return "".join(parts)


def _pie_svg(kind: str, labels: list[str], values: list[float], label: str) -> str:
    total = sum(max(value, 0) for value in values) or 1
    cx, cy, outer = 120, 120, 88
    parts = ['<svg role="img" aria-label="' + label + '" viewBox="0 0 640 260" width="640" height="260">']
    angle = 0.0
    for index, value in enumerate(values):
        sweep = math.tau * max(value, 0) / total
        end = angle + sweep
        path = _donut(cx, cy, outer, 48, angle, end) if kind == "donut" else _slice(cx, cy, outer, angle, end)
        if sweep > 0:
            parts.append(f'<path class="pie{index % 6}" d="{path}"/>')
        angle = end
    for index, name in enumerate(labels):
        y = 28 + index * 22
        parts.append(f'<rect class="pie{index % 6}" x="250" y="{y - 12}" width="14" height="14"/>')
        parts.append(f'<text x="272" y="{y}" font-size="13" font-family="sans-serif">{esc(_short(name, 32))}</text>')
    parts.append("</svg>")
    return "".join(parts)
