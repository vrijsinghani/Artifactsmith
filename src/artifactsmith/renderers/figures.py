"""Chart and diagram fences for PDF, DOCX, and XLSX.

The fast-path renderers draw the SVG. This module only decides which fence is a
figure, paints house colors into that SVG, and builds the table used when a
drawing is impossible or the format cannot hold an image. The fence source is
not returned.
"""

from __future__ import annotations

import re
from html import unescape

from artifactsmith.layout.chart import ChartSpec, aria_label, chart_svg, parse_chart
from artifactsmith.layout.chart_scale import bare_years, format_value
from artifactsmith.layout.diagram import Drawn, read_fence
from artifactsmith.layout.diagram_parse import KINDS, fallback_steps

from .md_parse import Block

# Export SVG does not load the page stylesheet. Colors are the house palette,
# scoped so a chart dot and a diagram dot do not share one fill.
_PAINT = (
    "<style>"
    ".asl-chart text,.asl-diagram text{fill:#1c1917;font-family:Liberation Sans,sans-serif}"
    ".asl-chart .bar{fill:#0b5cab}"
    ".asl-chart .pie1{fill:#0d6b3d}.asl-chart .pie2{fill:#b45309}"
    ".asl-chart .pie3{fill:#6d28d9}.asl-chart .pie4{fill:#0f766e}.asl-chart .pie5{fill:#9a3412}"
    ".asl-chart .line{fill:none;stroke:#0b5cab;stroke-width:2px}"
    ".asl-chart .line.pie1{stroke:#0d6b3d}.asl-chart .line.pie2{stroke:#b45309}"
    ".asl-chart .line.pie3{stroke:#6d28d9}.asl-chart .line.pie4{stroke:#0f766e}"
    ".asl-chart .line.pie5{stroke:#9a3412}"
    ".asl-chart .dot{fill:#0b5cab}.asl-chart .dot.pie1{fill:#0d6b3d}"
    ".asl-chart .dot.pie2{fill:#b45309}.asl-chart .dot.pie3{fill:#6d28d9}"
    ".asl-chart .dot.pie4{fill:#0f766e}.asl-chart .dot.pie5{fill:#9a3412}"
    ".asl-chart .axis{stroke:#d5deea;stroke-width:1px;fill:none}"
    ".asl-diagram .box{fill:#f3f6fb;stroke:#d5deea;stroke-width:1.5px}"
    ".asl-diagram .hi{stroke:#0b5cab;stroke-width:2.5px}"
    ".asl-diagram .edge{fill:none;stroke:#1c1917;stroke-width:1.5px}"
    ".asl-diagram .dash{stroke-dasharray:4 3}"
    ".asl-diagram .arrow{fill:#1c1917;stroke:none}"
    ".asl-diagram .wire,.asl-diagram .track{fill:none;stroke:#d5deea;stroke-width:1.5px}"
    ".asl-diagram .spine{fill:none;stroke:#0b5cab;stroke-width:2px}"
    ".asl-diagram .dot{fill:#ffffff;stroke:#0b5cab;stroke-width:2px}"
    ".asl-diagram .dot-hi{fill:#0b5cab;stroke:#0b5cab;stroke-width:2px}"
    ".asl-diagram .note{fill:#f3f6fb;stroke:#0b5cab;stroke-width:1.5px}"
    ".asl-diagram .tag{fill:#ffffff;stroke:none}"
    ".asl-diagram .muted{fill:#3f3f46}"
    "</style>"
)

