"""Server-drawn diagram fences for the fast path.

Flow, sequence, timeline, and tree become inline SVG. The line grammar follows
answer-me-with-html (MIT), https://github.com/QingYunA/answer-me-with-html
(src/components/flow.js, sequence.js, timeline.js, tree.js). The Python is not
a copy of those files. A fence that does not parse becomes a list of steps.
The raw source is not written into the page.
"""

from __future__ import annotations

from dataclasses import dataclass

from .diagram_draw import draw_diagram
from .diagram_parse import (
    Flow,
    Sequence,
    Timeline,
    Tree,
    fallback_steps,
    kind_of,
    parse_flow,
    parse_sequence,
    parse_timeline,
    parse_tree,
)
from .inline import esc
from .model import Block


@dataclass(frozen=True)
class Drawn:
    kind: str
    svg: str | None
    steps: tuple[str, ...]
    notes: tuple[str, ...] = ()


def read_fence(info: str, content: str) -> Drawn | None:
    """A diagram fence, or None when ``info`` is some other language."""
    kind = kind_of(info)
    if kind is None:
        return None
    model = _model(kind, info, content)
    if model is None:
        return Drawn(kind, None, tuple(fallback_steps(kind, content)))
    notes = (_skip_note(kind, model.skipped),) if model.skipped else ()
    return Drawn(kind, draw_diagram(kind, model), (), notes)


def timeline_from_children(children: list[Block]) -> Drawn | None:
    """``date | title | note`` lines inside ``::: timeline`` draw a timeline.

    A bullet list stays a list. That is the older timeline syntax.
    """
    if any(child.kind in {"list", "olist"} for child in children):
        return None
    parts = [child.source for child in children if child.kind == "paragraph" and child.source.strip()]
    text = "\n".join(parts).strip()
    if "|" not in text:
        return None
    drawn = read_fence("timeline", text)
    if drawn is None:
        return None
    # A failed pipe block must become a step list, not the raw paragraph.
    return drawn


def _skip_note(kind: str, count: int) -> str:
    noun = "line" if count == 1 else "lines"
    return f"{kind} diagram skipped {count} {noun}"


def steps_block(steps: tuple[str, ...]) -> Block:
    items = []
    for step in steps:
        shown = esc(step)
        para = Block(kind="paragraph", html=shown, plain=step)
        items.append(Block(kind="item", html=shown, plain=step, children=[para]))
    return Block(kind="steps", children=items)


def _model(kind: str, info: str, content: str) -> Flow | Sequence | Timeline | Tree | None:
    if kind == "flow":
        return parse_flow(info, content)
    if kind == "sequence":
        return parse_sequence(info, content)
    if kind == "timeline":
        return parse_timeline(info, content)
    return parse_tree(info, content)
