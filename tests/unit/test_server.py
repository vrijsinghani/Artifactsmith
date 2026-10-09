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
    assert "script-src 'none'" in r.headers.get("content-security-policy", "")
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
    assert "script-src 'none'" in r.headers.get("content-security-policy", "")
    assert not r.cookies


def test_shared_link_ok_and_revoked(svc):
    aid, ver, body = _seed_done_artifact(svc)
    sha = hashlib.sha256(body.encode()).hexdigest()
    sid = "publicshareid01"
    svc.db.exec(
        "INSERT INTO shares VALUES (?,?,?,?,?,?,?,NULL)",
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
