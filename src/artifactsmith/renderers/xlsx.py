"""XLSX renderer via openpyxl (MIT). Tables become sheets; prose becomes a Notes sheet.

Cells are always string literals (never formulas). Public http(s) URLs get a plain
hyperlink relationship, not ``=HYPERLINK()``.
"""

from __future__ import annotations

import io
import re
from collections.abc import Sequence
from typing import Any

from .base import RenderOutput
from .links import iter_inline_segments, public_href_or_none
from .md_parse import parse_blocks
from .safety import strip_ooxml_controls

_SHEET_SAFE = re.compile(r"[\[\]\*\:\/\\\?]")


def _sheet_name(title: str, used: set[str], index: int) -> str:
    cleaned = strip_ooxml_controls(title)
    base = _SHEET_SAFE.sub("", cleaned).strip() or f"Sheet{index}"
    base = base[:28]
    name = base
    n = 2
    while name.lower() in used:
        suffix = f"_{n}"
        name = base[: 31 - len(suffix)] + suffix
        n += 1
    used.add(name.lower())
    return name


def _first_public_href(text: str) -> str | None:
    for _display, href in iter_inline_segments(text):
        if href:
            return href
    return public_href_or_none(text.strip())


def _literal_cell(ws: Any, row: int, col: int, value: object) -> None:
    """Write untrusted content as a string cell so leading '=' cannot become a formula.

    The full cell text is preserved (every URL stays visible). When multiple public
    http(s) URLs appear, only the first becomes the cell hyperlink.
    """
    from openpyxl.cell.cell import TYPE_STRING

    text = strip_ooxml_controls("" if value is None else str(value))
    cell = ws.cell(row=row, column=col, value=text)
    cell.data_type = TYPE_STRING
    href = _first_public_href(text) if text else None
    if href:
        # Plain hyperlink relationship — never a formula.
        cell.hyperlink = href


def _write_row(ws: Any, row: int, values: Sequence[object]) -> None:
    for col, value in enumerate(values, start=1):
        _literal_cell(ws, row, col, value)


class XlsxRenderer:
    format = "xlsx"
    model_filename = "content.md"

    def render(self, *, title: str, body: str) -> RenderOutput:
        from openpyxl import Workbook

        wb = Workbook()
        # Remove the default sheet; we recreate intentionally.
        default = wb.active
        if default is not None:
            wb.remove(default)
        used: set[str] = set()
        notes: list[str] = []
        table_i = 0
        title = strip_ooxml_controls(title)
        if title:
            notes.append(title)
        for b in parse_blocks(body):
            if b.kind == "table":
                table_i += 1
                name = _sheet_name(b.headers[0] if b.headers else f"Table{table_i}", used, table_i)
                ws = wb.create_sheet(name)
                headers = b.headers or [f"col{i + 1}" for i in range(max((len(r) for r in b.rows), default=1))]
                _write_row(ws, 1, list(headers))
                width = len(headers)
                for i, row in enumerate(b.rows, start=2):
                    padded = list(row) + [""] * max(0, width - len(row))
                    _write_row(ws, i, padded[:width])
            elif b.kind == "heading":
                notes.append(b.text)
            elif b.kind == "paragraph":
                notes.append(b.text)
            elif b.kind == "list":
                notes.extend(f"- {it}" for it in b.items)
            elif b.kind == "code":
                notes.append(b.text)
        if not wb.sheetnames:
            ws = wb.create_sheet(_sheet_name(title or "Data", used, 1))
            _write_row(ws, 1, ["content"])
            _write_row(ws, 2, [body.strip() or "(empty)"])
        if notes:
            nws = wb.create_sheet(_sheet_name("Notes", used, 99))
            _write_row(nws, 1, ["notes"])
            for i, line in enumerate(notes, start=2):
                _write_row(nws, i, [line])
        buf = io.BytesIO()
        wb.save(buf)
        files = {
            "document.xlsx": buf.getvalue(),
            "content.md": (body.strip() + "\n").encode("utf-8"),
        }
        return RenderOutput(files=files, primary="document.xlsx")
