"""Tight drawing-only SVG allowlist on top of nh3.

nh3 cannot see namespaces or ancestors, so a second pass unwraps SVG ``<a>``,
keeps ``desc`` text-only, and drops any leftover active content. Style values
reuse the existing CSS sanitizer; every other value uses :mod:`svg_values`.
"""

from __future__ import annotations

from .css_sanitize import sanitize_inline_style
from .svg_path import parse_path
from .svg_tree import Node, parse_fragment, serialize, svg_root_spans
from .svg_values import attribute_ok, has_c0, has_fetch_or_scheme, points_ok

SVG_TAGS: frozenset[str] = frozenset(
    {"svg", "g", "rect", "line", "polyline", "polygon", "path", "circle", "ellipse", "text", "tspan", "desc"}
)
# Removed with their content so children cannot leak into the drawing tree.
SVG_CLEAN_CONTENT: frozenset[str] = frozenset(
    {
        "use",
        "image",
        "foreignobject",
        "animate",
        "animatemotion",
        "animatetransform",
        "set",
        "filter",
        "pattern",
        "mask",
        "clippath",
        "defs",
        "symbol",
        "marker",
        "lineargradient",
        "radialgradient",
        "style",
        "script",
        "font",
        "switch",
        "view",
        "feimage",
        "feturbulence",
        "fecomposite",
    }
)

_SHARED = frozenset(
    {
        "class",
        "id",
        "style",
        "aria-hidden",
        "fill",
        "stroke",
        "stroke-width",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-dasharray",
        "opacity",
        "fill-opacity",
        "stroke-opacity",
        "transform",
    }
)
_ATTRS: dict[str, frozenset[str]] = {
    "svg": _SHARED
    | frozenset({"viewBox", "width", "height", "preserveAspectRatio", "role", "aria-label", "aria-labelledby"}),
    "g": _SHARED,
    "rect": _SHARED | frozenset({"x", "y", "width", "height", "rx", "ry"}),
    "line": _SHARED | frozenset({"x1", "y1", "x2", "y2"}),
    "polyline": _SHARED | frozenset({"points"}),
    "polygon": _SHARED | frozenset({"points"}),
    "path": _SHARED | frozenset({"d"}),
    "circle": _SHARED | frozenset({"cx", "cy", "r"}),
    "ellipse": _SHARED | frozenset({"cx", "cy", "rx", "ry"}),
    "text": _SHARED
    | frozenset({"x", "y", "font-size", "font-family", "font-weight", "text-anchor", "dominant-baseline"}),
    "tspan": _SHARED
    | frozenset({"x", "y", "font-size", "font-family", "font-weight", "text-anchor", "dominant-baseline"}),
    "desc": frozenset({"class", "id", "aria-hidden"}),
}
_CANON = {"viewbox": "viewBox", "preserveaspectratio": "preserveAspectRatio"}
_MAX_ROOTS = 50
_MAX_ELEMENTS = 2000
_MAX_DEPTH = 16
_MAX_ATTR_BYTES = 256 * 1024
_MAX_PATH_COMMANDS = 5000
_MAX_NUMBERS = 20_000
_MAX_TEXT_NODES = 1000
_MAX_TEXT_CHARS = 20_000


class _Budget:
    def __init__(self) -> None:
        self.roots = 0
        self.elements = 0
        self.attr_bytes = 0
        self.path_commands = 0
        self.numbers = 0
        self.text_nodes = 0
        self.text_chars = 0
        self.over = False
        self.used_ids: set[str] = set()
        self.page_ids: set[str] = set()

    def add_root(self) -> None:
        self.roots += 1
        if self.roots > _MAX_ROOTS:
            self.over = True

    def add_element(self, depth: int) -> None:
        self.elements += 1
        if self.elements > _MAX_ELEMENTS or depth > _MAX_DEPTH:
            self.over = True

    def add_attr(self, value: str) -> None:
        self.attr_bytes += len(value.encode("utf-8"))
        if self.attr_bytes > _MAX_ATTR_BYTES:
            self.over = True

    def add_path(self, d: str) -> None:
        parsed = parse_path(d)
        if parsed:
            self.path_commands += parsed[0]
            self.numbers += parsed[1]
        if self.path_commands > _MAX_PATH_COMMANDS or self.numbers > _MAX_NUMBERS:
            self.over = True

    def add_points(self, value: str) -> None:
        count, _ok = points_ok(value)
        self.numbers += count
        if self.numbers > _MAX_NUMBERS:
            self.over = True

    def add_text(self, text: str) -> None:
        if not text:
            return
        self.text_nodes += 1
        self.text_chars += len(text)
        if self.text_nodes > _MAX_TEXT_NODES or self.text_chars > _MAX_TEXT_CHARS:
            self.over = True


