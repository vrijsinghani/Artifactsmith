"""Sequence diagrams: actors across the top, messages downward.

Column gaps grow with the label that crosses them, so the words sit on the
shaft instead of being crushed. That idea is from answer-me-with-html (MIT),
https://github.com/QingYunA/answer-me-with-html (src/components/sequence.js).
This layout is original Python. When the row of actors would be wider than a
phone column, messages stack instead.
"""

from __future__ import annotations

from .chart_scale import text_width
from .diagram_parse import Sequence, Step
from .diagram_svg import FONT, LINE, aria, arrow, chip, fits, line, rect, svg_doc, text_at, wrap_lines


def draw_sequence(seq: Sequence) -> str:
    for limit in (210.0, 150.0, 100.0):
        laid = _classic(seq, limit)
        if laid is not None and fits(laid[0]):
            return laid[1]
    return _stack(seq)


def _classic(seq: Sequence, label_limit: float) -> tuple[float, str] | None:
    count = len(seq.actors)
    index = {name: at for at, name in enumerate(seq.actors)}
    actor_w = [max(88.0, min(160.0, text_width(name, FONT) + 28)) for name in seq.actors]
    gaps = [actor_w[at] / 2 + actor_w[at + 1] / 2 + 36 for at in range(max(0, count - 1))]
    prepared: list[tuple[Step, list[str], str]] = []
    extra_right = 0.0
    seen_msg = 0
    for step in seq.steps:
        if step.kind != "msg":
            lines, full = wrap_lines(step.label, label_limit, FONT, max_lines=3)
            prepared.append((step, lines, full))
            continue
        seen_msg += 1
        shown = f"{seen_msg} {step.label}".strip() if seq.numbered else step.label
        lines, full = wrap_lines(shown, label_limit, FONT, max_lines=2)
        prepared.append((step, lines, full))
        label_w = max((text_width(line, FONT) for line in lines), default=0.0) + 20
        src, dst = index[step.src], index[step.dst]
        if src == dst:
            extra_right = max(extra_right, label_w + 24)
            continue
        lo, hi = min(src, dst), max(src, dst)
        span = sum(gaps[lo:hi])
        if span < label_w and gaps:
            gaps[hi - 1] += label_w - span
    xs = [18 + actor_w[0] / 2]
    for gap in gaps:
        xs.append(xs[-1] + gap)
    width = xs[-1] + actor_w[-1] / 2 + 18 + extra_right
    for step, lines, _full in prepared:
        if step.kind != "note" or not lines or not step.over:
            continue
        text_w = max(text_width(line, FONT) for line in lines) + 20
        indexes = [index[name] for name in step.over if name in index]
        if not indexes:
            continue
        lo, hi = min(indexes), max(indexes)
        cx = (xs[lo] + xs[hi]) / 2
        width = max(width, cx + text_w / 2 + 10, text_w + 16)
    if not fits(width):
        return None
    parts: list[str] = []
    y = 10.0
    for at, name in enumerate(seq.actors):
        parts.append(rect(xs[at] - actor_w[at] / 2, y, actor_w[at], 34, "box", rx=8))
        parts.append(text_at(xs[at], y + 17, name, FONT, anchor="middle", weight="700"))
    lifeline_top = y + 34
    y = lifeline_top + 18
    for step, lines, full in prepared:
        if step.kind == "divider":
            parts.append(text_at(width / 2, y + 8, step.label, FONT, anchor="middle", weight="700"))
            y += 28
        elif step.kind == "note":
            y = _note(parts, step, lines, full, xs, index, y)
        else:
            y = _message(parts, step, lines, full, xs, index, y)
    height = y + 14
    for at in range(count):
        parts.insert(0, line(xs[at], lifeline_top, xs[at], height - 10, "wire"))
    bits: list[str] = []
    for step in seq.steps:
        if step.kind == "msg":
            bits.append(f"{step.src} to {step.dst}" + (f", {step.label}" if step.label else ""))
        elif step.label:
            bits.append(step.label)
    return width, svg_doc(width, height, aria("sequence", bits[:12]), parts)


def _note(
    parts: list[str],
    step: Step,
    lines: list[str],
    full: str,
    xs: list[float],
    index: dict[str, int],
    y: float,
) -> float:
    indexes = [index[name] for name in step.over if name in index]
    if not indexes or not lines:
        return y
    lo, hi = min(indexes), max(indexes)
    text_w = max(text_width(line, FONT) for line in lines) + 20
    cx = (xs[lo] + xs[hi]) / 2
    width = max(text_w, xs[hi] - xs[lo] + 36)
    left = max(8.0, cx - width / 2)
    height = len(lines) * LINE + 14
    parts.append(rect(left, y, width, height, "note", rx=6))
    start = y + height / 2 - (len(lines) - 1) * LINE / 2
    for at, shown in enumerate(lines):
        parts.append(
            text_at(left + width / 2, start + at * LINE, shown, FONT, anchor="middle", full=full if at == 0 else "")
        )
    return y + height + 14


def _message(
    parts: list[str],
    step: Step,
    lines: list[str],
    full: str,
    xs: list[float],
    index: dict[str, int],
    y: float,
) -> float:
    src, dst = index[step.src], index[step.dst]
    if lines:
        anchor_x = xs[src] + 40 if src == dst else (xs[src] + xs[dst]) / 2
        for at, shown in enumerate(lines):
            parts.append(chip(anchor_x, y, shown, FONT, full=full if at == 0 else ""))
            y += LINE
        y += 4
    if src == dst:
        parts.append(arrow(xs[src], y, xs[src] + 26, y, dashed=step.dashed))
        return y + 26
    start = xs[src] + (7 if xs[dst] > xs[src] else -7)
    end = xs[dst] - (10 if xs[dst] > xs[src] else -10)
    parts.append(arrow(start, y, end, y, dashed=step.dashed))
    return y + 22


def _stack(seq: Sequence) -> str:
    """One message per row, full width. Used when actor columns cannot fit."""
    width = 360.0 if fits(360) else 320.0
    parts: list[str] = []
    y = 12.0
    box_w = min(120.0, (width - 48) / 2)
    for number, step in enumerate((item for item in seq.steps if item.kind == "msg"), start=1):
        label = f"{number} {step.label}".strip() if seq.numbered else step.label
        lines, full = wrap_lines(label, width - 24, FONT, max_lines=2)
        parts.append(rect(8, y, box_w, 32, "box", rx=6))
        parts.append(text_at(8 + box_w / 2, y + 16, step.src, FONT, anchor="middle", weight="700"))
        parts.append(rect(width - 8 - box_w, y, box_w, 32, "box", rx=6))
        parts.append(text_at(width - 8 - box_w / 2, y + 16, step.dst, FONT, anchor="middle", weight="700"))
        parts.append(arrow(8 + box_w + 4, y + 16, width - 12 - box_w, y + 16, dashed=step.dashed))
        y += 36
        for at, shown in enumerate(lines):
            parts.append(text_at(width / 2, y, shown, FONT, anchor="middle", full=full if at == 0 else ""))
            y += LINE
        y += 8
    if y <= 20:
        y = 36
        parts.append(text_at(12, 20, seq.actors[0], FONT))
    bits = [f"{step.src} to {step.dst}, {step.label}".strip(", ") for step in seq.steps if step.kind == "msg"]
    return svg_doc(width, y + 8, aria("sequence", bits[:12]), parts)
