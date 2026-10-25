"""SVG pieces for server-drawn diagrams.

No markers, scripts, or fetches. Arrow heads are polygons because the sanitizer
drops ``<marker>``. Colors come from stylesheet classes, not from the model.
"""

from __future__ import annotations

import math
from html import escape

from .chart_scale import chart_width_budget, text_width

# Same phone column as charts. A 390px viewport, after page padding, is wider
# than this, so type sized to the column stays at least 11px there.
FONT = 14.0
LINE = FONT + 4.0
BUDGET = chart_width_budget(FONT)
LIMIT = float(math.floor(BUDGET))


def esc(text: str) -> str:
    return escape(text, quote=True)


def n(value: float) -> str:
    if abs(value - round(value)) < 0.05:
        return str(int(round(value)))
    return f"{value:.1f}"


def clean(text: str, limit: int) -> str:
    kept = "".join(ch for ch in text if ch >= " " or ch == " ")
    kept = " ".join(kept.split())
    return kept[:limit]


def aria(kind: str, parts: list[str]) -> str:
    """A short label. Scheme-like tokens are removed so the attribute survives."""
    title = {"flow": "Flow", "sequence": "Sequence", "timeline": "Timeline", "tree": "Tree"}.get(kind, "Diagram")
    text = title if not parts else title + ": " + "; ".join(parts)
    text = "".join(ch for ch in text if ch >= " " or ch == " ")
    lowered = text.lower()
    for token in ("javascript:", "vbscript:", "data:", "url("):
        while token in lowered:
            at = lowered.index(token)
            text = text[:at] + text[at + len(token) :]
            lowered = text.lower()
    text = " ".join(text.split())
    if len(text) > 280:
        text = text[:277].rstrip() + "..."
    return text or title


def wrap_lines(text: str, limit: float, font: float, *, max_lines: int = 3) -> tuple[list[str], str]:
    """Visible lines, plus the original when a line was cut for a tooltip."""
    raw = clean(text, 200)
    if not raw:
        return [], ""
    limit = max(limit, font)
    if text_width(raw, font) <= limit:
        return [raw], ""
    words = raw.split(" ")
    lines: list[str] = []
    current = ""
    cut = False
    for word in words:
        trial = word if not current else f"{current} {word}"
        if text_width(trial, font) <= limit:
            current = trial
            continue
        if current:
            lines.append(current)
            if len(lines) >= max_lines:
                cut = True
                current = ""
                break
        if text_width(word, font) <= limit:
            current = word
            continue
        lines.append(_cut(word, limit, font))
        cut = True
        current = ""
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    elif current:
        cut = True
    if len(lines) >= max_lines and sum(len(line) for line in lines) + len(lines) < len(raw):
        cut = True
    if not lines:
        lines = [_cut(raw, limit, font)]
        cut = True
    return lines, raw if cut else ""


def _cut(text: str, limit: float, font: float) -> str:
    if text_width(text, font) <= limit:
        return text
    chars = list(text)
    while len(chars) > 1 and text_width("".join(chars) + "…", font) > limit:
        chars.pop()
    return "".join(chars) + "…"


def svg_doc(width: float, height: float, label: str, parts: list[str]) -> str:
    w = max(1, int(math.ceil(width)))
    h = max(1, int(math.ceil(height)))
    body = "".join(parts)
    # Natural width keeps type near FONT (about 14px) on a wide column.
    # min(100%, …) still shrinks it on a phone, where the viewBox stays
    # inside the 11px budget.
    style = f"width:min(100%,{w}px);height:auto"
    return (
        f'<svg role="img" aria-label="{esc(label)}" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}" style="{style}">{body}</svg>'
    )


def text_at(
    x: float,
    y: float,
    shown: str,
    font: float,
    *,
    anchor: str = "start",
    full: str = "",
    css: str = "",
    weight: str = "",
) -> str:
    attrs = [f'x="{n(x)}"', f'y="{n(y)}"', f'font-size="{font:.0f}"', 'dominant-baseline="middle"']
    if anchor != "start":
        attrs.append(f'text-anchor="{anchor}"')
    if css:
        attrs.append(f'class="{css}"')
    if weight:
        attrs.append(f'font-weight="{weight}"')
    tip = f"<title>{esc(full)}</title>" if full and full != shown else ""
    return f"<text {' '.join(attrs)}>{tip}{esc(shown)}</text>"


def rect(x: float, y: float, w: float, h: float, css: str, *, rx: float = 6) -> str:
    return f'<rect class="{css}" x="{n(x)}" y="{n(y)}" width="{n(w)}" height="{n(h)}" rx="{n(rx)}" ry="{n(rx)}"/>'


def line(x1: float, y1: float, x2: float, y2: float, css: str) -> str:
    return f'<line class="{css}" x1="{n(x1)}" y1="{n(y1)}" x2="{n(x2)}" y2="{n(y2)}"/>'


def poly(points: list[tuple[float, float]], css: str) -> str:
    body = " ".join(f"{n(x)},{n(y)}" for x, y in points)
    return f'<polygon class="{css}" points="{body}"/>'


def arrow(x1: float, y1: float, x2: float, y2: float, *, dashed: bool) -> str:
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    tip = 9.0
    wing = 4.5
    ax, ay = x2 - ux * tip, y2 - uy * tip
    css = "edge dash" if dashed else "edge"
    shaft = line(x1, y1, ax, ay, css)
    head = poly([(x2, y2), (ax + px * wing, ay + py * wing), (ax - px * wing, ay - py * wing)], "arrow")
    return shaft + head


def chip(x: float, y: float, shown: str, font: float, *, anchor: str = "middle", full: str = "") -> str:
    width = text_width(shown, font) + 8
    left = x - width / 2 if anchor == "middle" else x
    top = y - font * 0.7
    return rect(left, top, width, font + 6, "tag", rx=3) + text_at(
        x, y, shown, font, anchor=anchor, full=full, weight=""
    )


def fits(width: float) -> bool:
    return width <= LIMIT