def svg_allowed_attrs() -> dict[str, set[str]]:
    """Attribute names nh3 may keep; the filter still applies the value contract."""
    out: dict[str, set[str]] = {}
    for tag, names in _ATTRS.items():
        out[tag] = set(names) | {"style", "viewbox", "preserveaspectratio"}
    return out


def canonical_svg_attr(name: str) -> str:
    return _CANON.get(name.lower(), name)


def svg_attr_allowed(tag: str, attr: str) -> bool:
    return canonical_svg_attr(attr) in _ATTRS.get(tag, frozenset())


def filter_svg_attribute(tag: str, attr: str, value: str) -> str | None:
    """nh3 callback: keep geometry/presentation values that pass the contract."""
    if ":" in attr or attr.lower().startswith("on") or attr.lower() in {"href", "xlink:href"}:
        return None
    if has_c0(value) or has_fetch_or_scheme(value):
        return None
    if attr.lower() == "style":
        cleaned = sanitize_inline_style(value)
        return cleaned or None
    if not svg_attr_allowed(tag, attr):
        return None
    if not attribute_ok(canonical_svg_attr(attr), value):
        return None
    return value


def _collect_ids(node: Node, ids: set[str]) -> None:
    for raw_name, raw_value in node.attrs:
        if canonical_svg_attr(raw_name) == "id" and attribute_ok("id", raw_value):
            ids.add(raw_value.strip())
    for child in node.children:
        if isinstance(child, Node):
            _collect_ids(child, ids)


def _charge_attr(name: str, value: str, budget: _Budget) -> None:
    budget.add_attr(value)
    if name == "d":
        budget.add_path(value)
    elif name == "points":
        budget.add_points(value)


def _clean_attrs(tag: str, attrs: list[tuple[str, str]], budget: _Budget, page_ids: set[str]) -> list[tuple[str, str]]:
    kept: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw_name, raw_value in attrs:
        name = canonical_svg_attr(raw_name)
        if name.lower() in seen:
            continue
        filtered = filter_svg_attribute(tag, raw_name, raw_value)
        if filtered is None:
            continue
        if name == "id":
            key = filtered.strip()
            if key in budget.used_ids:
                continue
            budget.used_ids.add(key)
        if name == "aria-labelledby" and any(tok not in page_ids for tok in filtered.split()):
            continue
        seen.add(name.lower())
        _charge_attr(name, filtered, budget)
        kept.append((name, filtered))
    return kept


def _sanitize_node(node: Node, budget: _Budget, depth: int, in_svg: bool) -> list[Node | str]:
    tag = node.tag
    if ":" in tag or tag in SVG_CLEAN_CONTENT:
        return []
    if tag == "a" and in_svg:
        out: list[Node | str] = []
        for child in node.children:
            if isinstance(child, str):
                budget.add_text(child)
                if child:
                    out.append(child)
            else:
                out.extend(_sanitize_node(child, budget, depth, True))
        return out
    if tag in SVG_TAGS:
        if not in_svg and tag != "svg":
            return []
        next_depth = depth + 1
        if tag == "svg":
            budget.add_root()
        budget.add_element(next_depth)
        cleaned = Node(tag, _clean_attrs(tag, node.attrs, budget, budget.page_ids))
        if tag == "desc":
            text = "".join(c if isinstance(c, str) else "" for c in node.children)
            budget.add_text(text)
            if text:
                cleaned.children.append(text)
        else:
            for child in node.children:
                if isinstance(child, str):
                    budget.add_text(child)
                    if child:
                        cleaned.children.append(child)
                else:
                    cleaned.children.extend(_sanitize_node(child, budget, next_depth, True))
        if budget.over and tag == "svg":
            return []
        return [cleaned]
    if in_svg:
        return []
    return [node]


