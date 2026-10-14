"""HTTP coverage for BearerAuth and the cookieless preview origin."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time

import pytest
from starlette.testclient import TestClient

import artifactsmith.server as server
from artifactsmith.service import Service, sign


class FakeStore:
    bucket = "artifacts"

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    @staticmethod
    def prefix(workspace: str, artifact_id: str, version: int | None = None) -> str:
        from artifactsmith.store import Store

        return Store.prefix(workspace, artifact_id, version)

    def put(self, key: str, data: bytes, content_type: str) -> str:
        self.objects[key] = (data, content_type)
        return "vid"

    def get(self, key: str) -> tuple[bytes, str]:
        return self.objects[key]

    def ensure_bucket(self) -> bool:
        return False

    def purge_prefix(self, prefix: str) -> int:
        keys = [k for k in list(self.objects) if k.startswith(prefix)]
        for k in keys:
            del self.objects[k]
        return len(keys)


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


def _add_token(svc: Service, raw: str, *, name: str = "agent", workspace: str = "alpha", revoked: bool = False) -> None:
    svc.db.exec(
        "INSERT INTO tokens (id,token_hash,name,workspace,perms,created_at,revoked_at) VALUES (?,?,?,?,?,?,?)",
        "tok_" + name,
        hashlib.sha256(raw.encode()).hexdigest(),
        name,
        workspace,
        json.dumps(["create", "read", "edit", "export", "share", "delete"]),
        time.time(),
        time.time() if revoked else None,
    )


def _seed_done_artifact(svc: Service) -> tuple[str, int, str]:
    aid, ver = "art_preview", 1
    page = b"<html><body>HARBOR-17</body></html>"
    svc.db.exec(
        "INSERT INTO artifacts (id,workspace,slug,display_name,kind,format,created_by,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        aid,
        "alpha",
        "preview-page",
        "Preview",
        "web_static",
        "html",
        "agent",
        time.time(),
        time.time(),
    )
    svc.db.exec(
        "INSERT INTO versions (artifact_id,version,base_version,verbatim_request,status,job_id,model,"
        "sha256,primary_file,files_json,assumptions_json,summary,size,source_key,created_by,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        aid,
        ver,
        None,
        "show",
        "done",
        "job_1",
        "m",
        hashlib.sha256(page).hexdigest(),
        "index.html",
        json.dumps([{"name": "index.html", "content_type": "text/html"}]),
        "[]",
        "ok",
        len(page),
        None,
        "agent",
        time.time(),
    )
    key = FakeStore.prefix("alpha", aid, ver) + "index.html"
    svc.store.put(key, page, "text/html")
    return aid, ver, page.decode()


def test_api_healthz_without_token(svc):
    client = TestClient(server.BearerAuth(server.mcp.streamable_http_app()))
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer wrong-token"},
    ],
)
def test_api_rejects_missing_bad_token(svc, headers):
    _add_token(svc, "asmb_goodtokenvalue")
    client = TestClient(server.BearerAuth(server.mcp.streamable_http_app()))
    r = client.post("/mcp", headers={**headers, "Accept": "application/json, text/event-stream"})
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"


def test_api_rejects_revoked_token(svc):
    raw = "asmb_revokedtokenvalue"
    _add_token(svc, raw, name="revoked", revoked=True)
    client = TestClient(server.BearerAuth(server.mcp.streamable_http_app()))
    r = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw}", "Accept": "application/json, text/event-stream"},
    )
    assert r.status_code == 401


def test_preview_forged_or_expired_is_404(svc):
    client = TestClient(server.preview_app)
    r = client.get("/p/not-a-real-short-id")
    assert r.status_code == 404
    assert "set-cookie" not in {k.lower() for k in r.headers.keys()}

    aid, ver, _ = _seed_done_artifact(svc)
    sid = "expiredpreview1"
    svc.db.exec(
        "INSERT INTO short_links VALUES (?,?,?,?,?)",
        sid,
        aid,
        ver,
        "preview",
        time.time() - 10,
    )
    assert client.get(f"/p/{sid}").status_code == 404


def _assert_page_csp(header: str) -> None:
    """Served pages keep script-src none and exactly these sandbox flags."""
    parts = [part.strip() for part in header.split(";") if part.strip()]
    assert "script-src 'none'" in header
    assert parts[-1].split() == [
        "sandbox",
        "allow-popups",
        "allow-popups-to-escape-sandbox",
        "allow-top-navigation-by-user-activation",
    ]
    assert parts[:-1] == [
        "default-src 'none'",
        "img-src data:",
        "media-src data:",
        "style-src 'unsafe-inline'",
        "script-src 'none'",
        "font-src data:",
        "connect-src 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
    ]


def test_preview_ok_has_csp_and_no_cookies(svc):
    aid, ver, body = _seed_done_artifact(svc)
    sid = "previewtok1"
    svc.db.exec(
        "INSERT INTO short_links VALUES (?,?,?,?,?)",
        sid,
        aid,
        ver,
        "preview",
        time.time() + 3600,
    )
    client = TestClient(server.preview_app)
    r = client.get(f"/p/{sid}")
    assert r.status_code == 200
    assert body in r.text
    _assert_page_csp(r.headers.get("content-security-policy", ""))
    assert "set-cookie" not in {k.lower() for k in r.headers.keys()}
    assert not r.cookies


def test_download_forged_signature_404(svc):
    client = TestClient(server.preview_app)
    r = client.get("/dl/forged.signature")
    assert r.status_code == 404


def test_download_expired_signature_404(svc):
    aid, ver, _ = _seed_done_artifact(svc)
    tok = sign({"a": aid, "v": ver, "f": "index.html", "n": "x.html"}, ttl_s=-5)
    client = TestClient(server.preview_app)
    r = client.get(f"/dl/{tok}")
    assert r.status_code == 404


def test_preview_healthz(svc):
    client = TestClient(server.preview_app)
    assert client.get("/healthz").status_code == 200


def test_api_accepts_valid_token_past_auth(svc):
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def echo(request):
        return JSONResponse({"principal": request.scope.get("am_principal", {}).get("name")})

    raw = "asmb_goodtokenvalue"
    _add_token(svc, raw)
    client = TestClient(server.BearerAuth(Starlette(routes=[Route("/echo", echo)])))
    r = client.get("/echo", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 200
    assert r.json()["principal"] == "agent"


def test_download_signed_ok(svc):
    aid, ver, body = _seed_done_artifact(svc)
    tok = sign({"a": aid, "v": ver, "f": "index.html", "n": "page.html"}, ttl_s=60)
    client = TestClient(server.preview_app)
    r = client.get(f"/dl/{tok}")
    assert r.status_code == 200
    assert body.encode() in r.content
    assert "attachment" in r.headers.get("content-disposition", "")
    _assert_page_csp(r.headers.get("content-security-policy", ""))
    assert not r.cookies


def _share_principal() -> dict[str, object]:
    return {"id": "tok_1", "name": "agent", "workspace": "alpha", "perms": {"share", "read"}}


def _add_done_version(svc: Service, aid: str, version: int, page: bytes) -> None:
    svc.db.exec(
        "INSERT INTO versions (artifact_id,version,base_version,verbatim_request,status,job_id,model,"
        "sha256,primary_file,files_json,assumptions_json,summary,size,source_key,created_by,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        aid,
        version,
        version - 1,
        "show",
        "done",
        f"job_{version}",
        "m",
        hashlib.sha256(page).hexdigest(),
        "index.html",
        json.dumps([{"name": "index.html", "content_type": "text/html"}]),
        "[]",
        "ok",
        len(page),
        None,
        "agent",
        time.time(),
    )
    svc.store.put(FakeStore.prefix("alpha", aid, version) + "index.html", page, "text/html")


def test_floating_share_serves_the_latest_done_version(svc: Service):
    aid, _ver, _body = _seed_done_artifact(svc)
    principal = _share_principal()
    first = svc.share(principal, aid)
    sid = str(first["url"]).rstrip("/").rsplit("/", 1)[-1]
    later = b"<html><body>HARBOR-18</body></html>"
    _add_done_version(svc, aid, 2, later)
    client = TestClient(server.preview_app)
    page = client.get(f"/s/{sid}/")
    assert page.status_code == 200
    assert "HARBOR-18" in page.text
    assert "HARBOR-17" not in page.text
    again = svc.share(principal, aid)
    assert again["reused"] is True
    assert again["url"] == first["url"]
    assert again["follows"] == "latest"
    assert again["version"] == 2
    state = svc._share_state(aid, 2)
    assert state["state"] == "shared"
    assert sid in str(state["url"])
    _assert_page_csp(page.headers.get("content-security-policy", ""))


def test_pinned_share_stays_on_its_version(svc: Service):
    aid, _ver, _body = _seed_done_artifact(svc)
    principal = _share_principal()
    floating = svc.share(principal, aid)
    later = b"<html><body>HARBOR-18</body></html>"
    _add_done_version(svc, aid, 2, later)
    pinned = svc.share(principal, aid, version=1)
    frozen = svc.share(principal, aid, pin=True)
    client = TestClient(server.preview_app)
    pin_sid = str(pinned["url"]).rstrip("/").rsplit("/", 1)[-1]
    freeze_sid = str(frozen["url"]).rstrip("/").rsplit("/", 1)[-1]
    float_sid = str(floating["url"]).rstrip("/").rsplit("/", 1)[-1]
    assert pin_sid != float_sid
    assert client.get(f"/s/{pin_sid}/").text.find("HARBOR-17") >= 0
    assert "HARBOR-18" not in client.get(f"/s/{pin_sid}/").text
    assert "HARBOR-18" in client.get(f"/s/{freeze_sid}/").text
    third = b"<html><body>HARBOR-19</body></html>"
    _add_done_version(svc, aid, 3, third)
    assert "HARBOR-18" in client.get(f"/s/{freeze_sid}/").text
    assert "HARBOR-19" not in client.get(f"/s/{freeze_sid}/").text
    assert "HARBOR-19" in client.get(f"/s/{float_sid}/").text
    assert pinned["follows"] == "pinned"


def test_floating_share_hash_mismatch_is_404_and_stale_row_hash_is_not(svc: Service):
    aid, _ver, _body = _seed_done_artifact(svc)
    principal = _share_principal()
    first = svc.share(principal, aid)
    sid = str(first["url"]).rstrip("/").rsplit("/", 1)[-1]
    later = b"<html><body>HARBOR-18</body></html>"
    _add_done_version(svc, aid, 2, later)
    client = TestClient(server.preview_app)
    assert "HARBOR-18" in client.get(f"/s/{sid}/").text
    svc.store.put(
        FakeStore.prefix("alpha", aid, 2) + "index.html",
        b"<html><body>replaced</body></html>",
        "text/html",
    )
    for path in (f"/s/{sid}/", f"/s/{sid}/?raw=1", f"/s/{sid}/document.md"):
        missed = client.get(path)
        assert missed.status_code == 404
        assert b"replaced" not in missed.content


def test_unshare_version_revokes_only_the_pin(svc: Service):
    aid, _ver, _body = _seed_done_artifact(svc)
    principal = _share_principal()
    floating = svc.share(principal, aid)
    pinned = svc.share(principal, aid, version=1)
    svc.unshare(principal, aid, version=1)
    client = TestClient(server.preview_app)
    float_sid = str(floating["url"]).rstrip("/").rsplit("/", 1)[-1]
    pin_sid = str(pinned["url"]).rstrip("/").rsplit("/", 1)[-1]
    assert client.get(f"/s/{float_sid}/").status_code == 200
    assert client.get(f"/s/{pin_sid}/").status_code == 404
    svc.unshare(principal, aid)
    assert client.get(f"/s/{float_sid}/").status_code == 404


def test_shared_link_ok_and_revoked(svc):
    aid, ver, body = _seed_done_artifact(svc)
    sha = hashlib.sha256(body.encode()).hexdigest()
    sid = "publicshareid01"
    svc.db.exec(
        "INSERT INTO shares (id, artifact_id, version, sha256, created_by, created_at, expires_at, revoked_at, pinned) "
        "VALUES (?,?,?,?,?,?,?,NULL,0)",
        sid,
        aid,
        ver,
        sha,
        "agent",
        time.time(),
        time.time() + 86400,
    )
    client = TestClient(server.preview_app)
    r = client.get(f"/s/{sid}/")
    assert r.status_code == 200
    assert body in r.text
    _assert_page_csp(r.headers.get("content-security-policy", ""))
    assert not r.cookies
    svc.db.exec("UPDATE shares SET revoked_at=? WHERE id=?", time.time(), sid)
    assert client.get(f"/s/{sid}/").status_code == 404


def test_missing_openai_key_warning_uses_hostname():
    assert server.missing_openai_key_warning("https://api.openai.com", "") is True
    assert server.missing_openai_key_warning("https://api.openai.com/v1", "") is True
    assert server.missing_openai_key_warning("https://api.openai.com.evil.example", "") is False
    assert server.missing_openai_key_warning("https://api.openai.com", "sk-x") is False
    assert server.missing_openai_key_warning("http://mock-llm:8080", "") is False


@pytest.mark.asyncio
async def test_main_boots_and_shuts_down(svc, monkeypatch):
    """server.main binds the loop, starts workers, and shuts down cleanly."""
    started: list[str] = []

    class _FakeServer:
        def __init__(self, config):
            self.config = config

        async def serve(self):
            started.append(f"{self.config.host}:{self.config.port}")
            await asyncio.sleep(0)

    async def _no_delay(_seconds: float = 0):
        return None

    monkeypatch.setattr(server, "Service", lambda: svc)
    monkeypatch.setattr(server.uvicorn, "Server", _FakeServer)
    monkeypatch.setattr(server.asyncio, "sleep", _no_delay)

    await server.main()
    assert len(started) == 2
    assert server.SVC is None


def _seed_primary(
    svc: Service,
    *,
    aid: str,
    filename: str,
    body: bytes,
    content_type: str,
    fmt: str,
) -> str:
    svc.db.exec(
        "INSERT INTO artifacts (id,workspace,slug,display_name,kind,format,created_by,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        aid,
        "alpha",
        aid,
        "Pilot",
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
        "house",
        fmt,
        "agent",
        time.time(),
    )
    svc.store.put(FakeStore.prefix("alpha", aid, 1) + filename, body, content_type)
    sid = "s" + aid[-12:]
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


def test_markdown_share_is_html_and_raw_markdown_stays_downloadable(svc: Service):
    body = b"# Pilot\n\nThe code is HARBOR-17.\n"
    sid = _seed_primary(
        svc,
        aid="art_markdown_1",
        filename="document.md",
        body=body,
        content_type="text/markdown; charset=utf-8",
        fmt="markdown",
    )
    client = TestClient(server.preview_app)
    page = client.get(f"/s/{sid}/")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "HARBOR-17" in page.text
    assert "<html" in page.text.lower()
    assert "# Pilot" not in page.text
    _assert_page_csp(page.headers.get("content-security-policy", ""))
    raw = client.get(f"/s/{sid}/?raw=1")
    assert raw.status_code == 200
    assert raw.headers["content-type"].startswith("text/markdown")
    assert raw.content == body
    download = client.get(f"/s/{sid}/document.md")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("text/markdown")
    assert download.content == body
    assert "attachment" in download.headers.get("content-disposition", "")


def test_markdown_share_strips_script(svc: Service):
    body = b"# Pilot\n\nThe code is HARBOR-17.\n\n<script>alert(1)</script>\n"
    sid = _seed_primary(
        svc,
        aid="art_markdown_js",
        filename="document.md",
        body=body,
        content_type="text/markdown",
        fmt="markdown",
    )
    page = TestClient(server.preview_app).get(f"/s/{sid}/")
    assert page.status_code == 200
    assert "HARBOR-17" in page.text
    assert "<script" not in page.text.lower()


def test_markdown_share_rejects_host_outside_allow_list(svc: Service, monkeypatch: pytest.MonkeyPatch):
    from artifactsmith.config import CFG

    monkeypatch.setattr(CFG, "allowed_link_domains", ["example.com"])
    body = b"# Pilot\n\nSee [go](https://evil.example/x).\n"
    sid = _seed_primary(
        svc,
        aid="art_markdown_link",
        filename="document.md",
        body=body,
        content_type="text/markdown",
        fmt="markdown",
    )
    page = TestClient(server.preview_app).get(f"/s/{sid}/")
    assert page.status_code == 404
    assert "evil.example" not in page.text


def test_markdown_share_hash_mismatch_is_404(svc: Service):
    body = b"# Pilot\n\nThe code is HARBOR-17.\n"
    sid = _seed_primary(
        svc,
        aid="art_markdown_bad",
        filename="document.md",
        body=body,
        content_type="text/markdown",
        fmt="markdown",
    )
    svc.store.put(
        FakeStore.prefix("alpha", "art_markdown_bad", 1) + "document.md", b"# Other\n\nreplaced\n", "text/markdown"
    )
    client = TestClient(server.preview_app)
    page = client.get(f"/s/{sid}/")
    assert page.status_code == 404
    assert "replaced" not in page.text
    assert "HARBOR-17" not in page.text
    assert client.get(f"/s/{sid}/?raw=1").status_code == 404
    assert client.get(f"/s/{sid}/document.md").status_code == 404


def test_pdf_share_is_not_rendered_as_markdown(svc: Service):
    body = b"%PDF-1.4 fake"
    sid = _seed_primary(
        svc,
        aid="art_pdf_share1",
        filename="document.pdf",
        body=body,
        content_type="application/pdf",
        fmt="pdf",
    )
    page = TestClient(server.preview_app).get(f"/s/{sid}/")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("application/pdf")
    assert page.content == body
