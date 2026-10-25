"""Parser, escaping, and SVG render tests for fast-path diagrams."""

from __future__ import annotations

import re

from artifactsmith.layout.chart_scale import MIN_FONT_PX, NARROW_PX
from artifactsmith.layout.diagram import read_fence
from artifactsmith.layout.diagram_parse import parse_flow, parse_sequence, parse_timeline, parse_tree
from artifactsmith.layout.diagram_svg import FONT, LIMIT
from artifactsmith.layout.parse import parse_markdown
from artifactsmith.layout.prompt import fast_system_prompt
from artifactsmith.layout.render import render_layout
from artifactsmith.renderers.html_sanitize import sanitize_html_document
from artifactsmith.renderers.svg_sanitize import sanitize_svg_fragment

_VIEW = re.compile(r'viewBox="0 0 ([0-9.]+) ([0-9.]+)"')
_FONT = re.compile(r'font-size="([0-9.]+)"')


def _size(svg: str) -> tuple[float, float]:
    match = _VIEW.search(svg)
    assert match is not None
    return float(match.group(1)), float(match.group(2))


def _assert_phone(svg: str) -> None:
    width, _height = _size(svg)
    fonts = [float(item) for item in _FONT.findall(svg)]
    assert fonts
    assert width <= LIMIT + 0.5
    # 320px is the content column inside a 390px viewport.
    assert min(fonts) * NARROW_PX / width >= MIN_FONT_PX - 0.05
    assert min(fonts) >= FONT - 0.1
    cleaned = sanitize_svg_fragment(f"<div>{svg}</div>")
    assert 'role="img"' in cleaned
    assert "<script" not in cleaned.lower()
    assert "<svg" in cleaned


def test_flow_parser_reads_direction_labels_and_highlights():
    flow = parse_flow(
        "flow LR",
        "(CLOSED) -> LISTEN: passive open\nLISTEN -> *ESTABLISHED: receive ACK\nSYN_SENT --> *ESTABLISHED: receive SYN-ACK\n",
    )
    assert flow is not None
    assert flow.direction == "LR"
    assert [node.name for node in flow.nodes] == ["CLOSED", "LISTEN", "ESTABLISHED", "SYN_SENT"]
    assert flow.nodes[0].shape == "pill"
    assert flow.nodes[2].hi is True
    assert flow.edges[0].label == "passive open"
    assert flow.edges[2].dashed is True
    downward = parse_flow("flow TB", "A -> B: yes\n")
    assert downward is not None and downward.direction == "TB"


def test_sequence_parser_keeps_actor_order_notes_and_dashed_returns():
    seq = parse_sequence(
        "sequence",
        "participants: Server, Client\nnote Server: LISTEN\nClient -> Server: SYN\nServer --> Client: SYN-ACK\n",
    )
    assert seq is not None
    assert seq.actors == ["Server", "Client"]
    assert seq.steps[0].kind == "note" and seq.steps[0].label == "LISTEN"
    assert seq.steps[1].src == "Client" and seq.steps[1].label == "SYN"
    assert seq.steps[2].dashed is True


def test_timeline_parser_marks_the_key_moment():
    spec = parse_timeline("timeline", "Mar 2 | Kickoff | Team\n*Mar 16 | Decision | Build the API\n")
    assert spec is not None
    assert spec.events[0].hi is False
    assert spec.events[1].hi is True
    assert spec.events[1].when == "Mar 16"
    assert spec.events[1].note == "Build the API"
    forced = parse_timeline("timeline v", "Mar 2 | Kickoff\n")
    assert forced is not None and forced.force == "v"


def test_tree_parser_reads_indent_notes_and_highlights():
    spec = parse_tree("tree", "repo/\n  src/\n    *app.py | entry\n  tests/\n")
    assert spec is not None
    assert spec.roots[0].label == "repo/"
    assert spec.roots[0].children[0].label == "src/"
    child = spec.roots[0].children[0].children[0]
    assert child.label == "app.py" and child.hi and child.note == "entry"
    assert spec.roots[0].children[1].label == "tests/"


def test_malformed_diagrams_do_not_parse():
    assert parse_flow("flow", "not an edge\n") is None
    assert parse_flow("flow", "A ->\n") is None
    assert parse_flow("flow", "A -> B -> C\n") is None
    assert parse_flow("flow", "") is None
    assert parse_sequence("sequence", "hello there\n") is None
    assert parse_sequence("sequence", "participants: Ada\n") is None
    assert parse_timeline("timeline", "no pipe here\n") is None
    assert parse_timeline("timeline", "*\n") is None
    assert parse_tree("tree", "") is None
    assert parse_tree("tree", "*\n") is None
    assert parse_tree("tree", "|\n") is None


