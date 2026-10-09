"""XLSX renderer via openpyxl (MIT). Tables become sheets; prose becomes a Notes sheet."""

from __future__ import annotations

import io
import re

from .base import RenderOutput
from .md_parse import parse_blocks

_SHEET_SAFE = re.compile(r"[\[\]\*\:\/\\\?]")


def _sheet_name(title: str, used: set[str], index: int) -> str:
    base = _SHEET_SAFE.sub("", title).strip() or f"Sheet{index}"
    base = base[:28]
    name = base
    n = 2
    while name.lower() in used:
        suffix = f"_{n}"
        name = base[: 31 - len(suffix)] + suffix
        n += 1
    used.add(name.lower())
    return name


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
        if title:
            notes.append(title)
        for b in parse_blocks(body):
            if b.kind == "table":
                table_i += 1
                name = _sheet_name(b.headers[0] if b.headers else f"Table{table_i}", used, table_i)
                ws = wb.create_sheet(name)
                headers = b.headers or [f"col{i + 1}" for i in range(max((len(r) for r in b.rows), default=1))]
                ws.append(headers)
                width = len(headers)
                for row in b.rows:
                    padded = list(row) + [""] * max(0, width - len(row))
                    ws.append(padded[:width])
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
            ws.append(["content"])
            ws.append([body.strip() or "(empty)"])
        if notes:
            nws = wb.create_sheet(_sheet_name("Notes", used, 99))
            nws.append(["notes"])
            for line in notes:
                nws.append([line])
        buf = io.BytesIO()
        wb.save(buf)
        files = {
            "document.xlsx": buf.getvalue(),
            "content.md": (body.strip() + "\n").encode("utf-8"),
        }
        return RenderOutput(files=files, primary="document.xlsx")
