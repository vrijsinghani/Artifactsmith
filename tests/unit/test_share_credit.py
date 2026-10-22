"""Share-view credit line: present on HTML shares, absent when off, absent from exports."""

from __future__ import annotations

import hashlib
import json
import time
from urllib.parse import urlparse

import pytest
from starlette.testclient import TestClient
from tests.unit.test_server import FakeStore

import artifactsmith.server as server
from artifactsmith.config import CFG
from artifactsmith.service import Service
from artifactsmith.share_credit import CREDIT_TEXT, REPO, stamp_share_credit
from artifactsmith.styles import STYLES

_CREDIT = "Made with ArtifactSmith"
_PAGE = (
    b"<html><head><title>Harbor</title></head><body>"
    b"<p>HARBOR-17</p>"
    b'<p><a href="https://example.com/docs">company calendar</a> '
    b'<a href="https://example.com/other" target="_blank" rel="noopener">other note</a></p>'
    b"<table><tr><th>Name</th><td>AMAT</td></tr></table>"
    b'<p><img alt="mark" src="data:image/png;base64,'
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    b'"></p>'
    b"</body></html>"
)


@pytest.fixture
def svc(monkeypatch, tmp_path):
    monkeypatch.setattr("artifactsmith.service.Store", FakeStore)
    monkeypatch.setattr("artifactsmith.server.CFG.data_dir", tmp_path)
    monkeypatch.setattr("artifactsmith.server.CFG.secrets_dir", tmp_path / "secrets")
    (tmp_path / "secrets").mkdir(parents=True, exist_ok=True)
    service = Service()
    server.SVC = service
    yield service
    server.SVC = None


def _seed(
    svc: Service,
    *,
    aid: str,
    body: bytes,
    filename: str = "index.html",
    content_type: str = "text/html",
    fmt: str = "html",
    style: str = "house",
) -> str:
    svc.db.exec(
        "INSERT INTO artifacts (id,workspace,slug,display_name,kind,format,created_by,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        aid,
        "alpha",
        aid,
        "Harbor",
        "web_static",
        fmt,
        "agent",
        time.time(),
        time.time(),
    )
    svc.db.exec(
        "INSERT INTO versions (artifact_id,version,base_version,verbatim_request,status,job_id,model,"
        "sha256,primary_file,files_json,assumptions_json,summary,size,source_key,style,format,created_by,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        aid,
        1,
        None,
        "show",
        "done",
        "job_" + aid,
        "m",
        hashlib.sha256(body).hexdigest(),
        filename,
        json.dumps([{"name": filename, "content_type": content_type}]),
        "[]",
        "ok",
        len(body),
        None,
        style,
        fmt,
        "agent",
        time.time(),
    )
    svc.store.put(FakeStore.prefix("alpha", aid, 1) + filename, body, content_type)
    sid = "s" + aid[-14:]
    svc.db.exec(
        "INSERT INTO shares (id, artifact_id, version, sha256, created_by, created_at, expires_at, revoked_at, pinned) "
        "VALUES (?,?,?,?,?,?,?,NULL,0)",
        sid,
        aid,
        1,
        hashlib.sha256(body).hexdigest(),
        "agent",
        time.time(),
        time.time() + 86400,
    )
    return sid


def _footer(html: str) -> str:
    return html.split('id="am-share-credit"', 1)[1].split("</footer>", 1)[0]


def _assert_credit(html: str, marker: str) -> None:
    assert html.index(marker) < html.index(_CREDIT)
    footer = _footer(html)
    assert f'href="{REPO}"' in footer
    assert 'target="_blank"' in footer
    assert 'rel="noopener"' in footer
    assert _CREDIT in footer
    assert html.count('id="am-share-credit"') == 1


def test_stamp_is_unchanged_when_credit_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CFG, "share_credit", False)
    raw = b"<html><body>x</body></html>"
    assert stamp_share_credit(raw) is raw


def test_stamp_appends_when_the_page_has_no_body() -> None:
    out = stamp_share_credit(b"<p>HARBOR-17</p>")
    assert out.decode().index("HARBOR-17") < out.decode().index(CREDIT_TEXT)


def test_shared_html_contains_the_credit_link(svc: Service) -> None:
    sid = _seed(svc, aid="art_credit_html", body=_PAGE)
    client = TestClient(server.preview_app)
    page = client.get(f"/s/{sid}/")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    _assert_credit(page.text, "HARBOR-17")
    assert 'href="https://example.com/docs"' in page.text
    assert "<table>" in page.text
    assert 'alt="mark"' in page.text
    stored, _ctype = svc.store.get(FakeStore.prefix("alpha", "art_credit_html", 1) + "index.html")
    assert stored == _PAGE
    assert b"am-share-credit" not in stored


def test_shared_html_omits_the_credit_when_disabled(svc: Service, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CFG, "share_credit", False)
    sid = _seed(svc, aid="art_credit_off", body=_PAGE)
    page = TestClient(server.preview_app).get(f"/s/{sid}/")
    assert page.status_code == 200
    assert "HARBOR-17" in page.text
    assert "am-share-credit" not in page.text
    assert _CREDIT not in page.text
    assert REPO not in page.text


def test_export_preview_raw_and_pdf_omit_the_credit(svc: Service) -> None:
    sid = _seed(svc, aid="art_credit_export", body=_PAGE)
    client = TestClient(server.preview_app)
    raw = client.get(f"/s/{sid}/?raw=1")
    assert raw.status_code == 200
    assert raw.content == _PAGE
    principal = {"id": "tok_1", "name": "agent", "workspace": "alpha", "perms": {"export", "read"}}
    exported = svc.export(principal, "art_credit_export")
    downloaded = client.get(urlparse(str(exported["download_url"])).path)
    assert downloaded.status_code == 200
    assert downloaded.content == _PAGE
    assert b"am-share-credit" not in downloaded.content
    svc.db.exec(
        "INSERT INTO short_links VALUES (?,?,?,?,?)",
        "prevcredit0001",
        "art_credit_export",
        1,
        "preview",
        time.time() + 3600,
    )
    preview = client.get("/p/prevcredit0001")
    assert preview.status_code == 200
    assert "HARBOR-17" in preview.text
    assert "am-share-credit" not in preview.text
    pdf = b"%PDF-1.4 fake"
    pdf_sid = _seed(
        svc,
        aid="art_credit_pdf",
        body=pdf,
        filename="document.pdf",
        content_type="application/pdf",
        fmt="pdf",
    )
    served = client.get(f"/s/{pdf_sid}/")
    assert served.status_code == 200
    assert served.content == pdf


@pytest.mark.parametrize("style", STYLES)
def test_markdown_share_contains_the_credit_for_every_style(svc: Service, style: str) -> None:
    body = b"# Pilot\n\nSee [docs](https://example.com/docs).\n\nHARBOR-17\n"
    sid = _seed(
        svc,
        aid=f"art_md_{style}",
        body=body,
        filename="document.md",
        content_type="text/markdown",
        fmt="markdown",
        style=style,
    )
    page = TestClient(server.preview_app).get(f"/s/{sid}/")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    _assert_credit(page.text, "HARBOR-17")
    assert "example.com/docs" in page.text
    raw = TestClient(server.preview_app).get(f"/s/{sid}/?raw=1")
    assert raw.content == body
    assert b"am-share-credit" not in raw.content
