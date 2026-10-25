"""Tick marks, category labels, and gaps for server-drawn charts.

Tick steps are 1, 2, 2.5, or 5 times a power of ten. A line axis includes 0
when the data reaches it, or when the smallest value is at most a third of the
largest, because a line encodes position rather than length. That readable-axis
rule is adapted from answer-me-with-html (MIT),
https://github.com/QingYunA/answer-me-with-html (src/components/chart-scale.js).
The Python is not a copy of that file.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

# A phone content column is about this many CSS pixels. User-unit font must
# stay at least MIN_FONT_PX when the drawing is squeezed to that width.
NARROW_PX = 320.0
MIN_FONT_PX = 11.0
MAX_TICKS = 6
_MANTISSAS = (1.0, 2.0, 2.5, 5.0)


@dataclass(frozen=True)
class Scale:
    """Inclusive axis from ``lo`` to ``hi`` with ``ticks`` on ``step``."""

    lo: float
    hi: float
    step: float
    ticks: tuple[float, ...]


@dataclass(frozen=True)
class LabelMark:
    """One category label. ``shown`` is empty when the label is thinned out."""

    shown: str
    full: str
    rotate: bool


def finite_values(values: Sequence[float | None]) -> list[float]:
    return [float(value) for value in values if value is not None and math.isfinite(value)]


def point_runs(values: Sequence[float | None]) -> list[list[int]]:
    """Indexes of contiguous finite points. A missing point ends the run."""
    runs: list[list[int]] = []
    current: list[int] = []
    for index, value in enumerate(values):
        if value is None or not math.isfinite(value):
            if current:
                runs.append(current)
                current = []
            continue
        current.append(index)
    if current:
        runs.append(current)
    return runs


def bar_scale(values: Sequence[float | None]) -> Scale:
    """A bar axis starts at 0 and ends on a tick at or beyond the data."""
    finite = finite_values(values)
    if not finite:
        return Scale(0.0, 1.0, 1.0, (0.0, 1.0))
    lo = min(0.0, min(finite))
    hi = max(0.0, max(finite))
    return _cover(lo, hi, finite)


def line_scale(values: Sequence[float | None]) -> Scale:
    """A line axis may start above 0 so a narrow band is not drawn flat."""
    finite = finite_values(values)
    if not finite:
        return Scale(0.0, 1.0, 1.0, (0.0, 1.0))
    lo, hi = min(finite), max(finite)
    if lo <= 0 or hi == 0 or lo <= hi / 3:
        return bar_scale(finite)
    if lo == hi:
        pad = max(abs(lo) * 0.25, 1.0)
        return _cover(lo - pad, hi + pad, finite)
    return _cover(lo, hi, finite)


def format_value(value: float, unit: str) -> str:
    """One number plus its unit, for a value label or a screen-reader summary.

    Non-integers keep at least two significant figures, and the text rounds
    back to the same value. ``0.018`` stays ``0.018``; it does not become ``0.02``.
    """
    if not math.isfinite(value):
        return ""
    if value == int(value) and abs(value) < 1e15:
        number = int(value)
        # A bare year is not a thousands-grouped quantity ("1998", never "1,998").
        shown = str(number) if unit == "" and 1900 <= number <= 2100 else f"{number:,}"
    else:
        places = _places_for(value)
        sign = "-" if value < 0 else ""
        shown = sign + f"{abs(value):,.{places}f}"
    return _with_unit(shown, unit)


def format_tick(value: float, unit: str, step: float) -> str:
    """A tick label. Decimals follow the step so 4.0 and 4.2 line up."""
    places = _decimals(step)
    rounded = _round(value)
    if places == 0:
        number = int(round(rounded))
        shown = str(number) if unit == "" and 1900 <= number <= 2100 else f"{number:,}"
    else:
        shown = f"{rounded:,.{places}f}"
    return _with_unit(shown, unit)


def text_width(text: str, font: float) -> float:
    """Estimated advance width in user units. Slightly wide, so labels keep a gap."""
    units = 0.0
    for ch in text:
        if ch == " ":
            units += 0.33
        elif ch in "iljtfI.,:;|'!":
            units += 0.32
        elif ch in "mwMW@%":
            units += 0.86
        elif "A" <= ch <= "Z":
            units += 0.68
        else:
            units += 0.56
    return units * font


def plan_category_labels(labels: Sequence[str], slot: float, font: float) -> list[LabelMark]:
    """Show every label that fits. Otherwise thin, slant, then truncate.

    ``slot`` is the center-to-center gap in user units. A truncated label keeps
    the full string on ``LabelMark.full`` so the drawing can put it in ``<title>``.
    The last label is kept when it still clears the previous shown label.
    """
    count = len(labels)
    if count == 0:
        return []
    slot = max(slot, 1.0)
    for step in range(1, count + 1):
        room = step * slot - 4
        if room < font:
            continue
        if all(text_width(labels[index], font) <= room for index in range(0, count, step)):
            return _marks(labels, step, slot, font, rotate=False, limit=room)
    cos = math.cos(math.radians(40))
    sin = math.sin(math.radians(40))
    limit = max(slot * 6, font * 8)
    for step in range(1, count + 1):
        gap = step * slot
        if gap * sin < font * 1.05:
            continue
        shown = [_truncate(labels[index], limit, font) for index in range(0, count, step)]
        if all(text_width(text, font) * cos <= gap - 1 for text in shown):
            return _marks(labels, step, slot, font, rotate=True, limit=limit)
    return _marks(labels, max(1, count - 1), slot, font, rotate=True, limit=max(font * 4, slot * 3))


def prefers_horizontal(labels: Sequence[str]) -> bool:
    """Vertical bars collide once names are long or there are many of them."""
    if len(labels) > 6:
        return True
    return any(len(label) > 16 for label in labels)


def chart_width_budget(font: float) -> float:
    """Widest viewBox that still shows ``font`` at ``MIN_FONT_PX`` on a phone.

    Growing the font grows the drawing with it, so the phone still shrinks the
    text. The drawing stays inside this width instead, and gets taller when it
    needs room.
    """
    if font <= 0:
        return NARROW_PX
    return font * NARROW_PX / MIN_FONT_PX


def keep_tick_labels(positions: Sequence[float], sizes: Sequence[float], pad: float = 4.0) -> list[bool]:
    """Which tick labels fit along one axis. A label that would collide is dropped.

    The first label stays. The last stays when it clears the label before it.
    ``sizes`` are the label lengths along the axis (width for a horizontal axis,
    height for a vertical one).
    """
    count = len(positions)
    if count == 0:
        return []
    keep = [False] * count
    last = 0
    keep[0] = True
    for index in range(1, count):
        if _apart(positions[last], sizes[last], positions[index], sizes[index], pad):
            keep[index] = True
            last = index
    if count > 1 and not keep[-1]:
        keep[-1] = True
        for index in range(count - 2, 0, -1):
            if not keep[index]:
                continue
            if _apart(positions[index], sizes[index], positions[-1], sizes[-1], pad):
                break
            keep[index] = False
        if not _apart(positions[0], sizes[0], positions[-1], sizes[-1], pad):
            keep[-1] = False
    return keep


def _cover(lo: float, hi: float, values: Sequence[float]) -> Scale:
    if hi < lo:
        lo, hi = hi, lo
    span = max(hi - lo, 1e-9)
    whole = all(value == int(value) for value in values)
    chosen = 1.0
    for step in _steps(span, whole):
        chosen = step
        if _tick_count(lo, hi, step) <= MAX_TICKS:
            break
    start = _snap_down(lo, chosen)
    stop = _snap_up(hi, chosen)
    if stop <= start:
        stop = _round(start + chosen)
    count = max(1, int(round((stop - start) / chosen)))
    ticks = tuple(_round(start + index * chosen) for index in range(count + 1))
    return Scale(ticks[0], ticks[-1], chosen, ticks)


def _steps(span: float, whole: bool) -> Iterator[float]:
    power = math.floor(math.log10(span)) - 2
    for _ in range(16):
        for mantissa in _MANTISSAS:
            step = _round(mantissa * 10**power)
            if step <= 0:
                continue
            if whole and step != int(step):
                continue
            yield step
        power += 1


def _tick_count(lo: float, hi: float, step: float) -> int:
    return int(round((_snap_up(hi, step) - _snap_down(lo, step)) / step)) + 1


def _snap_down(value: float, step: float) -> float:
    return _round(math.floor(value / step + 1e-9) * step)


def _snap_up(value: float, step: float) -> float:
    return _round(math.ceil(value / step - 1e-9) * step)


def _round(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    if abs(value) >= 1e15:
        return float(value)
    rounded = round(value, 6)
    if rounded == int(rounded):
        return float(int(rounded))
    return rounded


def _places_for(value: float) -> int:
    """Decimal places for at least two significant figures, without a false rounding."""
    abs_value = abs(value)
    if abs_value == 0 or abs_value >= 1e15:
        return 0
    exponent = math.floor(math.log10(abs_value) + 1e-10)
    start = max(0, 2 - 1 - exponent)
    for places in range(start, 7):
        shown = float(f"{value:.{places}f}")
        if abs(shown - value) <= 10 ** (-(places + 1)):
            return places
    return 6


def _apart(left: float, left_size: float, right: float, right_size: float, pad: float) -> bool:
    return abs(right - left) >= (left_size + right_size) / 2 + pad


def _decimals(step: float) -> int:
    text = f"{_round(step):.6f}".rstrip("0").rstrip(".")
    if "." not in text:
        return 0
    return min(4, len(text.split(".", 1)[1]))


def _with_unit(shown: str, unit: str) -> str:
    if unit in {"$", "€", "£", "¥"}:
        sign = ""
        body = shown
        if shown.startswith("-"):
            sign, body = "-", shown[1:]
        return f"{sign}{unit}{body}"
    if unit == "%":
        return f"{shown}{unit}"
    if unit:
        return f"{shown} {unit}"
    return shown


def _truncate(text: str, limit: float, font: float) -> str:
    if text_width(text, font) <= limit:
        return text
    chars = list(text)
    while len(chars) > 1 and text_width("".join(chars) + "…", font) > limit:
        chars.pop()
    trimmed = "".join(chars).rstrip()
    return (trimmed or text[:1]) + "…"


def _marks(
    labels: Sequence[str],
    step: int,
    slot: float,
    font: float,
    *,
    rotate: bool,
    limit: float,
) -> list[LabelMark]:
    marks: list[LabelMark] = []
    last = len(labels) - 1
    for index, label in enumerate(labels):
        show = index % step == 0
        if index == last and not show:
            show = _last_fits(marks, label, index, slot, font, rotate, limit)
        if not show:
            marks.append(LabelMark("", label, rotate))
            continue
        shown = label if not rotate else _truncate(label, limit, font)
        if not rotate and text_width(shown, font) > limit:
            shown = _truncate(label, limit, font)
        marks.append(LabelMark(shown, label, rotate))
    return marks


def _last_fits(
    marks: list[LabelMark],
    label: str,
    index: int,
    slot: float,
    font: float,
    rotate: bool,
    limit: float,
) -> bool:
    previous = max((i for i, mark in enumerate(marks) if mark.shown), default=-1)
    if previous < 0:
        return True
    gap = (index - previous) * slot
    shown = _truncate(label, limit, font) if rotate else label
    reach = text_width(shown, font) * (math.cos(math.radians(40)) if rotate else 1)
    return reach <= gap - 1
