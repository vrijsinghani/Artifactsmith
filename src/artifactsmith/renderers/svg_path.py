"""SVG path grammar used by the drawing-only allowlist."""

from __future__ import annotations

from .svg_values import parse_number

_PATH_ARITY = {
    "M": 2,
    "L": 2,
    "H": 1,
    "V": 1,
    "C": 6,
    "S": 4,
    "Q": 4,
    "T": 2,
    "A": 7,
    "Z": 0,
}


def _read_number(src: str, i: int) -> tuple[float, int] | None:
    n = len(src)
    while i < n and src[i] in " \t\n\r,":
        i += 1
    if i >= n:
        return None
    start = i
    if src[i] in "+-":
        i += 1
    seen_digit = False
    while i < n and src[i].isdigit():
        seen_digit = True
        i += 1
    if i < n and src[i] == ".":
        i += 1
        while i < n and src[i].isdigit():
            seen_digit = True
            i += 1
    if not seen_digit:
        return None
    if i < n and src[i] in "eE":
        j = i + 1
        if j < n and src[j] in "+-":
            j += 1
        if j < n and src[j].isdigit():
            i = j
            while i < n and src[i].isdigit():
                i += 1
        else:
            return None
    number = parse_number(src[start:i])
    if number is None:
        return None
    return number, i


def _read_flag(src: str, i: int) -> tuple[int, int] | None:
    n = len(src)
    while i < n and src[i] in " \t\n\r,":
        i += 1
    if i < n and src[i] in "01":
        return int(src[i]), i + 1
    return None


def parse_path(d: str) -> tuple[int, int] | None:
    """Return (command_count, number_count) or None when ``d`` is not a path."""
    src = d.strip()
    if not src:
        return None
    i = 0
    n = len(src)
    commands = 0
    numbers = 0
    implicit = ""
    while i < n:
        while i < n and src[i] in " \t\n\r,":
            i += 1
        if i >= n:
            break
        ch = src[i]
        if ch.isalpha():
            cmd = ch.upper()
            if cmd not in _PATH_ARITY:
                return None
            i += 1
            implicit = "L" if cmd == "M" else cmd
            first = True
        elif implicit:
            cmd = implicit
            first = False
        else:
            return None
        arity = _PATH_ARITY[cmd]
        if cmd == "Z":
            commands += 1
            implicit = ""
            continue
        if cmd == "A":
            args: list[float] = []
            for idx in range(7):
                if idx in {3, 4}:
                    flag = _read_flag(src, i)
                    if flag is None:
                        return None
                    args.append(float(flag[0]))
                    i = flag[1]
                else:
                    parsed = _read_number(src, i)
                    if parsed is None:
                        return None
                    args.append(parsed[0])
                    i = parsed[1]
            if args[0] < 0 or args[1] < 0:
                return None
            commands += 1
            numbers += 7
            implicit = "A"
            continue
        got = 0
        while got < arity:
            parsed = _read_number(src, i)
            if parsed is None:
                return None
            i = parsed[1]
            got += 1
            numbers += 1
        if cmd == "M" and first:
            implicit = "L"
        commands += 1
    return commands, numbers
