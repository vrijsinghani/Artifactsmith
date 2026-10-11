"""Edit patches: the model returns exact find/replace spans; the server applies them.

The format is line-oriented on purpose. Mid-size models copy a short unique span more
reliably than they reproduce a whole page or a unified diff with line numbers.

A hunk matches the original base file, never an earlier replacement. Overlaps, empty
finds, and spans that are missing or ambiguous are errors. The caller then falls back
to a full rewrite. This module does not call a model and does not sanitize.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_HUNKS = 32

# Appended after the normal system prompt on the patch call only. Last, so it overrides
# the complete-file reply format. The full-rewrite call does not include this text.
PATCH_SYSTEM_ADDENDUM = """
EDIT OUTPUT FORMAT:
This call is an edit. Ignore the complete-file reply format above. Do not reproduce the base file.
Reply in EXACTLY this format and nothing else (no code fence around the reply):
===ASSUMPTIONS===
- one assumption per line (or "- none")
===SUMMARY===
one or two plain sentences describing only what changed
===PATCH===
<<<FIND>>>
<exact lines copied from the base file; the span must occur once>
<<<REPLACE>>>
<full new text for that span, or nothing if the span is deleted>
<<<END>>>
===END===

Patch rules:
- One or more FIND/REPLACE/END blocks. FIND is copied character for character from the base file.
- Every FIND is matched against the original base file, not against earlier replacements.
- When a short FIND would match more than once, include one to three surrounding lines so it is unique.
- REPLACE is the entire new text of that span, not a list of changed words. Do not escape HTML or Markdown.
- Prefer one block when the change sits in one region (a cell or a heading).
  Use two blocks only when the regions are apart: one deletes, one inserts, both copied from the original.
- To move content, delete it at its old place and insert it at its new place in the markup (two blocks). Never use CSS (order, flex, grid, position, float) to change the reading order.
- For a move, use two blocks: one whose FIND is the moved block and whose REPLACE is empty, and one whose FIND is only the first line of the destination (e.g. the heading) and whose REPLACE is the moved block followed by that line.
- The line break before each marker is not part of the text. Do not add a blank line before a marker.
- Leave every unchanged part of the file out of the reply.
- If a needed fact is missing, reply with only the NEEDS_INPUT block from the rules above, not a patch.
"""


class PatchError(ValueError):
    """The reply is not a patch that can be applied. Callers fall back to a full rewrite."""


@dataclass(frozen=True)
class Hunk:
    find: str
    replace: str


@dataclass(frozen=True)
class PatchReply:
    assumptions: list[str]
    summary: str
    hunks: list[Hunk]


_FENCE_RE = re.compile(r"^```[a-zA-Z0-9]*[ \t]*\n(.*)\n```[ \t]*\Z", re.S)
_STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.I | re.S)
_MOVE_REQUEST_RE = re.compile(r"\b(?:move|reorder|above|below|before|after)\b", re.I)
_SECTION_RE = re.compile(
    r"^===(ASSUMPTIONS|SUMMARY|PATCH|END|NEEDS_INPUT|FILE:.*?)===\s*$",
    re.M,
)
# Own-line markers. SEARCH is accepted when a model emits it; the prompt does not ask for it.
_FIND_START = frozenset({"<<<FIND>>>", "<<<<<<< SEARCH"})
_REPLACE_SWITCH = frozenset({"<<<REPLACE>>>", "======="})
_HUNK_END = frozenset({"<<<END>>>", ">>>>>>> REPLACE"})
_HUNK_MARKER_LINES = _FIND_START | _REPLACE_SWITCH | _HUNK_END
_DOC_MARKERS = ("<<<FIND>>>", "<<<REPLACE>>>", "<<<END>>>")


def _normalize(text: str) -> str:
    stripped = text.strip().replace("\r\n", "\n").replace("\r", "\n")
    fenced = _FENCE_RE.match(stripped)
    return fenced.group(1).strip() if fenced else stripped


def _section(text: str, name: str) -> str | None:
    marks = list(_SECTION_RE.finditer(text))
    for i, mark in enumerate(marks):
        if mark.group(1) != name:
            continue
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        return text[mark.end() : end].strip("\n")
    return None


def _assumptions(raw: str | None) -> list[str]:
    if not raw:
        return []
    items = [line.lstrip("-* ").strip() for line in raw.splitlines() if line.strip()]
    return [item for item in items if item.lower() not in ("none", "(none)")]


def _marker_kind(line: str, dialect: str) -> str | None:
    """Marker role for this line. The other dialect's markers stay text and fail later."""
    stripped = line.strip()
    if dialect != "search":
        if stripped == "<<<FIND>>>":
            return "find"
        if stripped == "<<<REPLACE>>>":
            return "replace"
        if stripped == "<<<END>>>":
            return "end"
    if dialect != "find":
        if stripped == "<<<<<<< SEARCH":
            return "find"
        if stripped == "=======":
            return "replace"
        if stripped == ">>>>>>> REPLACE":
            return "end"
    return None