def _account(node: Node, budget: _Budget, depth: int, in_svg: bool) -> None:
    """Charge page-wide limits without rewriting. Removed-root usage still counts."""
    tag = node.tag
    if not tag:
        for child in node.children:
            if isinstance(child, str):
                continue
            _account(child, budget, depth, in_svg)
        return
    if tag == "a" and in_svg:
        for child in node.children:
            if isinstance(child, str):
                budget.add_text(child)
            else:
                _account(child, budget, depth, True)
        return
    if tag not in SVG_TAGS:
        if in_svg:
            return
        for child in node.children:
            if isinstance(child, Node):
                _account(child, budget, depth, False)
        return
    if not in_svg and tag != "svg":
        return
    next_depth = depth + 1
    if tag == "svg":
        budget.add_root()
    budget.add_element(next_depth)
    for raw_name, raw_value in node.attrs:
        name = canonical_svg_attr(raw_name)
        filtered = filter_svg_attribute(tag, raw_name, raw_value)
        if filtered is None:
            continue
        _charge_attr(name, filtered, budget)
    if tag == "desc":
        budget.add_text("".join(c if isinstance(c, str) else "" for c in node.children))
        return
    for child in node.children:
        if isinstance(child, str):
            budget.add_text(child)
        else:
            _account(child, budget, next_depth, True)


def _svg_already_clean(node: Node, in_svg: bool, page_ids: set[str]) -> bool:
    tag = node.tag
    if not tag:
        return all(isinstance(c, str) or _svg_already_clean(c, in_svg, page_ids) for c in node.children)
    if ":" in tag or tag in SVG_CLEAN_CONTENT:
        return False
    if tag == "a" and in_svg:
        return False
    if tag in SVG_TAGS:
        if not in_svg and tag != "svg":
            return False
        allowed = _ATTRS[tag]
        seen: set[str] = set()
        for name, value in node.attrs:
            canon = canonical_svg_attr(name)
            if canon.lower() in seen or canon not in allowed:
                return False
            if filter_svg_attribute(tag, name, value) is None:
                return False
            if canon == "aria-labelledby" and any(tok not in page_ids for tok in value.split()):
                return False
            seen.add(canon.lower())
        if tag == "desc" and any(isinstance(c, Node) for c in node.children):
            return False
        return all(isinstance(c, str) or _svg_already_clean(c, True, page_ids) for c in node.children)
    if in_svg:
        return False
    return all(isinstance(c, str) or _svg_already_clean(c, False, page_ids) for c in node.children)


def sanitize_svg_fragment(html: str) -> str:
    """Enforce page-wide SVG limits; keep already-safe markup when it fits."""
    if "<svg" not in html.lower():
        return html
    tree = parse_fragment(html)
    counted = _Budget()
    _account(tree, counted, 0, False)
    page_ids: set[str] = set()
    _collect_ids(tree, page_ids)
    if not counted.over and _svg_already_clean(tree, False, page_ids):
        return html
    budget = _Budget()
    budget.page_ids = page_ids
    parts: list[str] = []
    last = 0
    for start, end in svg_root_spans(html):
        parts.append(html[last:start])
        parsed = parse_fragment(html[start:end])
        rewritten: list[str] = []
        for child in parsed.children:
            if isinstance(child, str):
                rewritten.append(child)
                continue
            for kept in _sanitize_node(child, budget, 0, False):
                rewritten.append(kept if isinstance(kept, str) else serialize(kept))
        parts.append("".join(rewritten))
        last = end
    parts.append(html[last:])
    return "".join(parts)