_MERMAID_LANG = frozenset({"mermaid", "xychart", "xychart-beta", "flowchart", "graph"})
_ARIA = re.compile(r'aria-label="([^"]*)"')
_DIRECTIVE = re.compile(
    r"^(?:mermaid|xychart(?:-beta)?|flowchart|graph(?:\s+(?:tb|td|bt|lr|rl))?|"
    r"sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|erDiagram|gantt|pie|journey|"
    r"gitGraph|mindmap|subgraph|end|style|classDef|class|click|direction|linkStyle|"
    r"title|x-axis|y-axis)\b",
    re.I,
)
_BODY_MERMAID = re.compile(
    r"^(?:xychart(?:-beta)?|flowchart|sequenceDiagram|graph\s+(?:tb|td|bt|lr|rl))\b",
    re.I,
)
_NOT_FLOW = re.compile(
    r"^(?:sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|erDiagram|gantt|pie|journey|gitGraph|mindmap)\b",
    re.I,
)
_LABELED_EDGE = re.compile(r"^(?P<src>.+?)\s+--\s+(?P<label>.+?)\s+-->\s+(?P<dst>.+)$")
_EDGE = re.compile(
    r"^(?P<src>.+?)\s*(?P<arrow>-\.->|==>|-->|->>|---|->)\s*(?:\|(?P<label>[^|]*)\|\s*)?(?P<dst>.+?)\s*$"
)
_ARROW_SRC = re.compile(r"-{1,3}\.?-{0,2}>{1,2}|==>|→|⇒")
_NODE = re.compile(r"^(?P<id>[A-Za-z_][\w-]*)\s*(?P<rest>\S.*)?$")
_NUMBER = re.compile(r"^[+-]?\d[\d,]*(?:\.\d+)?%?$")


def fence_blocks(info: str, content: str) -> list[Block] | None:
    """A chart, diagram, or Mermaid fence, or None when the fence is ordinary code."""
    lang = _lang(info)
    if lang == "chart" or lang in KINDS:
        drawn = _native(lang, info, content)
        if drawn is not None:
            return drawn
        if _body_is_mermaid(content):
            return _mermaid(content)
        if lang == "chart":
            return [_loose_table(content)]
        return [_step_table(fallback_steps(lang, content))]
    if lang in _MERMAID_LANG or (not lang and _body_is_mermaid(content)):
        return _mermaid(content)
    return None


def rasterize_svg(svg: str) -> bytes | None:
    """PNG of a painted SVG, or None when Cairo cannot draw it."""
    try:
        import cairosvg
    except ImportError:  # pragma: no cover - cairosvg is a runtime dependency
        return None
    try:
        raw = cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=1200)
    except Exception:
        return None
    if not isinstance(raw, (bytes, bytearray)):
        return None
    data = bytes(raw)
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    return data


def _native(lang: str, info: str, content: str) -> list[Block] | None:
    if lang == "chart":
        spec = parse_chart(info or "chart", content)
        if spec is None:
            return None
        return [_chart_figure(spec)]
    drawn = read_fence(info or lang, content)
    if drawn is None:
        return None
    return _diagram_blocks(drawn, content)


def _diagram_blocks(drawn: Drawn, content: str) -> list[Block]:
    steps = list(drawn.steps) or fallback_steps(drawn.kind, content)
    if drawn.svg:
        block = _figure("diagram", drawn.svg, _step_rows(steps))
    else:
        block = _step_table(steps)
    blocks = [block]
    if drawn.notes:
        blocks.append(Block(kind="list", items=list(drawn.notes)))
    return blocks


def _mermaid(content: str) -> list[Block]:
    chart = _xychart_block(content)
    if chart is not None:
        return [chart]
    flow = _flow_blocks(content)
    if flow is not None:
        return flow
    return [_plain_table(content)]


def _xychart_block(content: str) -> Block | None:
    extracted = _extract_xychart(content)
    if extracted is None:
        return None
    info, body, headers, rows = extracted
    spec = parse_chart(info, body)
    if spec is None:
        return _table(headers, rows)
    return _chart_figure(spec)


def _flow_blocks(content: str) -> list[Block] | None:
    if any(_NOT_FLOW.match(line.strip()) for line in content.splitlines()):
        return None
    info, body = _flow_source(content)
    if not body.strip():
        return None
    drawn = read_fence(info, body)
    if drawn is None or drawn.svg is None:
        return None
    return _diagram_blocks(drawn, body)


