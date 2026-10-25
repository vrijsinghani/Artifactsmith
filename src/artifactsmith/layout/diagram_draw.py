"""Lay out a flow inside the phone column.

Left to right when the row fits. Otherwise top to bottom, and a band that is
still too wide wraps onto the next line. An edge that would cross a node goes
around it, and its label sits in open space.
"""

from __future__ import annotations

from collections import defaultdict

from .chart_scale import text_width
from .diagram_label import draw_edges
from .diagram_parse import Flow, FlowNode, Sequence, Timeline, Tree
from .diagram_seq import draw_sequence
from .diagram_span import draw_timeline, draw_tree
from .diagram_svg import FONT, LIMIT, LINE, aria, fits, poly, rect, svg_doc, text_at, wrap_lines

Box = tuple[float, float, float, float]  # cx, cy, w, h


def draw_diagram(kind: str, model: Flow | Sequence | Timeline | Tree) -> str:
    if isinstance(model, Flow):
        return _flow(model)
    if isinstance(model, Sequence):
        return draw_sequence(model)
    if isinstance(model, Timeline):
        return draw_timeline(model)
    return draw_tree(model)


def _flow(flow: Flow) -> str:
    if flow.direction == "LR":
        wide = _flow_layout(flow, "LR")
        if fits(wide[0]):
            return wide[1]
    return _flow_layout(flow, "TB")[1]


def _ranks(flow: Flow) -> dict[str, int]:
    dead: set[int] = set()
    rank = {node.name: 0 for node in flow.nodes}
    limit = max(len(flow.nodes), 1)
    while True:
        rank = {node.name: 0 for node in flow.nodes}
        restart = False
        for _ in range(limit):
            moved = False
            for index, edge in enumerate(flow.edges):
                if index in dead:
                    continue
                nxt = rank[edge.src] + 1
                if nxt > limit:
                    dead.add(index)
                    edge.back = True
                    restart = True
                    break
                if rank[edge.dst] < nxt:
                    rank[edge.dst] = nxt
                    moved = True
            if restart or not moved:
                break
        if not restart:
            break
    for index, edge in enumerate(flow.edges):
        if index in dead or rank[edge.dst] <= rank[edge.src]:
            edge.back = True
    return rank


def _node_size(node: FlowNode, limit: float) -> tuple[list[str], str, float, float]:
    lines, full = wrap_lines(node.name, max(48.0, limit), FONT, max_lines=3)
    if not lines:
        lines = [node.name[:20]]
    width = max(text_width(line, FONT) for line in lines) + 24
    height = max(36.0, len(lines) * LINE + 14)
    if node.shape == "diamond":
        width, height = max(width * 1.3, 92), max(height * 1.15, 52)
    elif node.shape == "pill":
        width += 8
    return lines, full, min(width, LIMIT - 24), height


# Room under a node for one label, and a second label stacked under it.
_BAND = 88.0
_WRAP = 56.0


def _flow_layout(flow: Flow, direction: str) -> tuple[float, str]:
    rank = _ranks(flow)
    columns: dict[int, list[FlowNode]] = defaultdict(list)
    for node in flow.nodes:
        columns[rank[node.name]].append(node)
    keys = sorted(columns)
    boxes: dict[str, tuple[Box, FlowNode, list[str], str]] = {}
    lane = _lane(flow, rank)
    if direction == "LR":
        _flow_lr(flow, rank, keys, columns, boxes)
    else:
        _flow_tb(keys, columns, boxes, lane)
    width, height, shafts, labels = draw_edges(flow, boxes, lane)
    parts = [*shafts]
    parts.extend(_draw_node(box, node, lines, full) for box, node, lines, full in boxes.values())
    parts.extend(labels)
    bits = [f"{edge.src} to {edge.dst}" + (f" ({edge.label})" if edge.label else "") for edge in flow.edges[:12]]
    return width, svg_doc(width, height, aria("flow", bits), parts)


