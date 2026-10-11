"""Layout tree and the attribute allow-list. Anything else is dropped."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

ALLOWED_ATTRS: frozenset[str] = frozenset(
    {"tone", "size", "variant", "groups", "span", "cols", "pick", "phone", "class"}
)
ALLOWED_CLASSES: frozenset[str] = frozenset(
    {"emph", "rowhead", "signs", "total", "accent", "blue", "green", "amber", "red", "violet", "gray"}
)
TONES: frozenset[str] = frozenset({"accent", "blue", "green", "amber", "red", "violet", "gray"})
SIZES: frozenset[str] = frozenset({"sm", "lg", "xl"})
CALLOUTS: frozenset[str] = frozenset({"note", "warn", "success", "danger"})
CALL_LABELS: dict[str, str] = {"note": "Note", "warn": "Warning", "success": "Done", "danger": "Problem"}
_VARIANT = re.compile(r"^[a-z]{2,12}$")


class LayoutError(Exception):
    """The Markdown is not a usable layout page."""


@dataclass
class Cell:
    html: str
    plain: str
    colspan: int = 1


@dataclass
class Block:
    kind: str
    html: str = ""
    plain: str = ""
    level: int = 0
    classes: tuple[str, ...] = ()
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Block] = field(default_factory=list)
    headers: list[Cell] = field(default_factory=list)
    rows: list[list[Cell]] = field(default_factory=list)
    groups: tuple[tuple[str, int], ...] = ()
    labels: tuple[str, ...] = ()
    numbers: tuple[float, ...] = ()


def classify(raw: dict[str, Any] | None) -> tuple[dict[str, str], tuple[str, ...]]:
    """Keep allow-listed keys and classes. A bad tone becomes accent."""
    attrs: dict[str, str] = {}
    classes: list[str] = []
    for key, value in (raw or {}).items():
        if key not in ALLOWED_ATTRS:
            continue
        text = str(value).strip()
        if key == "class":
            for tok in text.split():
                if tok in ALLOWED_CLASSES and tok not in classes:
                    classes.append(tok)
            continue
        if key == "tone":
            attrs["tone"] = text if text in TONES else "accent"
        elif key == "size":
            if text in SIZES:
                attrs["size"] = text
        elif key in {"span", "cols", "pick"}:
            if text.isdigit() and 1 <= int(text) <= 12:
                attrs[key] = text
        elif key == "phone":
            if text in {"stack", "scroll"}:
                attrs["phone"] = text
        elif key == "groups":
            if text and len(text) <= 400 and "<" not in text and ">" not in text:
                attrs["groups"] = text
        elif key == "variant" and _VARIANT.fullmatch(text):
            attrs["variant"] = text
    return attrs, tuple(classes)


def class_attr(*parts: str) -> str:
    names = [part for part in parts if part]
    if not names:
        return ""
    return f' class="{" ".join(names)}"'