def _chart_figure(spec: ChartSpec) -> Block:
    names = [_series_name(name, index, len(spec.series)) for index, (name, _vals) in enumerate(spec.series)]
    headers = [spec.x_title or "Category", *names]
    flat = [value for _name, column in spec.series for value in column]
    year = bare_years(flat, spec.unit)
    rows: list[list[str]] = []
    for index, label in enumerate(spec.labels):
        row = [label]
        for _name, values in spec.series:
            value = values[index] if index < len(values) else None
            row.append("" if value is None else format_value(value, spec.unit, year=year))
        rows.append(row)
    first = list(spec.series[0][1])
    label = aria_label(spec.kind, "", list(spec.labels), first, spec.unit, year=year)
    svg = chart_svg(
        spec.kind,
        list(spec.labels),
        first,
        spec.unit,
        label,
        series=[(name, list(column)) for name, column in spec.series],
        x_title=spec.x_title,
        y_title=spec.y_title,
    )
    if not svg:
        return _table(headers, rows)
    block = _figure("chart", svg, (headers, rows))
    block.alt = label or block.alt
    return block


def _figure(kind: str, svg: str, table: tuple[list[str], list[list[str]]]) -> Block:
    headers, rows = table
    painted = _paint(svg, kind)
    return Block(
        kind="figure",
        figure=kind,
        svg=painted,
        alt=_alt(painted, kind),
        headers=headers,
        rows=rows,
    )


def _paint(svg: str, kind: str) -> str:
    if not svg.startswith("<svg") or not svg.endswith("</svg>"):
        return svg
    end = svg.find(">")
    if end < 0:
        return svg
    # CairoSVG reads CSS width and cannot parse min(). The numeric width attribute stays.
    open_tag = re.sub(r'\sstyle="[^"]*"', "", svg[:end])
    if "xmlns=" not in open_tag:
        open_tag = open_tag.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1)
    body = svg[end + 1 : -len("</svg>")]
    css = "asl-chart" if kind == "chart" else "asl-diagram"
    view = re.search(r'viewBox="0 0 ([0-9.]+) ([0-9.]+)"', open_tag)
    background = ""
    if view:
        background = f'<rect width="{view.group(1)}" height="{view.group(2)}" fill="#ffffff"/>'
    return f'{open_tag}>{_PAINT}{background}<g class="{css}">{body}</g></svg>'


def _alt(svg: str, kind: str) -> str:
    match = _ARIA.search(svg)
    if match:
        return unescape(match.group(1)).strip()
    return "Chart" if kind == "chart" else "Diagram"


def _table(headers: list[str], rows: list[list[str]]) -> Block:
    if not rows:
        return _step_table(["Chart could not be drawn"])
    return Block(kind="table", headers=headers, rows=rows)


def _step_table(steps: list[str]) -> Block:
    shown = [step for step in steps if step.strip()] or ["Diagram could not be drawn"]
    return _table(["Step"], [[step] for step in shown])


def _step_rows(steps: list[str]) -> tuple[list[str], list[list[str]]]:
    shown = [step for step in steps if step.strip()] or ["Diagram could not be drawn"]
    return ["Step"], [[step] for step in shown]


def _loose_table(content: str) -> Block:
    rows: list[list[str]] = []
    for line in content.splitlines():
        parts = _row_parts(line)
        if parts:
            rows.append(parts)
    if not rows:
        return _step_table(["Chart could not be drawn"])
    width = max(len(row) for row in rows)
    if len(rows) > 1 and width >= 2 and not _NUMBER.match(rows[0][1] if len(rows[0]) > 1 else ""):
        headers = rows[0] + [f"Column {i + 1}" for i in range(len(rows[0]), width)]
        body = rows[1:]
    else:
        headers = ["Category"] if width == 1 else ["Category", "Value"]
        headers += [f"Column {i + 1}" for i in range(len(headers), width)]
        body = rows
    padded = [row + [""] * (width - len(row)) for row in body]
    return _table(headers[:width], [row[:width] for row in padded])


def _row_parts(line: str) -> list[str] | None:
    raw = line.strip()
    if not raw or set(raw) <= set("|-: "):
        return None
    if "|" in raw:
        body = raw[1:] if raw.startswith("|") else raw
        if body.endswith("|"):
            body = body[:-1]
        return [part.strip() for part in body.split("|")]
    if ":" in raw:
        label, _, val = raw.lstrip("-").strip().partition(":")
        return [label.strip(), val.strip()]
    return [raw]


