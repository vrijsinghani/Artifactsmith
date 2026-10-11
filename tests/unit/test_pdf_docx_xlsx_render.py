"""PDF/DOCX/XLSX render bugs: emphasis, code wrap, numeric cells, sheet names."""

from __future__ import annotations

import io
import re
import zipfile
import zlib

from artifactsmith.renderers import get_renderer
from artifactsmith.renderers.md_parse import blocks_to_simple_html, parse_blocks
from artifactsmith.renderers.pdf import DenyAllURLFetcher

LONG_CMD = (
    "docker compose exec -T server artifactsmith token add "
    "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ "
    "--workspace alpha"
)
UNBROKEN = "X" * 240


def _pdf_text(pdf: bytes) -> str:
    chunks: list[str] = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", pdf, re.S):
        data = m.group(1)
        try:
            data = zlib.decompress(data)
        except zlib.error:
            pass
        for s in re.findall(rb"\((?:\\.|[^\\)])*\)", data):
            chunks.append(s[1:-1].decode("latin-1", errors="replace"))
        for hx in re.findall(rb"<([0-9A-Fa-f\s]+)>", data):
            hex_bytes = re.sub(rb"\s+", b"", hx)
            if len(hex_bytes) % 2:
                hex_bytes += b"0"
            try:
                raw = bytes.fromhex(hex_bytes.decode("ascii"))
            except ValueError:
                continue
            if raw.startswith(b"\xfe\xff"):
                chunks.append(raw[2:].decode("utf-16-be", errors="replace"))
            elif raw.startswith(b"\xff\xfe"):
                chunks.append(raw[2:].decode("utf-16-le", errors="replace"))
            else:
                chunks.append(raw.decode("latin-1", errors="replace"))
    return "".join(chunks)


def _iter_boxes(box: object) -> list[object]:
    out = [box]
    for child in getattr(box, "children", None) or ():
        out.extend(_iter_boxes(child))
    return out


def test_pdf_and_docx_render_inline_emphasis():
    body = "Use **bold**, *italic*, and `inline code` in the brief."
    html = blocks_to_simple_html("T", parse_blocks(body))
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert "<code>inline code</code>" in html
    assert "**bold**" not in html

    pdf = get_renderer("pdf").render(title="T", body=body).files["document.pdf"]
    extracted = _pdf_text(pdf)
    assert "bold" in extracted
    assert "italic" in extracted
    assert "inline code" in extracted
    assert "**bold**" not in extracted
    assert "*italic*" not in extracted

    from docx import Document

    data = get_renderer("docx").render(title="T", body=body).files["document.docx"]
    doc = Document(io.BytesIO(data))
    runs = [run for p in doc.paragraphs for run in p.runs]
    joined = "".join(run.text for run in runs)
    assert "bold" in joined
    assert "italic" in joined
    assert "inline code" in joined
    assert "**" not in joined
    assert any(run.bold and "bold" in run.text for run in runs)
    assert any(run.italic and "italic" in run.text for run in runs)
    assert any(run.font.name == "Courier New" and "inline code" in run.text for run in runs)

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
    assert "<w:b" in xml
    assert "<w:i" in xml


def test_pdf_wraps_long_code_line():
    body = f"```\n{LONG_CMD}\n{UNBROKEN}\n```"
    html = blocks_to_simple_html("T", parse_blocks(body))
    assert "white-space:pre-wrap" in html
    assert "overflow-wrap:anywhere" in html

    from weasyprint import HTML

    document = HTML(string=html, base_url=".", url_fetcher=DenyAllURLFetcher()).render()
    for page in document.pages:
        page_box = getattr(page, "_page_box", None)
        if page_box is None:
            continue
        page_width = float(page.width)
        for box in _iter_boxes(page_box):
            x = float(getattr(box, "position_x", 0) or 0)
            w = float(getattr(box, "width", 0) or 0)
            assert x + w <= page_width + 1.5, (x, w, page_width)

    pdf = get_renderer("pdf").render(title="T", body=body).files["document.pdf"]
    extracted = _pdf_text(pdf)
    assert "docker compose exec" in extracted
    assert "--workspace alpha" in extracted
    assert UNBROKEN in extracted or UNBROKEN[:80] in extracted


def test_xlsx_numeric_cell_types_and_formats():
    from openpyxl import load_workbook

    body = """## Metrics

| label | value |
| --- | --- |
| count | 1234 |
| grouped | 1,234 |
| decimal | 12.50 |
| money | $1,234.56 |
| pounds | £100 |
| pct | 12% |
| pct_dec | 12.5% |
| id | 007 |
| code | HARBOR-17 |
| formula | =1+2 |
"""
    out = get_renderer("xlsx").render(title="Metrics", body=body)
    wb = load_workbook(io.BytesIO(out.files["document.xlsx"]))
    ws = next(s for s in wb.worksheets if s.title != "Notes")
    by_label = {str(row[0].value): row[1] for row in ws.iter_rows(min_row=2) if row[0].value is not None}

    count = by_label["count"]
    assert count.value == 1234
    assert count.data_type == "n"

    grouped = by_label["grouped"]
    assert grouped.value == 1234
    assert grouped.data_type == "n"
    assert "," in grouped.number_format

    decimal = by_label["decimal"]
    assert float(decimal.value) == 12.5
    assert decimal.data_type == "n"
    assert "0.00" in decimal.number_format

    money = by_label["money"]
    assert float(money.value) == 1234.56
    assert money.data_type == "n"
    assert "$" in money.number_format

    pounds = by_label["pounds"]
    assert pounds.value == 100
    assert pounds.data_type == "n"
    assert "£" in pounds.number_format

    pct = by_label["pct"]
    assert float(pct.value) == 0.12
    assert pct.data_type == "n"
    assert "%" in pct.number_format

    pct_dec = by_label["pct_dec"]
    assert abs(float(pct_dec.value) - 0.125) < 1e-9
    assert "%" in pct_dec.number_format

    ident = by_label["id"]
    assert ident.value == "007"
    assert ident.data_type == "s"

    code = by_label["code"]
    assert code.value == "HARBOR-17"
    assert code.data_type == "s"

    formula = by_label["formula"]
    assert formula.value == "=1+2"
    assert formula.data_type == "s"


def test_xlsx_uses_model_sheet_names():
    from openpyxl import load_workbook

    long_name = "Quarterly revenue forecast extra words beyond limit"
    body = f"""## Q1 Revenue

| month | amount |
| --- | --- |
| Jan | 100 |

## Q1 Revenue

| month | amount |
| --- | --- |
| Feb | 200 |

## Foo/Bar:*Baz?

| a | b |
| --- | --- |
| 1 | 2 |

## {long_name}

| a | b |
| --- | --- |
| 3 | 4 |
"""
    out = get_renderer("xlsx").render(title="Book", body=body)
    wb = load_workbook(io.BytesIO(out.files["document.xlsx"]))
    names = [n for n in wb.sheetnames if n != "Notes"]
    assert names[0] == "Q1 Revenue"
    assert names[1] == "Q1 Revenue_2"
    assert names[2] == "FooBarBaz"
    assert names[3] == long_name[:31]
    assert all(len(n) <= 31 for n in wb.sheetnames)
    assert "month" not in wb.sheetnames
    assert "Foo/Bar:*Baz?" not in wb.sheetnames
