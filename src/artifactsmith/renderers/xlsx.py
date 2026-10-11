"""XLSX renderer via openpyxl (MIT). Tables become sheets; prose becomes a Notes sheet.

Numeric values are stored as numbers (currency/percent formats). Other cells are
string literals (never formulas). Public http(s) URLs get a plain hyperlink
relationship, not ``=HYPERLINK()``. Sheet names come from the heading above each table.
"""

from __future__ import annotations

import io
import re
from collections.abc import Sequence
from typing import Any

from .base import RenderOutput
from .links import iter_inline_segments, public_href_or_none
from .md_parse import parse_blocks, visible_text
from .safety import strip_ooxml_controls

_SHEET_SAFE = re.compile(r"[\[\]\*\:\/\\\?]")
_GROUPED_NUM = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")
_PLAIN_NUM = re.compile(r"\d+(?:\.\d+)?")
_CURRENCY_SYM = frozenset("$£€¥")
_EXCEL_NAME_MAX = 31
_RESERVED_SHEETS = frozenset({"history"})
_NOTES_NAME = "Notes"
_ID_WORDS = frozenset(
    {
        "zip",
        "postal",
        "postcode",
        "phone",
        "tel",
        "mobile",
        "cell",
        "fax",
        "id",
        "account",
        "acct",
        "sku",
        "upc",
        "ean",
        "isbn",
        "code",
        "ssn",
        "ein",
        "routing",
        "card",
    }
)
_COUNT_WORDS = frozenset({"number", "no", "#"})
_ID_BEFORE_COUNT = _ID_WORDS | frozenset({"invoice", "order", "po"})


def _clip_sheet_name(raw: str) -> str:
    return raw[:_EXCEL_NAME_MAX].strip().strip("'")


def _sheet_name(title: str, used: set[str], index: int) -> str:
    cleaned = strip_ooxml_controls(visible_text(title))
    base = _SHEET_SAFE.sub("", cleaned).strip().strip("'") or f"Sheet{index}"
    name = _clip_sheet_name(base)
    n = 2
    while not name or name.lower() in used or name.lower() in _RESERVED_SHEETS:
        suffix = f"_{n}"
        name = _clip_sheet_name(base[: max(0, _EXCEL_NAME_MAX - len(suffix))] + suffix)
        n += 1
    used.add(name.lower())
    return name


def _header_tokens(header: str) -> list[str]:
    text = visible_text(header).lower().replace(".", " ")
    return re.findall(r"[a-z0-9]+|#", text)


def _header_is_identifier(header: str) -> bool:
    tokens = _header_tokens(header)
    if not tokens:
        return False
    if any(tok in _ID_WORDS for tok in tokens):
        return True
    if tokens in (["number"], ["no"], ["#"]):
        return True
    return any(tok in _COUNT_WORDS and i > 0 and tokens[i - 1] in _ID_BEFORE_COUNT for i, tok in enumerate(tokens))


def _identifier_columns(headers: Sequence[str]) -> frozenset[int]:
    return frozenset(i + 1 for i, h in enumerate(headers) if _header_is_identifier(h))


def _currency_format(symbol: str, decimals: int) -> str:
    dec = f".{'0' * decimals}" if decimals else ""
    if symbol == "$":
        return f"$#,##0{dec}"
    return f'"{symbol}"#,##0{dec}'