def test_labels_are_escaped_in_every_diagram():
    payload = '<script>alert("x")</script> & co'
    samples = {
        "flow LR": f"A -> B: {payload}\n",
        "sequence": f"A -> B: {payload}\n",
        "timeline": f"Today | {payload}\n",
        "tree": f"root\n  {payload}\n",
    }
    for info, content in samples.items():
        drawn = read_fence(info, content)
        assert drawn is not None and drawn.svg
        assert "<script" not in drawn.svg.lower()
        assert "&lt;script&gt;" in drawn.svg
        assert "&amp;" in drawn.svg
        assert "&quot;" in drawn.svg
        _assert_phone(drawn.svg)


def test_each_diagram_renders_svg():
    flow = read_fence("flow LR", "(CLOSED) -> LISTEN: open\nLISTEN -> *ESTABLISHED: ack\n")
    sequence = read_fence(
        "sequence",
        "participants: Client, Server\nClient -> Server: SYN\nServer --> Client: SYN-ACK\nnote Client, Server: ESTABLISHED\n",
    )
    timeline = read_fence("timeline", "Mar 2 | Kickoff\n*Mar 16 | Decision | API\nApr 13 | Launch\n")
    tree = read_fence("tree", "repo/\n  src/\n    app.py\n")
    for drawn, needle in (
        (flow, "ESTABLISHED"),
        (sequence, "SYN-ACK"),
        (timeline, "Decision"),
        (tree, "app.py"),
    ):
        assert drawn is not None and drawn.svg
        assert needle in drawn.svg
        _assert_phone(drawn.svg)
    assert flow is not None and flow.svg and "hi" in flow.svg
    assert sequence is not None and sequence.svg and "dash" in sequence.svg
    assert timeline is not None and timeline.svg and "dot-hi" in timeline.svg
    assert tree is not None and tree.svg and "wire" in tree.svg


_TCP = """(CLOSED) -> LISTEN: passive open
LISTEN -> SYN_RCVD: receive SYN
(CLOSED) --> SYN_SENT: active open
SYN_SENT -> *ESTABLISHED: receive SYN-ACK
SYN_RCVD -> ESTABLISHED: receive ACK
"""


def _svg_rects(svg: str, css: str) -> list[tuple[float, float, float, float]]:
    pattern = re.compile(rf'<rect class="{css}" x="([^"]+)" y="([^"]+)" width="([^"]+)" height="([^"]+)"')
    return [(float(x), float(y), float(w), float(h)) for x, y, w, h in pattern.findall(svg)]


def _svg_lines(svg: str) -> list[tuple[float, float, float, float]]:
    pattern = re.compile(r'<line class="edge[^"]*" x1="([^"]+)" y1="([^"]+)" x2="([^"]+)" y2="([^"]+)"')
    return [(float(x1), float(y1), float(x2), float(y2)) for x1, y1, x2, y2 in pattern.findall(svg)]


def _boxes_overlap(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
    pad: float = 1,
) -> bool:
    ax, ay, aw, ah = left
    bx, by, bw, bh = right
    return ax < bx + bw + pad and bx < ax + aw + pad and ay < by + bh + pad and by < ay + ah + pad


def _line_through(x1: float, y1: float, x2: float, y2: float, box: tuple[float, float, float, float]) -> bool:
    left, top, width, height = box
    inset = 3
    left, top = left + inset, top + inset
    right, bottom = left + width - 2 * inset, top + height - 2 * inset
    if right <= left or bottom <= top:
        return False
    if max(x1, x2) < left or min(x1, x2) > right or max(y1, y2) < top or min(y1, y2) > bottom:
        return False
    if abs(x1 - x2) <= 0.8 or abs(y1 - y2) <= 0.8:
        return True
    for step in range(1, 8):
        t = step / 8
        x = x1 + (x2 - x1) * t
        y = y1 + (y2 - y1) * t
        if left < x < right and top < y < bottom:
            return True
    return False


def test_flow_labels_stay_off_the_nodes_and_skip_edges_go_around():
    drawn = read_fence("flow LR", _TCP)
    assert drawn is not None and drawn.svg
    svg = drawn.svg
    _assert_phone(svg)
    assert "receive SYN-ACK" in svg
    assert "dash" in svg
    nodes = _svg_rects(svg, r"box(?: hi)?")
    labels = _svg_rects(svg, "tag")
    assert len(nodes) == 5
    assert len(labels) == 5
    for label in labels:
        for node in nodes:
            assert not _boxes_overlap(label, node, 0)
    for label in labels:
        for other in labels:
            if other is not label:
                assert not _boxes_overlap(label, other, 0)
    for segment in _svg_lines(svg):
        for node in nodes:
            assert not _line_through(*segment, node)


