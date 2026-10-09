"""DOCX renderer via python-docx (MIT). Public http(s) become OOXML hyperlinks."""

from __future__ import annotations

import io
from typing import Any

from .base import RenderOutput
from .links import iter_inline_segments
from .md_parse import parse_blocks
from .safety import strip_ooxml_controls


def _add_text_with_links(paragraph: Any, text: str) -> None:
    """Append runs to a paragraph; public URLs become external hyperlinks."""
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    for display, href in iter_inline_segments(text):
        display = strip_ooxml_controls(display)
        if not display:
            continue
        if href is None:
            paragraph.add_run(display)
            continue
        part = paragraph.part
        r_id = part.relate_to(href, RT.HYPERLINK, is_external=True)
        hyperlink = OxmlElement("w:hyperlink")
        hyperlink.set(qn("r:id"), r_id)
        run = OxmlElement("w:r")
        rpr = OxmlElement("w:rPr")
        color = OxmlElement("w:color")
        color.set(qn("w:val"), "0563C1")
        rpr.append(color)
        u = OxmlElement("w:u")
        u.set(qn("w:val"), "single")
        rpr.append(u)
        run.append(rpr)
        text_el = OxmlElement("w:t")
        text_el.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        text_el.text = display
        run.append(text_el)
        hyperlink.append(run)
        paragraph._p.append(hyperlink)


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
            elif b.kind == "table":
                cols = max(len(b.headers), max((len(r) for r in b.rows), default=0))
                if cols == 0:
                    continue
                table = doc.add_table(rows=1 + len(b.rows), cols=cols)
                table.style = "Table Grid"
                hdr = table.rows[0].cells
                for i, h in enumerate(b.headers):
                    if i < cols:
                        hdr[i].text = ""
                        _add_text_with_links(hdr[i].paragraphs[0], h)
                for ri, row in enumerate(b.rows):
                    cells = table.rows[ri + 1].cells
                    for ci, val in enumerate(row):
                        if ci < cols:
                            cells[ci].text = ""
                            _add_text_with_links(cells[ci].paragraphs[0], val)
        buf = io.BytesIO()
        doc.save(buf)
        files = {
            "document.docx": buf.getvalue(),
            "content.md": (body.strip() + "\n").encode("utf-8"),
        }
        return RenderOutput(files=files, primary="document.docx")
