"""Deterministic renderers: each format produces expected bytes and no remote URLs."""

from __future__ import annotations

import io
import zipfile

from artifactsmith.renderers import SUPPORTED_FORMATS, get_renderer
from artifactsmith.renderers.safety import URL_RE

MD = """# Pilot store

The pilot store code is HARBOR-17.

## Numbers

| store | code |
| --- | --- |
| pilot | HARBOR-17 |
| next | HARBOR-24 |

- keep the fact
"""


def test_supported_formats():
    assert SUPPORTED_FORMATS == frozenset({"html", "markdown", "pdf", "docx", "xlsx"})


def test_html_passthrough():
    body = "<html><body><p>HARBOR-17</p></body></html>"
    out = get_renderer("html").render(title="t", body=body)
    assert out.primary == "index.html"
    assert b"HARBOR-17" in out.files["index.html"]
    assert not URL_RE.search(out.files["index.html"].decode())


def test_markdown_titles_when_needed():
    out = get_renderer("markdown").render(title="Pilot", body="The code is HARBOR-17.")
    text = out.files["document.md"].decode()
    assert text.startswith("# Pilot")
    assert "HARBOR-17" in text
    assert out.primary == "document.md"


def test_pdf_magic_and_source():
    out = get_renderer("pdf").render(title="Pilot", body=MD)
    assert out.files["document.pdf"].startswith(b"%PDF")
    assert b"HARBOR-17" in out.files["content.md"]
    assert out.primary == "document.pdf"


def test_docx_is_ooxml_zip():
    out = get_renderer("docx").render(title="Pilot", body=MD)
    data = out.files["document.docx"]
    assert data[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
    assert any(n.startswith("word/") for n in names)
    assert b"HARBOR-17" in out.files["content.md"]


def test_xlsx_has_workbook():
    out = get_renderer("xlsx").render(title="Pilot", body=MD)
    data = out.files["document.xlsx"]
    assert data[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert "[Content_Types].xml" in zf.namelist()
    assert out.primary == "document.xlsx"


def test_xlsx_without_table_still_writes_sheet():
    out = get_renderer("xlsx").render(title="Notes only", body="Just a sentence with HARBOR-17.")
    assert out.files["document.xlsx"][:2] == b"PK"
