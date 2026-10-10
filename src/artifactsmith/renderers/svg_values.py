"""Value contract for the drawing-only SVG allowlist (geometry and paint)."""

from __future__ import annotations

import math
import re
from html import unescape

_MAX_ABS = 1_000_000.0
_NUM = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_FUNC = re.compile(
    r"^(?P<name>translate|scale|rotate|skewX|skewY|matrix)\(\s*(?P<args>[^)]*)\s*\)$",
    re.I,
)
_COLOR_FN = re.compile(
    r"^(?P<fn>rgba?|hsla?)\(\s*(?P<args>[^)]*)\s*\)$",
    re.I,
)
_NAMED_COLORS = frozenset(
    """
    aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond
    blue blueviolet brown burlywood cadetblue chartreuse chocolate coral
    cornflowerblue cornsilk crimson cyan darkblue darkcyan darkgoldenrod darkgray
    darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange darkorchid
    darkred darksalmon darkseagreen darkslateblue darkslategray darkslategrey
    darkturquoise darkviolet deeppink deepskyblue dimgray dimgrey dodgerblue
    firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod
    gray green greenyellow grey honeydew hotpink indianred indigo ivory khaki
    lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan
    lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon
    lightseagreen lightskyblue lightslategray lightslategrey lightsteelblue
    lightyellow lime limegreen linen magenta maroon mediumaquamarine mediumblue
    mediumorchid mediumpurple mediumseagreen mediumslateblue mediumspringgreen
    mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin
    navajowhite navy oldlace olive olivedrab orange orangered orchid palegoldenrod
    palegreen paleturquoise palevioletred papayawhip peachpuff peru pink plum
    powderblue purple rebeccapurple red rosybrown royalblue saddlebrown salmon
    sandybrown seagreen seashell sienna silver skyblue slateblue slategray
    slategrey snow springgreen steelblue tan teal thistle tomato turquoise violet
    wheat white whitesmoke yellow yellowgreen
    """.split()
)
_GENERIC_FONTS = frozenset(
    {"serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui", "ui-sans-serif", "ui-serif"}
)
_ALIGN = frozenset(
    {
        "xminymin",
        "xmidymin",
        "xmaxymin",
        "xminymid",
        "xmidymid",
        "xmaxymid",
        "xminymax",
        "xmidymax",
        "xmaxymax",
    }
)
_C0_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_FETCH_RE = re.compile(r"url\s*\(|javascript:|vbscript:|data:", re.I)


def has_fetch_or_scheme(value: str) -> bool:
    """True when a decoded value can fetch, navigate, or run a scheme handler."""
    text = unescape(value)
    compact = re.sub(r"\s+", "", text)
    return bool(_FETCH_RE.search(compact)) or "url(" in compact.lower().replace("\\", "")


def has_c0(value: str) -> bool:
    return bool(_C0_RE.search(value))


def parse_number(value: str, *, minimum: float | None = None) -> float | None:
    raw = value.strip()
    if not _NUM.fullmatch(raw):
        return None
    try:
        number = float(raw)
    except ValueError:
        return None
    if not math.isfinite(number) or abs(number) > _MAX_ABS:
        return None
    if minimum is not None and number < minimum:
        return None
    return number


def _split_unit(value: str, units: frozenset[str]) -> tuple[str, str] | None:
    raw = value.strip()
    lower = raw.lower()
    for unit in sorted(units, key=len, reverse=True):
        if lower.endswith(unit) and unit:
            return raw[: -len(unit)].strip(), unit
    return raw, ""


def number_with_unit(
    value: str,
    *,
    units: frozenset[str],
    minimum: float | None = None,
    allow_auto: bool = False,
) -> bool:
    raw = value.strip()
    if allow_auto and raw.lower() == "auto":
        return True
    split = _split_unit(raw, units)
    if split is None:
        return False
    return parse_number(split[0], minimum=minimum) is not None


def _number_list(value: str, *, minimum: float | None = None, limit: int) -> list[float] | None:
    parts = [p for p in re.split(r"[,\s]+", value.strip()) if p]
    if len(parts) > limit:
        return None
    out: list[float] = []
    for part in parts:
        number = parse_number(part, minimum=minimum)
        if number is None:
            return None
        out.append(number)
    return out


def color_ok(value: str) -> bool:
    raw = value.strip()
    low = raw.lower()
    if low in {"none", "currentcolor", "transparent"} or low in _NAMED_COLORS:
        return True
    if _HEX.fullmatch(raw):
        return True
    match = _COLOR_FN.fullmatch(raw)
    if not match:
        return False
    args = [p for p in re.split(r"[,/\s]+", match.group("args").strip()) if p]
    name = match.group("fn").lower()
    need = 4 if name in {"rgba", "hsla"} else 3
    if len(args) != need and not (len(args) == 4 and name in {"rgb", "hsl"}):
        return False
    return all(parse_number(a[:-1] if a.endswith("%") else a, minimum=None) is not None for a in args)


def opacity_ok(value: str) -> bool:
    raw = value.strip()
    if raw.endswith("%"):
        number = parse_number(raw[:-1].strip(), minimum=0.0)
        return number is not None and number <= 100.0
    number = parse_number(raw, minimum=0.0)
    return number is not None and number <= 1.0


