"""DOCX renderer via python-docx (MIT). Public http(s) become OOXML hyperlinks."""

from __future__ import annotations

import io
from typing import Any

from .base import RenderOutput
from .figures import rasterize_svg
from .md_parse import Block, InlineSpan, iter_styled_spans, parse_blocks
from .safety import strip_ooxml_controls


def _apply_run_props(rpr: Any, span: InlineSpan, *, hyperlink: bool) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    if hyperlink:
        color = OxmlElement("w:color")
        color.set(qn("w:val"), "0563C1")
        rpr.append(color)
        u = OxmlElement("w:u")
        u.set(qn("w:val"), "single")
        rpr.append(u)
    if span.bold:
        rpr.append(OxmlElement("w:b"))
    if span.italic:
        rpr.append(OxmlElement("w:i"))
    if span.code:
        fonts = OxmlElement("w:rFonts")
        fonts.set(qn("w:ascii"), "Courier New")
        fonts.set(qn("w:hAnsi"), "Courier New")
        rpr.append(fonts)


def _add_text_with_links(paragraph: Any, text: str) -> None:
    """Append runs to a paragraph; public URLs become external hyperlinks."""
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt

    for span in iter_styled_spans(text):
        display = strip_ooxml_controls(span.text)
        if not display:
            continue
        if span.href is None:
            run = paragraph.add_run(display)
            run.bold = span.bold
            run.italic = span.italic
            if span.code:
                run.font.name = "Courier New"
                run.font.size = Pt(9)
            continue
        part = paragraph.part
        r_id = part.relate_to(span.href, RT.HYPERLINK, is_external=True)
        hyperlink = OxmlElement("w:hyperlink")
        hyperlink.set(qn("r:id"), r_id)
        run_el = OxmlElement("w:r")
        rpr = OxmlElement("w:rPr")
        _apply_run_props(rpr, span, hyperlink=True)
        run_el.append(rpr)
        text_el = OxmlElement("w:t")
        text_el.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        text_el.text = display
        run_el.append(text_el)
        hyperlink.append(run_el)
        paragraph._p.append(hyperlink)


def _add_table(doc: Any, headers: list[str], rows: list[list[str]]) -> None:
    cols = max(len(headers), max((len(row) for row in rows), default=0))
    if cols == 0:
        return
    table = doc.add_table(rows=1 + len(rows), cols=cols)
    table.style = "Table Grid"
    hdr = table.rows[0].cells
    for index, header in enumerate(headers):
        if index < cols:
            hdr[index].text = ""
            _add_text_with_links(hdr[index].paragraphs[0], header)
    for row_index, row in enumerate(rows):
        cells = table.rows[row_index + 1].cells
        for col_index, val in enumerate(row):
            if col_index < cols:
                cells[col_index].text = ""
                _add_text_with_links(cells[col_index].paragraphs[0], val)


def _add_figure(doc: Any, block: Block) -> bool:
    """Embed the drawing as a PNG. Return False so the caller can write the table."""
    from docx.oxml.ns import qn
    from docx.shared import Inches

    png = rasterize_svg(block.svg)
    if not png:
        return False
    paragraph = doc.add_paragraph()
    run = paragraph.add_run()
    shape = run.add_picture(io.BytesIO(png), width=Inches(6.2))
    alt = strip_ooxml_controls(block.alt).strip() or "Chart"
    if len(alt) > 280:
        alt = alt[:277].rstrip() + "..."
    doc_pr = shape._inline.docPr
    doc_pr.set("descr", alt)
    doc_pr.set("title", alt[:80])
    drawing = run._r.find(qn("w:drawing"))
    if drawing is not None:
        for element in drawing.iter():
            if element.tag.endswith("}cNvPr"):
                element.set("descr", alt)
    return True


class DocxRenderer:
    format = "docx"
    model_filename = "content.md"

    def render(self, *, title: str, body: str) -> RenderOutput:
        from docx import Document
        from docx.shared import Pt

        doc = Document()
        style = doc.styles["Normal"]
        style.font.name = "Calibri"
        style.font.size = Pt(11)
        if title:
            doc.add_heading(strip_ooxml_controls(title), level=0)
        for b in parse_blocks(body):
            if b.kind == "heading":
                p = doc.add_heading("", level=min(max(b.level, 1), 4))
                # Clear default empty run then add linked text.
                p.clear()
                _add_text_with_links(p, b.text)
            elif b.kind == "paragraph":
                p = doc.add_paragraph()
                _add_text_with_links(p, b.text)
            elif b.kind == "list":
                for item in b.items:
                    p = doc.add_paragraph(style="List Bullet")
                    _add_text_with_links(p, item)
            elif b.kind == "code":
                p = doc.add_paragraph(strip_ooxml_controls(b.text))
                for run in p.runs:
                    run.font.name = "Courier New"
                    run.font.size = Pt(9)
            elif b.kind == "figure" and b.svg and _add_figure(doc, b):
                continue
            elif b.kind in {"table", "figure"}:
                _add_table(doc, b.headers, b.rows)
        buf = io.BytesIO()
        doc.save(buf)
        files = {
            "document.docx": buf.getvalue(),
            "content.md": (body.strip() + "\n").encode("utf-8"),
        }
        return RenderOutput(files=files, primary="document.docx")
