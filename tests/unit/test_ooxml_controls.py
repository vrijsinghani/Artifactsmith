"""C0 controls (except tab/LF/CR) must not crash DOCX/XLSX renders."""

from __future__ import annotations

import io
import zipfile

import pytest
from openpyxl import load_workbook

from artifactsmith.renderers import get_renderer
from artifactsmith.renderers.safety import strip_ooxml_controls


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("hello\x0bworld", "helloworld"),  # VT
        ("hello\x0cworld", "helloworld"),  # FF
        ("a\x00b\x01c", "abc"),
        ("keep\t\n\rthese", "keep\t\n\rthese"),
    ],
)
def test_strip_ooxml_controls(raw, expected):
    assert strip_ooxml_controls(raw) == expected


def test_docx_strips_vt_and_builds():
    body = "Line with vertical tab\x0bhere and form feed\x0cdone."
    out = get_renderer("docx").render(title="T\x0bitle", body=body)
    data = out.files["document.docx"]
    assert data[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
    assert "\x0b" not in xml
    assert "\x0c" not in xml
    assert "vertical tab" in xml
    assert "form feed" in xml or "done" in xml


def test_xlsx_strips_ff_and_builds():
    body = "Cell with form feed\x0cinside."
    out = get_renderer("xlsx").render(title="Sheet", body=body)
    wb = load_workbook(io.BytesIO(out.files["document.xlsx"]))
    texts = [str(c.value) for ws in wb.worksheets for row in ws.iter_rows() for c in row if c.value]
    joined = "\n".join(texts)
    assert "\x0c" not in joined
    assert "form feed" in joined
    assert "inside" in joined
