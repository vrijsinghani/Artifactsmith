"""Parse diagram fences. A bad line fails the block; nothing here draws.

The line grammar follows answer-me-with-html (MIT),
https://github.com/QingYunA/answer-me-with-html
(src/components/flow.js, sequence.js, timeline.js, tree.js):
``A -> B: label``, ``-->`` for a dashed return, ``note A: text``,
``date | title``, and indentation for a tree. The Python is not a copy
of those files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

KINDS = frozenset({"flow", "sequence", "timeline", "tree"})
_MAX_LINES = 80
_MAX_LINE = 400
# A kickoff note runs past the fence line cap. Keep it, and cap what the
# drawing stores so a long tooltip cannot blow the page SVG text budget.
_MAX_TIMELINE_LINE = 4000
_MAX_NOTE = 600

_EDGE = re.compile(r"^(?P<src>.+?)\s*(?P<arrow>-->|->|→)\s*(?P<dst>.+?)(?:\s*:\s*(?P<label>.*))?$")
_PARTS = re.compile(r"^participants?\s*:\s*(.+)$", re.I)
_NOTE = re.compile(r"^note\s+([^:]+):\s*(.+)$", re.I)
_DIVIDER = re.compile(r"^==\s*(.+?)\s*==$")
_ARROW = re.compile(r"-->|->|→|⇒")


class _Bad(Exception):
    """The fence is not a diagram. The page shows a list instead."""


@dataclass
class FlowNode:
    name: str
    shape: str
    hi: bool


@dataclass
class FlowEdge:
    src: str
    dst: str
    label: str
    dashed: bool
    back: bool = False


@dataclass
class Flow:
    direction: str
    nodes: list[FlowNode]
    edges: list[FlowEdge]
    skipped: int = 0


@dataclass
class Step:
    kind: str
    src: str = ""
    dst: str = ""
    label: str = ""
    dashed: bool = False
    over: tuple[str, ...] = ()


@dataclass
class Sequence:
    actors: list[str]
    steps: list[Step]
    numbered: bool
    skipped: int = 0


@dataclass
class Event:
    when: str
    title: str
    note: str
    hi: bool


@dataclass
class Timeline:
    events: list[Event]
    force: str
    skipped: int = 0


@dataclass
class TreeNode:
    label: str
    note: str
    hi: bool
    children: list[TreeNode] = field(default_factory=list)


@dataclass
class Tree:
    roots: list[TreeNode]
    skipped: int = 0


def kind_of(info: str) -> str | None:
    name = (info or "").split()[:1]
    if not name:
        return None
    kind = name[0].lower()
    return kind if kind in KINDS else None


def parse_flow(info: str, content: str) -> Flow | None:
    try:
        return _flow(info, content)
    except _Bad:
        return None


def parse_sequence(info: str, content: str) -> Sequence | None:
    try:
        return _sequence(info, content)
    except _Bad:
        return None


def parse_timeline(info: str, content: str) -> Timeline | None:
    try:
        return _timeline(info, content)
    except _Bad:
        return None


def parse_tree(info: str, content: str) -> Tree | None:
    try:
        return _tree(content)
    except _Bad:
        return None


def fallback_steps(kind: str, content: str) -> list[str]:
    """Readable steps. Arrow syntax is rewritten so the page is not the source."""
    steps: list[str] = []
    for raw in (content or "").splitlines():
        step = _humanize(kind, raw)
        if step:
            steps.append(step)
        if len(steps) >= 40:
            break
    return steps or ["Diagram could not be drawn"]


def _rows(content: str) -> tuple[list[str], int]:
    """Non-comment lines, and how many were too long to keep."""
    rows: list[str] = []
    skipped = 0
    for raw in (content or "").splitlines():
        text = raw.strip()
        if not text or text.startswith("//"):
            continue
        if len(text) > _MAX_LINE:
            skipped += 1
            continue
        rows.append(text)
    if len(rows) > _MAX_LINES:
        raise _Bad()
    return rows, skipped


def _flow(info: str, content: str) -> Flow:
    direction = _direction(info)
    nodes: dict[str, FlowNode] = {}
    edges: list[FlowEdge] = []
    order: list[str] = []
    rows, skipped = _rows(content)

    def add(raw: str) -> str:
        name, shape, hi = _node(raw)
        found = nodes.get(name)
        if found is None:
            nodes[name] = FlowNode(name, shape, hi)
            order.append(name)
        elif hi:
            found.hi = True
        return name

    for row in rows:
        match = _EDGE.fullmatch(row)
        if match is None:
            skipped += 1
            continue
        label = " ".join((match.group("label") or "").split())[:80]
        dashed = match.group("arrow") == "-->"
        pieces = [piece for piece in re.split(r"\s+&\s+", match.group("dst")) if piece.strip()]
        try:
            src = add(match.group("src"))
        except _Bad:
            skipped += 1
            continue
        added = False
        for piece in pieces:
            try:
                dst = add(piece)
            except _Bad:
                skipped += 1
                continue
            edges.append(FlowEdge(src, dst, label, dashed))
            added = True
        if not added:
            skipped += 1
    if not edges or len(nodes) > 24 or len(edges) > 48:
        raise _Bad()
    return Flow(direction, [nodes[name] for name in order], edges, skipped)


def _direction(info: str) -> str:
    picked = "LR"
    for token in info.split()[1:]:
        word = token.lower()
        if word in {"tb", "td", "vertical", "v"}:
            picked = "TB"
        elif word in {"lr", "horizontal", "h"}:
            picked = "LR"
    return picked


def _node(raw: str) -> tuple[str, str, bool]:
    text = " ".join(raw.split())
    hi = False
    if text.startswith("*"):
        hi = True
        text = text[1:].strip()
    shape = "box"
    if len(text) >= 2 and text[0] == "(" and text[-1] == ")":
        shape, text = "pill", text[1:-1].strip()
    elif len(text) >= 2 and text[0] == "{" and text[-1] == "}":
        shape, text = "diamond", text[1:-1].strip()
    elif text.startswith("[(") and text.endswith(")]") and len(text) >= 4:
        text = text[2:-2].strip()
    elif len(text) >= 2 and text[0] == "[" and text[-1] == "]":
        text = text[1:-1].strip()
    if text.startswith("*"):
        hi = True
        text = text[1:].strip()
    text = " ".join(text.split())
    if not text or len(text) > 60 or "->" in text or "→" in text:
        raise _Bad()
    return text, shape, hi


def _sequence(info: str, content: str) -> Sequence:
    numbered = "num" in {token.lower() for token in info.split()[1:]}
    actors: list[str] = []
    steps: list[Step] = []

    def add(name: str) -> str:
        cleaned = " ".join(name.split())
        if not cleaned or len(cleaned) > 40:
            raise _Bad()
        if cleaned not in actors:
            actors.append(cleaned)
        return cleaned

    rows, skipped = _rows(content)
    for row in rows:
        parts = _PARTS.fullmatch(row)
        note = _NOTE.fullmatch(row)
        divider = _DIVIDER.fullmatch(row)
        message = _EDGE.fullmatch(row)
        try:
            if parts:
                named = False
                for name in re.split(r"[,，]", parts.group(1)):
                    if name.strip():
                        add(name)
                        named = True
                if not named:
                    skipped += 1
                continue
            if note:
                over = tuple(add(name) for name in re.split(r"[,，]", note.group(1)) if name.strip())
                if not over:
                    skipped += 1
                    continue
                steps.append(Step("note", label=" ".join(note.group(2).split())[:120], over=over))
                continue
            if divider:
                steps.append(Step("divider", label=" ".join(divider.group(1).split())[:80]))
                continue
            if message is None:
                skipped += 1
                continue
            src = add(message.group("src"))
            dst = add(message.group("dst"))
        except _Bad:
            skipped += 1
            continue
        label = " ".join((message.group("label") or "").split())[:80]
        steps.append(Step("msg", src, dst, label, message.group("arrow") == "-->"))
    if not actors or not any(step.kind == "msg" for step in steps):
        raise _Bad()
    if len(actors) > 8 or len(steps) > 40:
        raise _Bad()
    return Sequence(actors, steps, numbered, skipped)


def _timeline(info: str, content: str) -> Timeline:
    force = ""
    for token in info.split()[1:]:
        word = token.lower()
        if word in {"v", "vertical"}:
            force = "v"
        elif word in {"h", "horizontal"}:
            force = "h"
    events: list[Event] = []
    rows, skipped = _timeline_rows(content)
    for row in rows:
        hi = row.startswith("*")
        body = row[1:].strip() if hi else row
        parts = [part.strip() for part in body.split("|")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            skipped += 1
            continue
        note = " ".join(" ".join(part for part in parts[2:] if part).split())
        if len(note) > _MAX_NOTE:
            note = note[: _MAX_NOTE - 1].rstrip() + "…"
        events.append(Event(parts[0][:40], parts[1][:80], note, hi))
    if not events or len(events) > 24:
        raise _Bad()
    return Timeline(events, force, skipped)


def _timeline_rows(content: str) -> tuple[list[str], int]:
    """Timeline lines stay even when the note is long. Other fences use ``_rows``."""
    rows: list[str] = []
    for raw in (content or "").splitlines():
        text = raw.strip()
        if not text or text.startswith("//"):
            continue
        if len(text) > _MAX_TIMELINE_LINE:
            text = text[:_MAX_TIMELINE_LINE]
        rows.append(text)
    if len(rows) > _MAX_LINES:
        raise _Bad()
    return rows, 0


def _tree(content: str) -> Tree:
    roots: list[TreeNode] = []
    stack: list[tuple[int, TreeNode]] = []
    count = 0
    skipped = 0
    for raw in (content or "").splitlines():
        if not raw.strip() or raw.strip().startswith("//"):
            continue
        if len(raw) > _MAX_LINE:
            skipped += 1
            continue
        expanded = raw.replace("\t", "  ")
        indent = len(expanded) - len(expanded.lstrip(" "))
        body = expanded.strip()
        if body.startswith("- "):
            body = body[2:].strip()
        hi = body.startswith("*")
        if hi:
            body = body[1:].strip()
        label, _, note = body.partition("|")
        label, note = " ".join(label.split()), " ".join(note.split())
        if not label or len(label) > 80:
            skipped += 1
            continue
        node = TreeNode(label[:80], note[:80], hi)
        depth = indent // 2
        while stack and stack[-1][0] >= depth:
            stack.pop()
        if stack:
            stack[-1][1].children.append(node)
        else:
            roots.append(node)
        stack.append((depth, node))
        count += 1
        if count > 60:
            raise _Bad()
    if not roots:
        raise _Bad()
    return Tree(roots, skipped)


def _humanize(kind: str, raw: str) -> str:
    text = raw.strip()
    if not text or text.startswith("//"):
        return ""
    if kind == "timeline":
        body = text[1:].strip() if text.startswith("*") else text
        parts = [part.strip() for part in body.split("|") if part.strip()]
        if len(parts) < 2:
            return ""
        return " — ".join(parts)[:180]
    low = text.lower()
    if kind == "sequence" and low.startswith("participant"):
        names = text.partition(":")[2].strip()
        return f"Actors: {names}"[:180] if names else ""
    if kind == "sequence" and low.startswith("note "):
        body = text.partition(":")[2].strip() or text[5:].strip()
        return f"Note: {body}"[:180] if body else ""
    if text.startswith("==") and text.endswith("==") and len(text) > 4:
        inner = text.strip("=").strip()
        return inner[:180]
    text = _ARROW.sub(" to ", text)
    text = text.replace("*", "")
    text = text.replace("[(", "").replace(")]", "")
    text = re.sub(r"[{}\[\]()]", "", text)
    text = re.sub(r"\s+", " ", text).strip(" :")
    if not text or "->" in text or "```" in text or text.startswith(":::"):
        return ""
    return text[:180]