def viewbox_ok(value: str) -> bool:
    nums = _number_list(value, limit=4)
    return nums is not None and len(nums) == 4 and nums[2] >= 0 and nums[3] >= 0


def preserve_aspect_ok(value: str) -> bool:
    parts = value.strip().split()
    if not parts or len(parts) > 2:
        return False
    first = parts[0]
    if first.lower() == "none":
        return len(parts) == 1
    if first.lower() not in _ALIGN:
        return False
    return len(parts) == 1 or parts[1].lower() in {"meet", "slice"}


def points_ok(value: str) -> tuple[int, bool]:
    nums = _number_list(value, limit=10_000)
    if nums is None or len(nums) % 2 != 0:
        return 0, False
    return len(nums), True


def class_ok(value: str) -> bool:
    tokens = value.split()
    return 1 <= len(tokens) <= 16 and all(_IDENT.fullmatch(tok) and len(tok) <= 64 for tok in tokens)


def font_family_ok(value: str) -> bool:
    if len(value) > 200:
        return False
    names = [n.strip() for n in value.split(",")]
    if not names or len(names) > 8:
        return False
    for name in names:
        if (name.startswith('"') and name.endswith('"')) or (name.startswith("'") and name.endswith("'")):
            name = name[1:-1].strip()
        if name.lower() in _GENERIC_FONTS:
            continue
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 \-]*", name):
            return False
    return True


def transform_ok(value: str) -> bool:
    raw = value.strip()
    if not raw:
        return False
    funcs = re.findall(r"[a-zA-Z]+\s*\([^)]*\)", raw)
    joined = "".join(funcs)
    if re.sub(r"\s+", "", joined) != re.sub(r"\s+", "", raw) or len(funcs) > 8:
        return False
    arity = {"translate": (1, 2), "scale": (1, 2), "rotate": (1, 3), "skewx": (1, 1), "skewy": (1, 1), "matrix": (6, 6)}
    for func in funcs:
        match = _FUNC.fullmatch(func.strip())
        if not match:
            return False
        args = _number_list(match.group("args"), limit=6)
        lo, hi = arity[match.group("name").lower()]
        if args is None or not lo <= len(args) <= hi:
            return False
    return True


def attribute_ok(name: str, value: str) -> bool:
    """True when a decoded SVG attribute value matches the allowlist contract."""
    if has_c0(value) or has_fetch_or_scheme(value) or "var(" in value.lower():
        return False
    key = name
    if key in {"x", "y", "x1", "y1", "x2", "y2", "cx", "cy"}:
        return number_with_unit(value, units=frozenset({"px", "%"}))
    if key in {"width", "height", "r"}:
        return number_with_unit(value, units=frozenset({"px", "%", "em"}), minimum=0.0)
    if key in {"rx", "ry"}:
        return number_with_unit(value, units=frozenset({"px", "%", "em"}), minimum=0.0, allow_auto=True)
    if key == "stroke-width":
        return number_with_unit(value, units=frozenset({"px"}), minimum=0.0)
    if key == "font-size":
        return number_with_unit(value, units=frozenset({"px", "em", "rem", "%"}), minimum=0.0)
    if key in {"opacity", "fill-opacity", "stroke-opacity"}:
        return opacity_ok(value)
    if key in {"fill", "stroke"}:
        return color_ok(value)
    if key == "stroke-dasharray":
        return value.strip().lower() == "none" or _number_list(value, minimum=0.0, limit=16) is not None
    if key == "stroke-linecap":
        return value.strip().lower() in {"butt", "round", "square"}
    if key == "stroke-linejoin":
        return value.strip().lower() in {"miter", "round", "bevel"}
    if key == "text-anchor":
        return value.strip().lower() in {"start", "middle", "end"}
    if key == "dominant-baseline":
        return value.strip().lower() in {"auto", "middle", "central", "hanging", "alphabetic"}
    if key == "font-weight":
        raw = value.strip().lower()
        if raw in {"normal", "bold", "bolder", "lighter"}:
            return True
        number = parse_number(raw, minimum=100.0)
        return number is not None and number <= 900 and number % 100 == 0
    if key == "font-family":
        return font_family_ok(value)
    if key == "viewBox":
        return viewbox_ok(value)
    if key == "preserveAspectRatio":
        return preserve_aspect_ok(value)
    if key == "points":
        return points_ok(value)[1]
    if key == "d":
        from .svg_path import parse_path

        parsed = parse_path(value)
        return parsed is not None and parsed[0] <= 2000
    if key == "transform":
        return transform_ok(value)
    if key == "class":
        return class_ok(value)
    if key == "id":
        return bool(_IDENT.fullmatch(value.strip()) and len(value.strip()) <= 64)
    if key == "role":
        return value.strip().lower() in {"img", "presentation"}
    if key == "aria-hidden":
        return value.strip().lower() in {"true", "false"}
    if key == "aria-label":
        return 0 < len(value) <= 300
    if key == "aria-labelledby":
        tokens = value.split()
        return 1 <= len(tokens) <= 4 and all(_IDENT.fullmatch(tok) and len(tok) <= 64 for tok in tokens)
    return False
