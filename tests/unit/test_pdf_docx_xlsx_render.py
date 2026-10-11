"""PDF/DOCX/XLSX render bugs: emphasis, code wrap, numeric cells, sheet names."""

from __future__ import annotations

import io
import zipfile

from artifactsmith.renderers import get_renderer
from artifactsmith.renderers.md_parse import blocks_to_simple_html, parse_blocks
from artifactsmith.renderers.pdf import DenyAllURLFetcher

LONG_CMD = (
    "docker compose exec -T server artifactsmith token add "
    "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ "
    "--workspace alpha"
)
UNBROKEN = "X" * 240


def _iter_boxes(box: object) -> list[object]:
    out = [box]
    for child in getattr(box, "children", None) or ():
        out.extend(_iter_boxes(child))
    return out


def _layout(html: str) -> object:
    from weasyprint import HTML

    return HTML(string=html, base_url=".", url_fetcher=DenyAllURLFetcher()).render()


def _text_boxes(document: object) -> list[object]:
    boxes: list[object] = []
    for page in document.pages:  # type: ignore[attr-defined]
        page_box = getattr(page, "_page_box", None)
        if page_box is None:
            continue
        boxes.extend(b for b in _iter_boxes(page_box) if getattr(b, "text", None))
    return boxes


def test_pdf_and_docx_render_inline_emphasis():
    body = "Use **bold**, *italic*, and `inline code` in the brief."
    html = blocks_to_simple_html("T", parse_blocks(body))
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert "<code>inline code</code>" in html
    assert "**bold**" not in html

    document = _layout(html)
    texts = [str(getattr(box, "text", "")) for box in _text_boxes(document)]
    joined = "".join(texts)
    assert "bold" in joined
    assert "italic" in joined
    assert "inline code" in joined
    assert "**" not in joined
    by_text = {str(getattr(box, "text", "")): box for box in _text_boxes(document)}
    assert int(by_text["bold"].style["font_weight"]) >= 700
    assert str(by_text["italic"].style["font_style"]) == "italic"
    assert "monospace" in " ".join(str(f) for f in by_text["inline code"].style["font_family"])

    pdf = get_renderer("pdf").render(title="T", body=body).files["document.pdf"]
    assert pdf.startswith(b"%PDF")
    assert b"**bold**" not in pdf

    from docx import Document

    data = get_renderer("docx").render(title="T", body=body).files["document.docx"]
    doc = Document(io.BytesIO(data))
    runs = [run for p in doc.paragraphs for run in p.runs]
    joined_docx = "".join(run.text for run in runs)
    assert "bold" in joined_docx
    assert "italic" in joined_docx
    assert "inline code" in joined_docx
    assert "**" not in joined_docx
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
    assert "word-break:break-all" in html

    document = _layout(html)
    texts = [str(getattr(box, "text", "")) for box in _text_boxes(document)]
    joined = "".join(texts)
    assert "docker compose exec" in joined
    assert "--workspace alpha" in joined
    assert UNBROKEN in joined.replace("\n", "")

    code_lines = [t for t in texts if "docker" in t or "workspace" in t or set(t) == {"X"}]
    assert len(code_lines) >= 2

    for page in document.pages:  # type: ignore[attr-defined]
        page_box = getattr(page, "_page_box", None)
        if page_box is None:
            continue
        page_width = float(page.width)
        for box in _iter_boxes(page_box):
            x = float(getattr(box, "position_x", 0) or 0)
            w = float(getattr(box, "width", 0) or 0)
            assert x + w <= page_width + 1.5, (x, w, page_width)

    pdf = get_renderer("pdf").render(title="T", body=body).files["document.pdf"]
    assert pdf.startswith(b"%PDF")


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