def _lane(flow: Flow, rank: dict[str, int]) -> float:
    """Right margin for an edge that skips a rank. The label has to fit there."""
    extra = 0.0
    for edge in flow.edges:
        if edge.src == edge.dst or edge.src not in rank or edge.dst not in rank:
            continue
        if rank[edge.dst] > rank[edge.src] + 1 or rank[edge.dst] <= rank[edge.src]:
            extra = max(extra, text_width(edge.label, FONT) + 8 if edge.label else 16)
    if extra <= 0:
        return 0.0
    return min(extra + 20, 170)


def _flow_lr(
    flow: Flow,
    rank: dict[str, int],
    keys: list[int],
    columns: dict[int, list[FlowNode]],
    boxes: dict[str, tuple[Box, FlowNode, list[str], str]],
) -> None:
    gaps = [_pair_gap(flow, rank, src, dst) for src, dst in zip(keys, keys[1:], strict=False)]
    room = max(64.0, (LIMIT - 24 - sum(gaps)) / max(len(keys), 1) - 24)
    cursor = 12.0
    bottom = 12.0
    staged: list[tuple[list[tuple[FlowNode, list[str], str, float, float, float]], float, float]] = []
    for key in keys:
        y = 12.0
        col_w = 0.0
        items: list[tuple[FlowNode, list[str], str, float, float, float]] = []
        for node in columns[key]:
            lines, full, width, height = _node_size(node, room)
            items.append((node, lines, full, width, height, y))
            y += height + 22
            col_w = max(col_w, width)
        staged.append((items, col_w, y))
        bottom = max(bottom, y)
    for index, (items, col_w, y) in enumerate(staged):
        extra = (bottom - y) / 2
        cx = cursor + col_w / 2
        for node, lines, full, width, height, top in items:
            cy = top + extra + height / 2
            boxes[node.name] = ((cx, cy, width, height), node, lines, full)
        cursor += col_w
        if index < len(gaps):
            cursor += gaps[index]


def _pair_gap(flow: Flow, rank: dict[str, int], src_rank: int, dst_rank: int) -> float:
    labels = [
        edge.label
        for edge in flow.edges
        if edge.label and rank.get(edge.src) == src_rank and rank.get(edge.dst) == dst_rank
    ]
    if not labels:
        return 36.0
    return max(36.0, max(text_width(label, FONT) + 8 for label in labels) + 18)


def _flow_tb(
    keys: list[int],
    columns: dict[int, list[FlowNode]],
    boxes: dict[str, tuple[Box, FlowNode, list[str], str]],
    lane: float,
) -> None:
    """One rank per band. A band that passes the phone column wraps."""
    stop = LIMIT - 16 - lane
    x = 12.0
    y = 12.0
    row_h = 0.0
    for key in keys:
        if x > 12:
            y += row_h + _BAND
            x = 12.0
            row_h = 0.0
        for node in columns[key]:
            lines, full, width, height = _node_size(node, min(168.0, stop - 24))
            if x > 12 and x + width > stop:
                y += row_h + _WRAP
                x = 12.0
                row_h = 0.0
            cx, cy = x + width / 2, y + height / 2
            boxes[node.name] = ((cx, cy, width, height), node, lines, full)
            x += width + 18
            row_h = max(row_h, height)


def _draw_node(box: Box, node: FlowNode, lines: list[str], full: str) -> str:
    cx, cy, width, height = box
    css = "box hi" if node.hi else "box"
    if node.shape == "diamond":
        shape = poly([(cx, cy - height / 2), (cx + width / 2, cy), (cx, cy + height / 2), (cx - width / 2, cy)], css)
    else:
        rx = height / 2 if node.shape == "pill" else 8
        shape = rect(cx - width / 2, cy - height / 2, width, height, css, rx=rx)
    block = len(lines) * LINE
    start = cy - block / 2 + LINE / 2
    weight = "700" if node.hi else ""
    texts = [
        text_at(cx, start + index * LINE, shown, FONT, anchor="middle", full=full if index == 0 else "", weight=weight)
        for index, shown in enumerate(lines)
    ]
    return shape + "".join(texts)