def _parse_numeric(raw: str) -> tuple[int | float, str] | None:
    """Return (number, Excel format) for ints, decimals, currency, and percents.

    Identifier-like values (long digit strings, E.164, leading zeros) stay text.
    """
    text = raw.strip()
    if not text or text[0] in "=@":
        return None
    if any(c in text for c in _CURRENCY_SYM) and "%" in text:
        return None
    e164 = text.startswith("+") and "%" not in text and not any(c in text for c in _CURRENCY_SYM)
    if e164 and sum(ch.isdigit() for ch in text) >= 7:
        return None
    percent = text.endswith("%")
    if percent:
        text = text[:-1].strip()
    sign = 1
    currency = ""
    if text and text[0] in "+-":
        sign = -1 if text[0] == "-" else 1
        text = text[1:].strip()
    if text and text[0] in _CURRENCY_SYM:
        currency = text[0]
        text = text[1:].strip()
    if text and text[0] in "+-":
        sign = -1 if text[0] == "-" else 1
        text = text[1:].strip()
    if not text:
        return None
    if _GROUPED_NUM.fullmatch(text):
        grouped = True
        core = text.replace(",", "")
    elif _PLAIN_NUM.fullmatch(text):
        grouped = False
        core = text
    else:
        return None
    if re.match(r"^0\d", core):
        return None
    if not grouped and "." not in core and len(core) >= 10:
        return None
    if len(core.replace(".", "")) > 15:
        return None
    if "." in core:
        number: int | float = sign * float(core)
        decimals = len(core.split(".", 1)[1])
    else:
        number = sign * int(core)
        decimals = 0
    if percent:
        return float(number) / 100.0, ("0%" if decimals == 0 else f"0.{'0' * decimals}%")
    if currency:
        return number, _currency_format(currency, decimals)
    if decimals:
        return number, (f"#,##0.{'0' * decimals}" if grouped else f"0.{'0' * decimals}")
    return number, ("#,##0" if grouped else "0")


def _first_public_href(text: str) -> str | None:
    for _display, href in iter_inline_segments(text):
        if href:
            return href
    return public_href_or_none(text.strip())


def _literal_cell(ws: Any, row: int, col: int, value: object, *, force_text: bool = False) -> None:
    """Write a cell: numbers as numbers, everything else as a string (never a formula).

    The full cell text is preserved (every URL stays visible). When multiple public
    http(s) URLs appear, only the first becomes the cell hyperlink.
    """
    from openpyxl.cell.cell import TYPE_STRING

    raw = strip_ooxml_controls("" if value is None else str(value))
    href = _first_public_href(raw) if raw else None
    text = visible_text(raw, inline=True) if raw else ""
    cell = ws.cell(row=row, column=col)
    parsed = None if force_text or href else _parse_numeric(text)
    if parsed is not None:
        number, fmt = parsed
        cell.value = number
        cell.number_format = fmt
        return
    shown = text if not href or href in text else raw
    cell.value = shown
    cell.data_type = TYPE_STRING
    if href:
        cell.hyperlink = href


def _write_row(ws: Any, row: int, values: Sequence[object], *, id_cols: frozenset[int] = frozenset()) -> None:
    for col, value in enumerate(values, start=1):
        _literal_cell(ws, row, col, value, force_text=col in id_cols)


class XlsxRenderer:
    format = "xlsx"
    model_filename = "content.md"

    def render(self, *, title: str, body: str) -> RenderOutput:
        from openpyxl import Workbook

        wb = Workbook()
        default = wb.active
        if default is not None:
            wb.remove(default)
        used: set[str] = {_NOTES_NAME.lower()}
        notes: list[str] = []
        table_i = 0
        pending_sheet: str | None = None
        title = strip_ooxml_controls(title)
        if title:
            notes.append(title)
        for b in parse_blocks(body):
            if b.kind == "heading":
                pending_sheet = b.text
                notes.append(b.text)
                continue
            if b.kind == "table":
                table_i += 1
                raw_name = pending_sheet or (b.headers[0] if b.headers else f"Table{table_i}")
                pending_sheet = None
                ws = wb.create_sheet(_sheet_name(raw_name, used, table_i))
                headers = b.headers or [f"col{i + 1}" for i in range(max((len(r) for r in b.rows), default=1))]
                id_cols = _identifier_columns(headers)
                _write_row(ws, 1, list(headers))
                width = len(headers)
                for i, row in enumerate(b.rows, start=2):
                    padded = list(row) + [""] * max(0, width - len(row))
                    _write_row(ws, i, padded[:width], id_cols=id_cols)
                continue
            pending_sheet = None
            if b.kind == "paragraph":
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
            nws = wb.create_sheet(_NOTES_NAME)
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