def _plain_table(content: str) -> Block:
    steps: list[str] = []
    for raw in content.splitlines():
        text = raw.strip()
        if not text or text.startswith("%%") or _DIRECTIVE.match(text):
            continue
        softened = _soften(text)
        if softened and not _DIRECTIVE.match(softened):
            steps.append(softened)
        if len(steps) >= 40:
            break
    return _step_table(steps)


def _soften(text: str) -> str:
    def _label(match: re.Match[str]) -> str:
        inner = match.group(1).strip().strip("\"'")
        return f" {inner} " if inner else " "

    text = re.sub(r":::[\w-]+", "", text)
    text = re.sub(r"[\[({]([^\[\](){}]*)[\])}]", _label, text)
    text = _ARROW_SRC.sub(" to ", text)
    text = text.replace("|", " ")
    text = re.sub(r"\s+", " ", text).strip(" :-")
    if "```" in text or "->" in text:
        return ""
    return text[:180]


def _extract_xychart(content: str) -> tuple[str, str, list[str], list[list[str]]] | None:
    if not any(line.strip().lower().startswith("xychart") for line in content.splitlines()):
        return None
    labels: list[str] = []
    x_title = ""
    y_title = ""
    title = ""
    series: list[tuple[str, list[str]]] = []
    bars = 0
    lines = 0
    for raw in content.splitlines():
        text = raw.strip().rstrip(";")
        if not text or text.startswith("%%"):
            continue
        low = text.lower()
        if low.startswith("xychart"):
            continue
        if low.startswith("title"):
            title = _clean_title(text.split(None, 1)[1] if " " in text else "")
            continue
        if low.startswith("x-axis"):
            x_title, labels = _axis_labels(text.split(None, 1)[1] if " " in text else "")
            continue
        if low.startswith("y-axis"):
            y_title = _quoted_title(text)
            continue
        if low.startswith("bar ") or low.startswith("bar[") or low.startswith("line ") or low.startswith("line["):
            keyword = "line" if low.startswith("line") else "bar"
            rest = text.split(None, 1)[1] if " " in text else ""
            if keyword == "bar" and low.startswith("bar["):
                rest = text[3:]
            if keyword == "line" and low.startswith("line["):
                rest = text[4:]
            numbers = _number_list(rest)
            if numbers is None:
                continue
            if keyword == "bar":
                bars += 1
                name = "Bar" if bars == 1 else f"Bar {bars}"
            else:
                lines += 1
                name = "Line" if lines == 1 else f"Line {lines}"
            series.append((name, numbers))
    if not series:
        return None
    width = max(len(values) for _name, values in series)
    if not labels:
        labels = [str(index + 1) for index in range(width)]
    labels = labels[:width]
    while len(labels) < width:
        labels.append(str(len(labels) + 1))
    if len(series) == 1 and (y_title or title):
        series = [(y_title or title, series[0][1])]
    headers = [x_title or "Category", *[name for name, _values in series]]
    rows = []
    for index, label in enumerate(labels):
        row = [label]
        for _name, values in series:
            row.append(values[index] if index < len(values) else "")
        rows.append(row)
    kind = "line" if bars == 0 else "bar"
    unit = _unit(f"{title} {y_title} {x_title}")
    info = f"chart {kind}"
    if unit:
        info += f" unit={unit}"
    if x_title:
        info += f" x={_token(x_title)}"
    if y_title or title:
        info += f" y={_token(y_title or title)}"
    body_lines = [" | ".join(headers)]
    body_lines.extend(" | ".join(row) for row in rows)
    return info, "\n".join(body_lines), headers, rows


def _flow_source(content: str) -> tuple[str, str]:
    direction = "TB"
    for raw in content.splitlines():
        match = re.match(r"(?:flowchart|graph)\s+(TB|TD|BT|LR|RL)\b", raw.strip(), re.I)
        if match:
            direction = "LR" if match.group(1).upper() in {"LR", "RL"} else "TB"
            break
    lines: list[str] = []
    for raw in content.splitlines():
        text = raw.strip().rstrip(";")
        if not text or text.startswith("%%") or _DIRECTIVE.match(text):
            continue
        edge = _convert_edge(text)
        if edge:
            lines.append(edge)
        if len(lines) >= 48:
            break
    return f"flow {direction}", "\n".join(lines)


