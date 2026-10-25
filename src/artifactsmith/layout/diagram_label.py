"""Place flow labels in open space, then draw the shafts underneath the nodes."""

from __future__ import annotations

from dataclasses import dataclass

from .chart_scale import text_width
from .diagram_parse import Flow, FlowEdge, FlowNode
from .diagram_route import route_edge
from .diagram_svg import FONT, LIMIT, LINE, arrow, chip, line, rect, text_at, wrap_lines

Box = tuple[float, float, float, float]
NodeDraw = tuple[Box, FlowNode, list[str], str]
Rect = tuple[float, float, float, float]


def draw_edges(
    flow: Flow,
    boxes: dict[str, NodeDraw],
    lane: float,
) -> tuple[float, float, list[str], list[str]]:
    marks = _marks(flow, boxes, lane)
    width, height = _bounds(boxes, marks)
    shafts = [_stroke(mark.points, mark.dashed) for mark in marks]
    labels = [_paint(mark) for mark in marks if mark.lines]
    return width, height, shafts, labels


@dataclass
class _Mark:
    points: list[tuple[float, float]]
    dashed: bool
    lines: list[str]
    full: str
    x: float
    y: float
    anchor: str
    w: float
    h: float

    def rect(self) -> Rect:
        left = self.x - self.w / 2 if self.anchor == "middle" else self.x
        return left, self.y - FONT * 0.7, self.w, self.h

    def move_to(self, left: float, top: float) -> None:
        self.x = left + (self.w / 2 if self.anchor == "middle" else 0.0)
        self.y = top + FONT * 0.7


def _marks(flow: Flow, boxes: dict[str, NodeDraw], lane: float) -> list[_Mark]:
    rights = [box[0] + box[2] / 2 for box, *_rest in boxes.values()]
    gutter = (max(rights) if rights else 12.0) + 16
    marks: list[_Mark] = []
    for edge in flow.edges:
        if edge.src not in boxes or edge.dst not in boxes:
            continue
        src = _rect_of(boxes[edge.src][0])
        dst = _rect_of(boxes[edge.dst][0])
        others = [_rect_of(box) for name, (box, *_rest) in boxes.items() if name not in {edge.src, edge.dst}]
        points = route_edge(edge, src, dst, others, gutter)
        marks.append(_mark(edge, points, gutter, lane))
    _spread(marks, [_rect_of(box) for box, *_rest in boxes.values()])
    return marks


def _mark(edge: FlowEdge, points: list[tuple[float, float]], gutter: float, lane: float) -> _Mark:
    dashed = edge.dashed or edge.back
    if not edge.label:
        return _Mark(points, dashed, [], "", 0, 0, "middle", 0, 0)
    spot, anchor, room = _spot(points, gutter, lane)
    lines, full = wrap_lines(edge.label, room, FONT, max_lines=2)
    if not lines:
        return _Mark(points, dashed, [], "", 0, 0, "middle", 0, 0)
    width = max(text_width(shown, FONT) for shown in lines) + 8
    height = (len(lines) - 1) * LINE + FONT + 6
    return _Mark(points, dashed, lines, full, spot[0], spot[1], anchor, width, height)


def _spot(points: list[tuple[float, float]], gutter: float, lane: float) -> tuple[tuple[float, float], str, float]:
    best = points[0]
    best_len = -1.0
    gutter_at: tuple[float, float] | None = None
    for start, end in zip(points, points[1:], strict=False):
        length = abs(end[0] - start[0]) + abs(end[1] - start[1])
        if length > best_len:
            best_len = length
            best = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
        if abs(start[0] - gutter) < 1 and abs(end[0] - gutter) < 1 and length > 20:
            gutter_at = (gutter + 6, (start[1] + end[1]) / 2)
    if gutter_at is not None and lane >= 48:
        return gutter_at, "start", max(48.0, lane - 16)
    return best, "middle", 160.0


def _spread(marks: list[_Mark], nodes: list[Rect]) -> None:
    labeled = [mark for mark in marks if mark.w]
    for _ in range(12):
        moved = False
        for index, mark in enumerate(labeled):
            left, top, width, height = mark.rect()
            for other in labeled[index + 1 :]:
                ole, otop, owidth, oheight = other.rect()
                if _overlap(left, top, width, height, ole, otop, owidth, oheight, 3):
                    other.move_to(ole, top + height + 4)
                    moved = True
            for node in nodes:
                left, top, width, height = mark.rect()
                if not _overlap(left, top, width, height, *node, 2):
                    continue
                _nx, ny, _nw, nh = node
                below = top + height / 2 >= ny + nh / 2
                mark.move_to(left, ny + nh + 4 if below else ny - height - 4)
                if mark.rect()[1] < 4:
                    mark.move_to(left, ny + nh + 4)
                moved = True
        if not moved:
            break
    for mark in labeled:
        left, top, width, _height = mark.rect()
        if left < 4:
            mark.move_to(4, top)
            left = 4
        if left + width > LIMIT - 4:
            mark.move_to(max(4.0, LIMIT - 4 - width), top)


def _overlap(
    left: float,
    top: float,
    width: float,
    height: float,
    other_left: float,
    other_top: float,
    other_width: float,
    other_height: float,
    gap: float,
) -> bool:
    return (
        left < other_left + other_width + gap
        and other_left < left + width + gap
        and top < other_top + other_height + gap
        and other_top < top + height + gap
    )


def _rect_of(box: Box) -> Rect:
    cx, cy, width, height = box
    return cx - width / 2, cy - height / 2, width, height


def _bounds(boxes: dict[str, NodeDraw], marks: list[_Mark]) -> tuple[float, float]:
    right, bottom = 12.0, 12.0
    for box, *_rest in boxes.values():
        cx, cy, width, height = box
        right = max(right, cx + width / 2)
        bottom = max(bottom, cy + height / 2)
    for mark in marks:
        for x, y in mark.points:
            right = max(right, x)
            bottom = max(bottom, y)
        if mark.w:
            left, top, width, height = mark.rect()
            right = max(right, left + width)
            bottom = max(bottom, top + height)
    return right + 12, bottom + 12


def _stroke(points: list[tuple[float, float]], dashed: bool) -> str:
    clean = [points[0]]
    for point in points[1:]:
        if abs(point[0] - clean[-1][0]) > 0.5 or abs(point[1] - clean[-1][1]) > 0.5:
            clean.append(point)
    if len(clean) < 2:
        return ""
    css = "edge dash" if dashed else "edge"
    parts = [line(x1, y1, x2, y2, css) for (x1, y1), (x2, y2) in zip(clean, clean[1:-1], strict=False)]
    x1, y1 = clean[-2]
    x2, y2 = clean[-1]
    parts.append(arrow(x1, y1, x2, y2, dashed=dashed))
    return "".join(parts)


def _paint(mark: _Mark) -> str:
    if len(mark.lines) == 1:
        return chip(mark.x, mark.y, mark.lines[0], FONT, anchor=mark.anchor, full=mark.full)
    left, top, width, height = mark.rect()
    body = [rect(left, top, width, height, "tag", rx=3)]
    for index, shown in enumerate(mark.lines):
        body.append(
            text_at(
                mark.x,
                mark.y + index * LINE,
                shown,
                FONT,
                anchor=mark.anchor,
                full=mark.full if index == 0 else "",
            )
        )
    return "".join(body)