def test_wide_flow_switches_to_vertical_and_a_wide_band_wraps():
    chain = "\n".join(f"S{index} -> S{index + 1}: go" for index in range(8))
    drawn = read_fence("flow LR", chain)
    assert drawn is not None and drawn.svg
    width, height = _size(drawn.svg)
    _assert_phone(drawn.svg)
    assert height > width
    star = "\n".join(f"Hub -> N{index}: edge" for index in range(8))
    wrapped = read_fence("flow TB", star)
    assert wrapped is not None and wrapped.svg
    _assert_phone(wrapped.svg)
    star_w, star_h = _size(wrapped.svg)
    assert star_w <= LIMIT + 0.5
    assert star_h > 80


def test_long_timeline_uses_a_spine():
    lines = "\n".join(f"2026-03-{day:02d} | Event {day} | note" for day in range(1, 8))
    drawn = read_fence("timeline", lines)
    assert drawn is not None and drawn.svg
    assert "spine" in drawn.svg
    _assert_phone(drawn.svg)


def test_failed_diagram_is_a_list_not_the_source():
    source = """# Handshake

```flow LR
nope <script>alert(1)</script>
also bad
```
"""
    html, notes = render_layout(source, style="house", title="Handshake")
    assert "flow diagram shown as a list" in notes
    assert "asl-steps" in html
    assert "nope" in html
    assert "->" not in html
    assert "```" not in html
    assert "<script" not in html.lower()
    assert "<pre" not in html
    doc = sanitize_html_document(html)
    assert "<script" not in doc.lower()


def test_stray_line_is_skipped_and_the_diagram_stays():
    source = """# Handshake

```flow LR
Start -> Review: submit
nope <script>alert(1)</script>
```
"""
    html, notes = render_layout(source, style="house", title="Handshake")
    assert "flow diagram skipped 1 line" in notes
    assert "shown as a list" not in " ".join(notes)
    assert "asl-diagram" in html
    assert "Review" in html
    assert "nope" not in html
    assert "->" not in html
    assert "<script" not in html.lower()
    kept = parse_sequence("sequence", "A -> B: hi\nnot a message\n")
    assert kept is not None and kept.skipped == 1 and kept.steps[0].label == "hi"
    timeline = parse_timeline("timeline", "Mar 2 | Kickoff\nnope\n")
    assert timeline is not None and timeline.skipped == 1 and timeline.events[0].title == "Kickoff"


def test_timeline_container_draws_pipe_lines_not_a_raw_paragraph():
    """Prompt E put fence lines inside ::: timeline. That must draw, not print pipes."""
    source = """# Project kickoff plan: customer self-service help center

## Eight-week timeline

::: timeline
*Week 1 | Kickoff and discovery | Confirm the project sponsor, project manager, workstream leads, and decision rights.
Week 2 | Strategy and information architecture | Select or confirm the help center platform.
Week 3 | Build | Draft the highest-priority articles.
:::
"""
    html, notes = render_layout(source, style="house", title="Kickoff")
    assert notes == []
    assert "asl-diagram" in html
    assert "dot-hi" in html
    assert "Kickoff and discovery" in html
    assert "Week 1" in html
    assert "Strategy and information architecture" in html
    assert "*Week 1" not in html
    assert "|" not in html
    assert 'class="asl-timeline"' not in html
    assert "width:100%" in html
    doc = sanitize_html_document(html)
    assert "Kickoff and discovery" in doc
    assert "*Week 1" not in doc


def test_old_timeline_list_and_chart_fence_stay():
    source = """# Board

::: timeline
- March | Shipped
:::

```chart bar
A: 1
B: 2
```

```python
print(1)
```
"""
    html, notes = render_layout(source, style="house", title="Board")
    assert notes == []
    assert "asl-timeline" in html
    assert "March" in html and "Shipped" in html
    assert "<svg" in html
    assert "print(1)" in html
    blocks, parsed_notes = parse_markdown("```flowx\nA -> B\n```\n")
    assert parsed_notes == []
    assert blocks[0].kind == "code"
    assert "A -> B" in blocks[0].html


def test_prompt_teaches_the_diagram_fences():
    house = fast_system_prompt("house")
    for fence in ("```flow LR", "```sequence", "```tree"):
        assert fence in house
    assert "::: timeline" in house
    assert "date | title | note" in house
    assert "```timeline" not in house