def _convert_edge(text: str) -> str:
    labeled = _LABELED_EDGE.match(text)
    if labeled:
        src = _node_label(labeled.group("src"))
        dst = _node_label(labeled.group("dst"))
        if src and dst:
            return _edge_line(src, dst, labeled.group("label") or "", dashed=False)
    match = _EDGE.match(text)
    if match is None:
        return ""
    src = _node_label(match.group("src"))
    dst = _node_label(match.group("dst"))
    if not src or not dst:
        return ""
    dashed = match.group("arrow") in {"-.->", "---"}
    return _edge_line(src, dst, match.group("label") or "", dashed=dashed)


def _edge_line(src: str, dst: str, label: str, *, dashed: bool) -> str:
    arrow = "-->" if dashed else "->"
    line = f"{src} {arrow} {dst}"
    safe = " ".join(label.replace(":", " ").replace("->", " ").split())[:80]
    if safe:
        line += f" : {safe}"
    return line


def _node_label(token: str) -> str | None:
    text = token.strip().strip(";").strip()
    text = re.sub(r":::[\w-]+$", "", text).strip()
    if not text:
        return None
    match = _NODE.match(text)
    if match is None:
        return None
    ident = match.group("id")
    rest = (match.group("rest") or "").strip()
    if not rest:
        return ident[:60]
    shape, label = _unwrap(rest)
    label = label.strip().strip("\"'").strip()
    label = " ".join(label.replace("->", " ").replace(":", " ").split())
    if not label:
        label = ident
    shown = label[:60]
    if shape == "diamond":
        return "{" + shown + "}"
    if shape == "pill":
        return "(" + shown + ")"
    return shown


def _unwrap(rest: str) -> tuple[str, str]:
    pairs = (
        ("{{", "}}", "box"),
        ("((", "))", "pill"),
        ("[[", "]]", "box"),
        ("[(", ")]", "box"),
        ("{", "}", "diamond"),
        ("(", ")", "pill"),
        ("[", "]", "box"),
    )
    for start, end, shape in pairs:
        if rest.startswith(start) and rest.endswith(end) and len(rest) >= len(start) + len(end):
            return shape, rest[len(start) : len(rest) - len(end)]
    return "box", rest


def _axis_labels(text: str) -> tuple[str, list[str]]:
    title = _quoted_title(text)
    match = re.search(r"\[(.*)]", text)
    body = match.group(1) if match else text
    if match is None:
        body = re.sub(r'^\s*"[^"]*"\s*', "", body)
        body = re.sub(r"^\s*'[^']*'\s*", "", body)
    labels = []
    for part in body.split(","):
        item = part.strip().strip("\"'")
        if item and not re.fullmatch(r"(?:-->|---)", item):
            labels.append(item[:80])
    return title, labels


def _number_list(text: str) -> list[str] | None:
    match = re.search(r"\[(.*)]", text)
    body = match.group(1) if match else text
    parts = [part.strip().strip("\"'") for part in body.split(",") if part.strip()]
    if not parts or not any(_NUMBER.match(part) for part in parts):
        return None
    return parts


def _quoted_title(text: str) -> str:
    match = re.search(r'"([^"]+)"|\'([^\']+)\'', text)
    if not match:
        return ""
    return _clean_title(match.group(1) or match.group(2) or "")


def _clean_title(text: str) -> str:
    return " ".join(text.strip().strip("\"'").split())[:40]


def _token(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in " %$€£" else " " for ch in text)
    return "_".join(cleaned.split())[:40]


def _unit(text: str) -> str:
    lowered = text.lower()
    if "$" in text or "usd" in lowered:
        return "$"
    if "%" in text or "percent" in lowered:
        return "%"
    return ""


def _series_name(name: str, index: int, count: int) -> str:
    if name:
        return name
    if count == 1:
        return "Value"
    return f"Series {index + 1}"


def _lang(info: str) -> str:
    parts = (info or "").strip().split()
    return parts[0].lower() if parts else ""


def _body_is_mermaid(content: str) -> bool:
    for raw in content.splitlines():
        text = raw.strip()
        if text:
            return _BODY_MERMAID.match(text) is not None
    return False
