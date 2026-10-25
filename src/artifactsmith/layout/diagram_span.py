"""Timeline and tree diagrams.

A timeline is a row of dated events when that row fits the phone column, and a
vertical spine otherwise. A tree is an indented folder, or a small org chart
when one root has a handful of leaves that fit.
"""

from __future__ import annotations

from .chart_scale import text_width
from .diagram_parse import Timeline, Tree, TreeNode
from .diagram_svg import FONT, LIMIT, LINE, aria, fits, line, rect, svg_doc, text_at, wrap_lines


def draw_timeline(spec: Timeline) -> str:
    if spec.force != "v":
        horizontal = _horizontal(spec)
        if horizontal is not None:
            return horizontal
    return _vertical(spec)


def _horizontal(spec: Timeline) -> str | None:
    count = len(spec.events)
    date_w = max(text_width(event.when, FONT) for event in spec.events)
    col = date_w + 18
    if count * col > LIMIT - 8:
        return None
    room = (LIMIT - 8) / count
    col = max(col, min(room, 120.0))
    if not fits(count * col + 8):
        return None
    text_room = col - 14
    prepared = []
    for event in spec.events:
        title, title_full = wrap_lines(event.title, text_room, FONT, max_lines=2)
        note, note_full = wrap_lines(event.note, text_room, FONT, max_lines=2) if event.note else ([], "")
        prepared.append((event, title or [event.title[:12]], title_full, note, note_full))
    title_lines = max(len(item[1]) for item in prepared)
    note_lines = max((len(item[3]) for item in prepared), default=0)
    axis = 28 + LINE
    height = axis + 18 + title_lines * LINE + (note_lines * LINE if note_lines else 0) + 16
    width = count * col
    parts = [line(col / 2, axis, width - col / 2, axis, "track")]
    for index, (event, title, title_full, note, note_full) in enumerate(prepared):
        cx = col * index + col / 2
        parts.append(text_at(cx, 16, event.when, FONT, anchor="middle", weight="700"))
        dot = "dot-hi" if event.hi else "dot"
        radius = 6 if event.hi else 4.5
        parts.append(
            f'<circle class="{dot}" cx="{cx:.0f}" cy="{axis:.0f}" r="{radius}"/>'
        )
        y = axis + 16
        weight = "700" if event.hi else ""
        for at, shown in enumerate(title):
            parts.append(text_at(cx, y, shown, FONT, anchor="middle", full=title_full if at == 0 else "", weight=weight))
            y += LINE
        for at, shown in enumerate(note):
            parts.append(text_at(cx, y, shown, FONT, anchor="middle", css="muted", full=note_full if at == 0 else ""))
            y += LINE
    bits = [f"{event.when} {event.title}" for event in spec.events[:12]]
    return svg_doc(width, height, aria("timeline", bits), parts)


def _vertical(spec: Timeline) -> str:
    text_room = LIMIT - 48
    y = 16.0
    rows = []
    for event in spec.events:
        title, title_full = wrap_lines(event.title, text_room, FONT, max_lines=2)
        note, note_full = wrap_lines(event.note, text_room, FONT, max_lines=2) if event.note else ([], "")
        block = LINE + max(1, len(title)) * LINE + len(note) * LINE + 12
        rows.append((event, title or [event.title], title_full, note, note_full, y, block))
        y += block
    height = y + 8
    parts = [line(18, 16, 18, height - 10, "spine")]
    right = 80.0
    for event, title, title_full, note, note_full, top, block in rows:
        cy = top + 8
        dot = "dot-hi" if event.hi else "dot"
        radius = 6 if event.hi else 4.5
        parts.append(f'<circle class="{dot}" cx="18" cy="{cy:.0f}" r="{radius}"/>')
        span = text_width(event.when, FONT)
        span = max(span, max((text_width(shown, FONT) for shown in title), default=0))
        span = max(span, max((text_width(shown, FONT) for shown in note), default=0))
        if event.hi:
            parts.append(rect(32, top - 4, span + 16, block - 8, "note", rx=6))
        parts.append(text_at(36, cy, event.when, FONT, weight="700"))
        right = max(right, 36 + text_width(event.when, FONT))
        cursor = cy + LINE
        weight = "700" if event.hi else ""
        for at, shown in enumerate(title):
            parts.append(text_at(36, cursor, shown, FONT, full=title_full if at == 0 else "", weight=weight))
            right = max(right, 36 + text_width(shown, FONT))
            cursor += LINE
        for at, shown in enumerate(note):
            parts.append(text_at(36, cursor, shown, FONT, css="muted", full=note_full if at == 0 else ""))
            right = max(right, 36 + text_width(shown, FONT))
            cursor += LINE
    bits = [f"{event.when} {event.title}" for event in spec.events[:12]]
    return svg_doc(min(LIMIT, right + 16), height, aria("timeline", bits), parts)