def _dialect_of(line: str) -> str:
    if line.strip() == "<<<<<<< SEARCH":
        return "search"
    return "find"


def _join_hunk_lines(lines: list[str]) -> str:
    """Join hunk lines. The blank line before a marker is the separator, not text."""
    if lines and lines[-1] == "":
        lines = lines[:-1]
    return "\n".join(lines)


def _has_marker_line(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in _HUNK_MARKER_LINES:
            return True
        if stripped.startswith("===") and stripped.endswith("==="):
            return True
    return False


def _parse_hunks(body: str) -> list[Hunk]:
    """Read FIND/REPLACE/END blocks one line at a time.

    A new FIND while a replacement is open closes that hunk. Models often omit
    ``<<<END>>>`` between a delete block and the insert block of a move.
    """
    hunks: list[Hunk] = []
    state = "idle"
    dialect = ""
    find_lines: list[str] = []
    replace_lines: list[str] = []
    junk_before = False

    def close() -> None:
        find = _join_hunk_lines(find_lines)
        replace = _join_hunk_lines(replace_lines)
        if find == "":
            raise PatchError("empty find")
        if _has_marker_line(find) or _has_marker_line(replace):
            raise PatchError("marker in hunk")
        hunks.append(Hunk(find, replace))
        if len(hunks) > MAX_HUNKS:
            raise PatchError("too many hunks")

    for line in body.splitlines():
        kind = _marker_kind(line, dialect)
        if state == "idle":
            if kind == "find":
                if junk_before:
                    raise PatchError("malformed patch")
                state = "find"
                dialect = _dialect_of(line)
                find_lines = []
                replace_lines = []
            elif kind is None:
                if line.strip() and hunks:
                    raise PatchError("malformed patch")
                if line.strip():
                    junk_before = True
            else:
                raise PatchError("malformed patch")
        elif state == "find":
            if kind == "replace":
                state = "replace"
                replace_lines = []
            elif kind is None:
                find_lines.append(line)
            else:
                raise PatchError("malformed patch")
        elif kind == "find":
            close()
            state = "find"
            dialect = _dialect_of(line)
            find_lines = []
            replace_lines = []
        elif kind == "end":
            close()
            state = "idle"
            dialect = ""
            find_lines = []
            replace_lines = []
        elif kind == "replace":
            raise PatchError("malformed patch")
        else:
            replace_lines.append(line)
    if state != "idle":
        raise PatchError("malformed patch")
    if not hunks:
        raise PatchError("empty patch")
    return hunks


def parse_patch_reply(text: str) -> PatchReply:
    """Parse a patch reply. Raises PatchError when the reply is not a clean patch."""
    cleaned = _normalize(text)
    body = _section(cleaned, "PATCH")
    if body is None:
        raise PatchError("no patch section")
    summary = (_section(cleaned, "SUMMARY") or "").strip()
    return PatchReply(_assumptions(_section(cleaned, "ASSUMPTIONS")), summary, _parse_hunks(body))


def _line_spans(text: str) -> list[tuple[int, int, str]]:
    """(start, end_including_break, content_without_break) for each line."""
    spans: list[tuple[int, int, str]] = []
    i = 0
    n = len(text)
    while i < n:
        j = i
        while j < n and text[j] not in "\r\n":
            j += 1
        end = j
        if end < n and text[end] == "\r":
            end += 1
        if end < n and text[end] == "\n":
            end += 1
        spans.append((i, end, text[i:j]))
        if end == i:
            break
        i = end
    return spans


def _indent(line: str) -> str:
    i = 0
    while i < len(line) and line[i] in " \t":
        i += 1
    return line[:i]


def _exact_span(base: str, find: str) -> tuple[int, int] | None:
    start = base.find(find)
    if start < 0:
        return None
    if base.find(find, start + len(find)) >= 0:
        raise PatchError("find matched more than once")
    return start, start + len(find)


def _rstrip_span(base: str, find: str) -> tuple[int, int]:
    """Match a span whose lines differ only by trailing spaces or tabs."""
    find_lines = find.splitlines()
    if not find_lines:
        raise PatchError("find did not match")
    base_lines = _line_spans(base)
    width = len(find_lines)
    if width > len(base_lines):
        raise PatchError("find did not match")
    hits: list[tuple[int, int]] = []
    for i in range(len(base_lines) - width + 1):
        matched = True
        for k in range(width):
            content = base_lines[i + k][2]
            wanted = find_lines[k]
            if _indent(content) != _indent(wanted) or content.rstrip(" \t") != wanted.rstrip(" \t"):
                matched = False
                break
        if matched:
            start = base_lines[i][0]
            last_start, _last_end, last_content = base_lines[i + width - 1]
            hits.append((start, last_start + len(last_content)))
    if len(hits) > 1:
        raise PatchError("find matched more than once")
    if not hits:
        raise PatchError("find did not match")
    return hits[0]


def _locate(base: str, find: str) -> tuple[int, int]:
    exact = _exact_span(base, find)
    if exact is not None:
        return exact
    return _rstrip_span(base, find)


def css_only_move(verbatim: str, base: str, hunks: list[Hunk]) -> bool:
    """True when a move request is answered only by text inside ``<style>``."""
    if not hunks or _MOVE_REQUEST_RE.search(verbatim) is None:
        return False
    regions = [(match.start(), match.end()) for match in _STYLE_RE.finditer(base)]
    if not regions:
        return False
    for hunk in hunks:
        if "<" in hunk.replace:
            return False
        try:
            start, end = _locate(base, hunk.find)
        except PatchError:
            return False
        if not any(lo <= start and end <= hi for lo, hi in regions):
            return False
    return True


def apply_patch(base: str, hunks: list[Hunk]) -> str:
    """Apply hunks to the original base. Raises PatchError if they do not apply cleanly."""
    if not hunks:
        raise PatchError("empty patch")
    if len(hunks) > MAX_HUNKS:
        raise PatchError("too many hunks")
    spans: list[tuple[int, int, str]] = []
    for hunk in hunks:
        start, end = _locate(base, hunk.find)
        spans.append((start, end, hunk.replace))
    spans.sort(key=lambda item: (item[0], item[1]))
    for prev, nxt in zip(spans, spans[1:], strict=False):
        if nxt[0] < prev[1]:
            raise PatchError("overlapping hunks")
    updated = base
    for start, end, replace in reversed(spans):
        updated = updated[:start] + replace + updated[end:]
    for token in _DOC_MARKERS:
        if token in updated and token not in base:
            raise PatchError("marker in hunk")
    return updated


def assemble_model_reply(fmt: str, assumptions: list[str], summary: str, body: str) -> str:
    """Build the complete-file reply the existing parser already accepts."""
    filename = "index.html" if fmt == "html" else "content.md"
    assumption_lines = "\n".join(f"- {item}" for item in assumptions) if assumptions else "- none"
    return (
        "===ASSUMPTIONS===\n"
        f"{assumption_lines}\n"
        "===SUMMARY===\n"
        f"{summary.strip()}\n"
        f"===FILE: {filename}===\n"
        f"{body}\n"
        "===END===\n"
    )
