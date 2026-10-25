"""Bend a flow edge so it does not cut through a node.

A short hop drops through the open gap. An edge with a node in between goes
down the right margin instead, so it does not land on the same side as the
short hop.
"""

from __future__ import annotations

from .diagram_parse import FlowEdge

Rect = tuple[float, float, float, float]


def route_edge(
    edge: FlowEdge,
    src: Rect,
    dst: Rect,
    others: list[Rect],
    gutter: float,
) -> list[tuple[float, float]]:
    if edge.src == edge.dst:
        right, mid = src[0] + src[2], src[1] + src[3] / 2
        return [(right, mid - 6), (right + 16, mid - 6), (right + 16, mid + 8), (right, mid + 8)]
    downward = _down_path(src, dst, others)
    if downward is not None:
        return downward
    across = _across_path(src, dst, others)
    if across is not None:
        return across
    return _gutter_path(src, dst, others, gutter)


def _down_path(src: Rect, dst: Rect, others: list[Rect]) -> list[tuple[float, float]] | None:
    left, top, width, height = src
    dest_left, dest_top, dest_w, _dest_h = dst
    start_x, start_y = left + width / 2, top + height
    dest_x = dest_left + dest_w / 2
    if dest_top < start_y - 1 or _between(start_y, dest_top, others):
        return None
    straight = [(start_x, start_y), (dest_x, dest_top)]
    if abs(start_x - dest_x) <= 24 and not _blocked(straight, others):
        return straight
    # Low in the gap, so a label on the straight edge above it keeps its own row.
    mid = dest_top - 20 if dest_top - 20 > start_y + 14 else (start_y + dest_top) / 2
    elbow = [(start_x, start_y), (start_x, mid), (dest_x, mid), (dest_x, dest_top)]
    if not _blocked(elbow, others):
        return elbow
    return None


def _across_path(src: Rect, dst: Rect, others: list[Rect]) -> list[tuple[float, float]] | None:
    left, top, width, height = src
    dest_left, dest_top, _dest_w, dest_h = dst
    start_x, start_y = left + width, top + height / 2
    dest_y = dest_top + dest_h / 2
    if dest_left < start_x - 1:
        return None
    straight = [(start_x, start_y), (dest_left, dest_y)]
    if abs(start_y - dest_y) <= 18 and not _blocked(straight, others):
        return straight
    mid = (start_x + dest_left) / 2
    elbow = [(start_x, start_y), (mid, start_y), (mid, dest_y), (dest_left, dest_y)]
    if not _blocked(elbow, others):
        return elbow
    return None


def _gutter_path(src: Rect, dst: Rect, others: list[Rect], gutter: float) -> list[tuple[float, float]]:
    left, top, width, height = src
    dest_left, dest_top, dest_w, dest_h = dst
    start_y = top + height / 2
    dest_y = dest_top + dest_h / 2
    direct = [(left + width, start_y), (gutter, start_y), (gutter, dest_y), (dest_left + dest_w, dest_y)]
    if not _blocked(direct, others):
        return direct
    above = top - 16
    lifted = [
        (left + width / 2, top),
        (left + width / 2, above),
        (gutter, above),
        (gutter, dest_y),
        (dest_left + dest_w, dest_y),
    ]
    if above > 8 and not _blocked(lifted, others):
        return lifted
    return direct


def _between(start_y: float, dest_top: float, others: list[Rect]) -> bool:
    if dest_top <= start_y + 4:
        return False
    return any(top + height > start_y + 4 and top < dest_top - 4 for _left, top, _width, height in others)


def _blocked(points: list[tuple[float, float]], rects: list[Rect]) -> bool:
    for start, end in zip(points, points[1:], strict=False):
        if any(_hits(*start, *end, rect) for rect in rects):
            return True
    return False


def _hits(x1: float, y1: float, x2: float, y2: float, rect: Rect) -> bool:
    left, top, width, height = rect
    right, bottom = left + width, top + height
    if max(x1, x2) < left or min(x1, x2) > right or max(y1, y2) < top or min(y1, y2) > bottom:
        return False
    if abs(x1 - x2) <= 0.8 or abs(y1 - y2) <= 0.8:
        return True
    return _crosses(x1, y1, x2, y2, left, top, right, bottom)


def _crosses(x1: float, y1: float, x2: float, y2: float, left: float, top: float, right: float, bottom: float) -> bool:
    dx, dy = x2 - x1, y2 - y1
    params = ((-dx, x1 - left), (dx, right - x1), (-dy, y1 - top), (dy, bottom - y1))
    enter, leave = 0.0, 1.0
    for pi, qi in params:
        if abs(pi) < 1e-9:
            if qi < 0:
                return False
            continue
        t = qi / pi
        if pi < 0:
            enter = max(enter, t)
        else:
            leave = min(leave, t)
        if enter > leave:
            return False
    return True
