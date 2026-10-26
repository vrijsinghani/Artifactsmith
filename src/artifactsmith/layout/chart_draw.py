"""Inline SVG charts. Colors come from stylesheet classes, not from the model."""

from __future__ import annotations

import math
from collections.abc import Sequence
from html import escape

from .chart_scale import (
    LabelMark,
    Scale,
    bar_scale,
    bare_years,
    chart_width_budget,
    format_tick,
    format_value,
    keep_tick_labels,
    line_scale,
    plan_category_labels,
    point_runs,
    prefers_horizontal,
    text_width,
)

Series = list[tuple[str, list[float | None]]]
_FONT = 14.0
_PLOT_H = 168.0
_COS = math.cos(math.radians(40))
_SIN = math.sin(math.radians(40))


def esc(text: str) -> str:
    return escape(text, quote=True)


def draw_chart(
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
    """One SVG element. ``series`` overrides the single ``values`` column."""
    columns = _columns(labels, values, series)
    if not columns:
        return ""
    safe = esc(label)
    if kind in {"pie", "donut"}:
        return _pie(kind, labels, columns[0][1], safe)
    drawn = "hbar" if kind == "hbar" or (kind == "bar" and prefers_horizontal(labels)) else kind
    font = _FONT
    if drawn == "hbar":
        svg, _width = _horizontal(labels, columns, unit, safe, x_title, y_title, font)
    else:
        svg, _width = _vertical(drawn, labels, columns, unit, safe, x_title, y_title, font)
    return svg


def _columns(
    labels: list[str],
    values: Sequence[float | None],
    series: Sequence[tuple[str, Sequence[float | None]]] | None,
) -> Series:
    count = len(labels)
    raw = list(series) if series else [("", list(values))]
    columns: Series = []
    for name, column in raw[:6]:
        aligned: list[float | None] = []
        for index in range(count):
            cell = column[index] if index < len(column) else None
            if cell is None or not math.isfinite(cell):
                aligned.append(None)
            else:
                aligned.append(float(cell))
        columns.append((str(name), aligned))
    if not any(value is not None for _name, column in columns for value in column):
        return []
    return columns


def _vertical(
    kind: str,
    labels: list[str],
    series: Series,
    unit: str,
    label: str,
    x_title: str,
    y_title: str,
    font: float,
) -> tuple[str, float]:
    count = max(len(labels), 1)
    flat = [value for _name, column in series for value in column]
    scale = line_scale(flat) if kind == "line" else bar_scale(flat)
    year = bare_years(flat, unit)
    tick_w = _tick_width(scale, unit, font, year)
    title_x, title_col = _y_title_band(font, y_title)
    legend_h, legend = _legend(series, font)
    budget = chart_width_budget(font)
    plot_w = max(72.0, budget - (title_col + tick_w + 10) - 16)
    marks = plan_category_labels(labels, plot_w / count, font)
    left = title_col + tick_w + 8
    right = 12.0
    for _ in range(4):
        slot = plot_w / count
        marks = plan_category_labels(labels, slot, font)
        left = max(title_col + tick_w + 8, _overhang(marks, slot, font) + 4)
        right = max(12.0, _overhang_right(marks, slot, font, count))
        if left + plot_w + right <= budget + 0.5:
            break
        plot_w = max(64.0, plot_w - (left + plot_w + right - budget))
    show_values = _few(series)
    top = max(font * 0.9 + 4, 8) + legend_h + (font + 6 if show_values else 2)
    cat_h = _cat_height(marks, font)
    foot = cat_h + (font + 6 if x_title else 0) + 6
    width = left + plot_w + right
    height = top + _PLOT_H + foot
    parts = [legend]
    parts.extend(_y_axis(scale, unit, font, left, top, plot_w, title_x, y_title, year))
    if kind == "line":
        parts.extend(_lines(series, scale, left, top, slot, font, show_values, unit, year))
    else:
        parts.extend(_bars(series, scale, left, top, slot, font, show_values, unit, year))
    parts.extend(_categories(marks, left, top + _PLOT_H, slot, font))
    if x_title:
        parts.append(_axis_title(x_title, left + plot_w / 2, height - 6, font, plot_w))
    return _svg(width, height, label, parts), width


def _horizontal(
    labels: list[str],
    series: Series,
    unit: str,
    label: str,
    x_title: str,
    y_title: str,
    font: float,
) -> tuple[str, float]:
    flat = [value for _name, column in series for value in column]
    scale = bar_scale(flat)
    year = bare_years(flat, unit)
    groups = len(series)
    row_h = max(font + 12, groups * (font * 0.85) + 10)
    legend_h, legend = _legend(series, font)
    budget = chart_width_budget(font)
    tick_w = _tick_width(scale, unit, font, year)
    finite = [value for value in flat if value is not None]
    value_w = max((text_width(format_value(value, unit, year=year), font) for value in finite), default=0.0)
    right = max(tick_w / 2, value_w * 0.5) + 8
    plot_w = min(180.0, max(88.0, budget * 0.46))
    cat_w = font * 6
    cat_limit = font * 6
    for _ in range(4):
        cat_limit = max(font * 4, budget - plot_w - right - 8)
        cat_w = min(cat_limit, max((text_width(name, font) for name in labels), default=0.0) + 6)
        if cat_w + 8 + plot_w + right <= budget + 0.5:
            break
        plot_w = max(72.0, plot_w - (cat_w + 8 + plot_w + right - budget))
    left = cat_w + 8
    tick_h = font + 8
    top = max(font * 0.6, 8) + legend_h
    height = top + row_h * len(labels) + tick_h + (font + 8 if y_title or x_title else 6)
    width = left + plot_w + right
    parts = [legend]
    parts.extend(_x_axis(scale, unit, font, left, top, plot_w, row_h * len(labels), year))
    parts.extend(_hbars(labels, series, scale, left, top, plot_w, row_h, cat_w, font, unit, cat_limit, year))
    title = y_title or x_title
    if title:
        parts.append(_axis_title(title, left + plot_w / 2, height - 4, font, plot_w))
    return _svg(width, height, label, parts), width


def _lines(
    series: Series,
    scale: Scale,
    left: float,
    top: float,
    slot: float,
    font: float,
    show_values: bool,
    unit: str,
    year: bool,
) -> list[str]:
    parts: list[str] = []
    for index, (_name, column) in enumerate(series):
        for run in point_runs(column):
            coords: list[tuple[float, float, float]] = []
            for i in run:
                value = column[i]
                if value is None:
                    continue
                coords.append((left + (i + 0.5) * slot, _y(value, scale, top), value))
            if len(coords) >= 2:
                points = " ".join(f"{_n(cx)},{_n(cy)}" for cx, cy, _value in coords)
                parts.append(f'<polyline class="{_line_class(index)}" points="{points}"/>')
            for cx, cy, value in coords:
                parts.append(f'<circle class="{_fill_class("dot", index)}" cx="{_n(cx)}" cy="{_n(cy)}" r="3.5"/>')
                if show_values:
                    parts.append(_text(cx, cy - 8, format_value(value, unit, year=year), "", font, anchor="middle"))
    return parts


def _bars(
    series: Series,
    scale: Scale,
    left: float,
    top: float,
    slot: float,
    font: float,
    show_values: bool,
    unit: str,
    year: bool,
) -> list[str]:
    groups = len(series)
    gap = 3.0
    inner = slot * 0.72
    bar_w = max(3.0, (inner - gap * (groups - 1)) / groups)
    base = _y(0.0, scale, top)
    parts: list[str] = []
    for index, (_name, column) in enumerate(series):
        for i, value in enumerate(column):
            if value is None:
                continue
            gx = left + i * slot + (slot - (groups * bar_w + (groups - 1) * gap)) / 2
            x = gx + index * (bar_w + gap)
            y_val = _y(value, scale, top)
            y = min(y_val, base)
            height = max(abs(base - y_val), 0.0 if value == 0 else 1.0)
            parts.append(
                f'<rect class="{_fill_class("bar", index)}" x="{_n(x)}" y="{_n(y)}" '
                f'width="{_n(bar_w)}" height="{_n(height)}"/>'
            )
            if show_values:
                label_y = y - 4 if value >= 0 else y + height + font
                parts.append(
                    _text(x + bar_w / 2, label_y, format_value(value, unit, year=year), "", font, anchor="middle")
                )
    return parts


def _hbars(
    labels: list[str],
    series: Series,
    scale: Scale,
    left: float,
    top: float,
    plot_w: float,
    row_h: float,
    cat_w: float,
    font: float,
    unit: str,
    limit: float,
    year: bool,
) -> list[str]:
    groups = len(series)
    gap = 3.0
    bar_h = max(6.0, (row_h - 10 - gap * (groups - 1)) / groups)
    span = scale.hi - scale.lo or 1.0
    origin = left + (0.0 - scale.lo) / span * plot_w
    show_values = _few(series)
    parts: list[str] = []
    for i, name in enumerate(labels):
        shown = name if text_width(name, font) <= limit else _cut(name, limit, font)
        mid = top + i * row_h + row_h / 2
        parts.append(_text(cat_w, mid, shown, name, font, anchor="end", baseline="central"))
        block = groups * bar_h + (groups - 1) * gap
        y0 = top + i * row_h + (row_h - block) / 2
        for index, (_sname, column) in enumerate(series):
            value = column[i] if i < len(column) else None
            if value is None:
                continue
            end = left + (value - scale.lo) / span * plot_w
            x = min(origin, end)
            width = max(abs(end - origin), 0.0 if value == 0 else 1.0)
            y = y0 + index * (bar_h + gap)
            parts.append(
                f'<rect class="{_fill_class("bar", index)}" x="{_n(x)}" y="{_n(y)}" '
                f'width="{_n(width)}" height="{_n(bar_h)}"/>'
            )
            if show_values:
                anchor = "start" if value >= 0 else "end"
                vx = end + (4 if value >= 0 else -4)
                parts.append(
                    _text(
                        vx,
                        y + bar_h / 2,
                        format_value(value, unit, year=year),
                        "",
                        font,
                        anchor=anchor,
                        baseline="central",
                    )
                )
    return parts


def _y_axis(
    scale: Scale,
    unit: str,
    font: float,
    left: float,
    top: float,
    plot_w: float,
    title_x: float,
    y_title: str,
    year: bool,
) -> list[str]:
    parts: list[str] = []
    labels = [format_tick(tick, unit, scale.step, year=year) for tick in scale.ticks]
    positions = [_y(tick, scale, top) for tick in scale.ticks]
    # A centered tick label is about one em tall. Drop a label that would sit on its neighbor.
    keep = keep_tick_labels(positions, [font * 1.15 for _label in labels])
    for index, label in enumerate(labels):
        y = positions[index]
        minor = " minor" if _minor(index, len(scale.ticks)) or not keep[index] else ""
        parts.append(f'<line class="axis{minor}" x1="{_n(left)}" y1="{_n(y)}" x2="{_n(left + plot_w)}" y2="{_n(y)}"/>')
        if not keep[index]:
            continue
        parts.append(_text(left - 6, y, label, "", font, anchor="end", baseline="central", css=minor.strip()))
    if y_title:
        # Shorter than the plot, so the rotated caps stay inside the viewBox.
        limit = max(font * 2, _PLOT_H - font)
        parts.append(_axis_title(y_title, title_x, top + _PLOT_H / 2, font, limit, rotate=-90))
    return parts


def _x_axis(
    scale: Scale, unit: str, font: float, left: float, top: float, plot_w: float, plot_h: float, year: bool
) -> list[str]:
    parts: list[str] = []
    span = scale.hi - scale.lo or 1.0
    labels = [format_tick(tick, unit, scale.step, year=year) for tick in scale.ticks]
    positions = [left + (tick - scale.lo) / span * plot_w for tick in scale.ticks]
    sizes = [text_width(label, font) for label in labels]
    keep = keep_tick_labels(positions, sizes)
    for index, label in enumerate(labels):
        x = positions[index]
        minor = " minor" if _minor(index, len(scale.ticks)) or not keep[index] else ""
        parts.append(f'<line class="axis{minor}" x1="{_n(x)}" y1="{_n(top)}" x2="{_n(x)}" y2="{_n(top + plot_h)}"/>')
        if not keep[index]:
            continue
        parts.append(_text(x, top + plot_h + font, label, "", font, anchor="middle", css=minor.strip()))
    return parts


def _categories(marks: list[LabelMark], left: float, axis_y: float, slot: float, font: float) -> list[str]:
    parts: list[str] = []
    for index, mark in enumerate(marks):
        if not mark.shown:
            continue
        x = left + (index + 0.5) * slot
        if mark.rotate:
            # End-anchored text rotated -40 swings upward. Start below that rise
            # so the label clears the lowest y tick, which hangs under the axis.
            rise = text_width(mark.shown, font) * _SIN + font * 0.35
            y = axis_y + _tick_hang(font) + rise
            parts.append(_text(x, y, mark.shown, mark.full, font, anchor="end", rotate=-40))
        else:
            parts.append(_text(x, axis_y + _category_drop(font), mark.shown, mark.full, font, anchor="middle"))
    return parts


def _legend(series: Series, font: float) -> tuple[float, str]:
    if len(series) < 2:
        return 0.0, ""
    parts: list[str] = []
    x = 8.0
    y = 6.0
    row_h = font + 6
    max_x = 8.0
    for index, (name, _column) in enumerate(series):
        shown = name or f"Series {index + 1}"
        width = 16 + text_width(shown, font)
        if x > 8 and x + width > 380:
            x = 8.0
            y += row_h
        parts.append(f'<rect class="{_fill_class("bar", index)}" x="{_n(x)}" y="{_n(y)}" width="10" height="10"/>')
        parts.append(_text(x + 14, y + 9, shown, name, font, baseline="central"))
        x += width + 14
        max_x = max(max_x, x)
    return y + row_h, "".join(parts)


def _pie(kind: str, labels: list[str], values: list[float | None], label: str) -> str:
    finite = [0.0 if value is None or value < 0 else value for value in values]
    total = sum(finite) or 1.0
    # 22 user units is 11px when this 640-wide drawing is shown at 320px.
    font = 22.0
    row = 28
    height = max(260, 24 + len(labels) * row)
    parts = [_svg_open(640, height, label)]
    angle = 0.0
    for index, value in enumerate(finite):
        sweep = math.tau * value / total
        end = angle + sweep
        if sweep > 0:
            path = _donut(120, 120, 88, 48, angle, end) if kind == "donut" else _slice(120, 120, 88, angle, end)
            parts.append(f'<path class="pie{index % 6}" d="{path}"/>')
        angle = end
    for index, name in enumerate(labels):
        shown = name if len(name) <= 32 else name[:31].rstrip() + "…"
        y = 28 + index * row
        parts.append(f'<rect class="pie{index % 6}" x="250" y="{y - 12}" width="14" height="14"/>')
        parts.append(_text(272, y, shown, name, font))
    parts.append("</svg>")
    return "".join(parts)


def _axis_title(text: str, x: float, y: float, font: float, limit: float, rotate: float | None = None) -> str:
    shown = text if text_width(text, font) <= limit else _cut(text, limit, font)
    anchor = "middle"
    return _text(x, y, shown, text, font, anchor=anchor, rotate=rotate)


def _text(
    x: float,
    y: float,
    shown: str,
    full: str,
    font: float,
    *,
    anchor: str = "start",
    baseline: str = "",
    rotate: float | None = None,
    css: str = "",
) -> str:
    attrs = [f'x="{_n(x)}"', f'y="{_n(y)}"', f'font-size="{font:.0f}"']
    if css:
        attrs.append(f'class="{css}"')
    if anchor != "start":
        attrs.append(f'text-anchor="{anchor}"')
    if baseline:
        attrs.append(f'dominant-baseline="{baseline}"')
    if rotate is not None:
        attrs.append(f'transform="rotate({rotate:.0f} {_n(x)} {_n(y)})"')
    title = f"<title>{esc(full)}</title>" if full and full != shown else ""
    return f"<text {' '.join(attrs)}>{title}{esc(shown)}</text>"


def _svg(width: float, height: float, label: str, parts: list[str]) -> str:
    return _svg_open(width, height, label) + "".join(parts) + "</svg>"


def _svg_open(width: float, height: float, label: str) -> str:
    w = max(1, int(math.ceil(width)))
    h = max(1, int(math.ceil(height)))
    return f'<svg role="img" aria-label="{label}" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'


def _y(value: float, scale: Scale, top: float) -> float:
    span = scale.hi - scale.lo or 1.0
    return top + (scale.hi - value) / span * _PLOT_H


def _tick_width(scale: Scale, unit: str, font: float, year: bool) -> float:
    return max(text_width(format_tick(tick, unit, scale.step, year=year), font) for tick in scale.ticks)


def _few(series: Series) -> bool:
    if len(series) != 1:
        return False
    return sum(value is not None for value in series[0][1]) <= 8


def _minor(index: int, count: int) -> bool:
    if count <= 4:
        return False
    return index not in {0, count - 1} and index % 2 == 1


def _overhang(marks: list[LabelMark], slot: float, font: float) -> float:
    reach = 0.0
    for index, mark in enumerate(marks):
        if not mark.shown:
            continue
        width = text_width(mark.shown, font)
        span = width * _COS if mark.rotate else width / 2
        reach = max(reach, span - (index + 0.5) * slot)
    return reach


def _overhang_right(marks: list[LabelMark], slot: float, font: float, count: int) -> float:
    reach = 0.0
    for index, mark in enumerate(marks):
        if not mark.shown or mark.rotate:
            continue
        width = text_width(mark.shown, font) / 2
        reach = max(reach, width - (count - index - 0.5) * slot)
    return reach


def _cat_height(marks: list[LabelMark], font: float) -> float:
    shown = [mark for mark in marks if mark.shown]
    if not shown:
        return 4.0
    if any(mark.rotate for mark in shown):
        widest = max(text_width(mark.shown, font) for mark in shown)
        return _tick_hang(font) + widest * _SIN + font + 8
    return _category_drop(font) + font * 0.35 + 4


def _tick_hang(font: float) -> float:
    """How far a centered tick label extends below the axis."""
    return font * 0.85 + 6


def _category_drop(font: float) -> float:
    """Baseline of a flat category label, below the hanging tick label."""
    return _tick_hang(font) + font * 0.85


def _y_title_band(font: float, title: str) -> tuple[float, float]:
    """Anchor x and column width for a rotated y title. ``(0, 0)`` when there is none.

    A rotated glyph's cap height sticks out to one side of the anchor by most of
    an em. The anchor sits far enough from x=0 that ``Rate`` is not clipped into
    something that reads as ``Kate``.
    """
    if not title:
        return 0.0, 0.0
    anchor = font * 0.95 + 4
    column = anchor + font * 0.5 + 4
    return anchor, column


def _fill_class(kind: str, index: int) -> str:
    if index % 6 == 0:
        return kind
    return f"{kind} pie{index % 6}"


def _line_class(index: int) -> str:
    if index % 6 == 0:
        return "line"
    return f"line pie{index % 6}"


def _cut(text: str, limit: float, font: float) -> str:
    if text_width(text, font) <= limit:
        return text
    chars = list(text)
    while len(chars) > 1 and text_width("".join(chars) + "…", font) > limit:
        chars.pop()
    return ("".join(chars).rstrip() or text[:1]) + "…"


def _n(value: float) -> str:
    if abs(value - round(value)) < 0.05:
        return str(int(round(value)))
    return f"{value:.1f}"


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
    if sweep >= math.tau - 1e-3:
        return _slice(cx, cy, outer, 0, math.tau)
    large = 1 if sweep > math.pi else 0
    x0, y0 = _xy(cx, cy, outer, start)
    x1, y1 = _xy(cx, cy, outer, end)
    i0, j0 = _xy(cx, cy, inner, start)
    i1, j1 = _xy(cx, cy, inner, end)
    return (
        f"M {x0:.2f} {y0:.2f} A {outer:.2f} {outer:.2f} 0 {large} 1 {x1:.2f} {y1:.2f} "
        f"L {i1:.2f} {j1:.2f} A {inner:.2f} {inner:.2f} 0 {large} 0 {i0:.2f} {j0:.2f} Z"
    )