def draw_tree(spec: Tree) -> str:
    if len(spec.roots) == 1:
        root = spec.roots[0]
        leaves = root.children and all(not child.children for child in root.children)
        if leaves and 2 <= len(root.children) <= 4:
            org = _org(root)
            if org is not None:
                return org
    return _indent(spec)


def _org(root: TreeNode) -> str | None:
    kids = root.children
    packed: list[tuple[TreeNode, list[str], str, list[str], str, float, float]] = []
    for node in [root, *kids]:
        lines, full = wrap_lines(node.label, 110, FONT, max_lines=2)
        if not lines:
            lines = [node.label[:16]]
        note, note_full = wrap_lines(node.note, 110, FONT, max_lines=1) if node.note else ([], "")
        width = max(text_width(line, FONT) for line in [*lines, *note]) + 28
        height = (len(lines) + len(note)) * LINE + 16
        packed.append((node, lines, full, note, note_full, width, height))
    gap = 16.0
    row = sum(item[5] for item in packed[1:]) + gap * (len(packed) - 2)
    width = max(packed[0][5], row) + 24
    if not fits(width):
        return None
    root_box = packed[0]
    parts: list[str] = []
    root_x = (width - root_box[5]) / 2
    _org_box(parts, root_box, root_x, 12)
    root_cx = root_x + root_box[5] / 2
    child_y = 12 + root_box[6] + 28
    parts.append(line(root_cx, 12 + root_box[6], root_cx, child_y - 14, "wire"))
    cursor = (width - row) / 2
    centers: list[float] = []
    for item in packed[1:]:
        _org_box(parts, item, cursor, child_y)
        centers.append(cursor + item[5] / 2)
        cursor += item[5] + gap
    if len(centers) >= 2:
        bar_y = child_y - 14
        parts.append(line(centers[0], bar_y, centers[-1], bar_y, "wire"))
        for cx in centers:
            parts.append(line(cx, bar_y, cx, child_y, "wire"))
    height = child_y + max(item[6] for item in packed[1:]) + 12
    bits = [root.label, *[child.label for child in kids]]
    return svg_doc(width, height, aria("tree", bits), parts)


def _org_box(
    parts: list[str],
    item: tuple[TreeNode, list[str], str, list[str], str, float, float],
    x: float,
    y: float,
) -> None:
    node, lines, full, note, note_full, width, height = item
    css = "box hi" if node.hi else "box"
    parts.append(rect(x, y, width, height, css, rx=8))
    cursor = y + 8 + LINE / 2
    weight = "700" if node.label.endswith("/") or node.hi else ""
    for at, shown in enumerate(lines):
        parts.append(text_at(x + width / 2, cursor, shown, FONT, anchor="middle", full=full if at == 0 else "", weight=weight))
        cursor += LINE
    for at, shown in enumerate(note):
        parts.append(text_at(x + width / 2, cursor, shown, FONT, anchor="middle", css="muted", full=note_full if at == 0 else ""))
        cursor += LINE


def _indent(spec: Tree) -> str:
    parts: list[str] = []
    y = 16.0
    right = 40.0
    labels: list[str] = []

    def walk(node: TreeNode, depth: int, parent: tuple[float, float] | None) -> None:
        nonlocal y, right
        shown_depth = depth
        while 16 + shown_depth * 16 > LIMIT - 96 and shown_depth > 0:
            shown_depth -= 1
        x = 16 + shown_depth * 16
        room = max(48.0, LIMIT - x - 16)
        lines, full = wrap_lines(node.label, room, FONT, max_lines=2)
        if not lines:
            lines = [node.label[:16]]
        note, note_full = wrap_lines(node.note, room, FONT, max_lines=1) if node.note else ([], "")
        block = (len(lines) + len(note)) * LINE + 8
        cy = y + block / 2
        if parent is not None:
            px, py = parent
            parts.append(line(px, py, px, cy, "wire"))
            parts.append(line(px, cy, x - 4, cy, "wire"))
        if node.hi:
            widest = max(text_width(line, FONT) for line in [*lines, *note]) if note else max(text_width(line, FONT) for line in lines)
            parts.append(rect(x - 4, y, widest + 12, block, "note", rx=4))
        weight = "700" if node.label.endswith("/") or node.hi else ""
        cursor = y + LINE / 2 + 2
        for at, shown in enumerate(lines):
            parts.append(text_at(x, cursor, shown, FONT, full=full if at == 0 else "", weight=weight))
            right = max(right, x + text_width(shown, FONT))
            cursor += LINE
        for at, shown in enumerate(note):
            parts.append(text_at(x, cursor, shown, FONT, css="muted", full=note_full if at == 0 else ""))
            right = max(right, x + text_width(shown, FONT))
            cursor += LINE
        labels.append(node.label)
        anchor = (x + 6, cy)
        y += block
        for child in node.children:
            walk(child, depth + 1, anchor)

    for root in spec.roots:
        walk(root, 0, None)
    return svg_doc(min(LIMIT, right + 16), y + 8, aria("tree", labels[:12]), parts)
