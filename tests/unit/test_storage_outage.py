"""Object-storage disconnects on read routes are 503, not an unhandled 500."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable

import pytest
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    HTTPClientError,
    ProxyConnectionError,
    ReadTimeoutError,
    ResponseStreamingError,
    SSLError,
)
from starlette.testclient import TestClient
from tests.unit.test_server import FakeStore, _seed_done_artifact

import artifactsmith.server as server
from artifactsmith.service import ALL_PERMS, Service, sign
from artifactsmith.store import is_storage_outage

_HOST = "http://storage:9000/artifacts"
_IP = "http://10.1.2.3:9000"
_LEAKS = ("storage:9000", "10.1.2.3")
_PAGE = "This page is briefly unavailable. Try again in a minute."
_TOOL = "Object storage is briefly unavailable. Try again in a minute."

_Outage = Callable[[], BaseException]


def _endpoint() -> BaseException:
    return EndpointConnectionError(endpoint_url=_HOST)


def _connect_timeout() -> BaseException:
    return ConnectTimeoutError(endpoint_url=_HOST)


def _read_timeout() -> BaseException:
    return ReadTimeoutError(endpoint_url=_HOST)


def _closed() -> BaseException:
    return ConnectionClosedError(endpoint_url=_HOST, error="closed")


def _proxy() -> BaseException:
    return ProxyConnectionError(proxy_url=_IP, error="down")


def _stream() -> BaseException:
    return ResponseStreamingError(error="reset storage:9000")


def _wrapped() -> BaseException:
    try:
        raise EndpointConnectionError(endpoint_url=_HOST)
    except EndpointConnectionError as exc:
        try:
            raise RuntimeError("wrapped storage:9000") from exc
        except RuntimeError as wrapped:
            return wrapped


_OUTAGES: list[tuple[str, _Outage]] = [
    ("endpoint", _endpoint),
    ("connect-timeout", _connect_timeout),
    ("read-timeout", _read_timeout),
    ("closed", _closed),
    ("proxy", _proxy),
    ("stream", _stream),
    ("wrapped", _wrapped),
]


@pytest.fixture
def svc(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr("artifactsmith.service.Store", FakeStore)
    monkeypatch.setattr("artifactsmith.server.CFG.data_dir", tmp_path)
    monkeypatch.setattr("artifactsmith.server.CFG.secrets_dir", tmp_path / "secrets")
    (tmp_path / "secrets").mkdir(parents=True, exist_ok=True)
    service = Service()
    server.SVC = service
    yield service
    server.SVC = None


def _share(svc: Service, aid: str, ver: int, body: str) -> str:
    sid = "publicshareid01"
    svc.db.exec(
        "INSERT INTO shares (id, artifact_id, version, sha256, created_by, created_at, expires_at, revoked_at, pinned) "
        "VALUES (?,?,?,?,?,?,?,NULL,0)",
        sid,
        aid,
        ver,
        hashlib.sha256(body.encode()).hexdigest(),
        "agent",
        time.time(),
        time.time() + 86400,
    )
    return sid


def _preview(svc: Service, aid: str, ver: int) -> str:
    sid = "previewtok1"
    svc.db.exec(
        "INSERT INTO short_links VALUES (?,?,?,?,?)",
        sid,
        aid,
        ver,
        "preview",
        time.time() + 3600,
    )
    return sid


def _paths(svc: Service) -> dict[str, str]:
    aid, ver, body = _seed_done_artifact(svc)
    sid = _share(svc, aid, ver, body)
    token = _preview(svc, aid, ver)
    download = sign({"a": aid, "v": ver, "f": "index.html", "n": "page.html"}, ttl_s=60)
    return {
        "share": f"/s/{sid}",
        "share-slash": f"/s/{sid}/",
        "share-raw": f"/s/{sid}/?raw=1",
        "share-md": f"/s/{sid}/document.md",
        "preview": f"/p/{token}",
        "preview-slash": f"/p/{token}/",
        "preview-md": f"/p/{token}/document.md",
        "download": f"/dl/{download}",
    }


def _break_get(svc: Service, make: _Outage) -> None:
    def get(_key: str) -> tuple[bytes, str]:
        raise make()

    svc.store.get = get  # type: ignore[method-assign]


def _assert_unavailable(response) -> None:
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert response.text == _PAGE
    for leak in _LEAKS:
        assert leak not in response.text
        assert leak not in response.headers.get("retry-after", "")


@pytest.mark.parametrize(
    "route",
    [
        "share",
        "share-slash",
        "share-raw",
        "share-md",
        "preview",
        "preview-slash",
        "preview-md",
        "download",
    ],
)
@pytest.mark.parametrize("make", [factory for _name, factory in _OUTAGES], ids=[name for name, _factory in _OUTAGES])
def test_read_routes_return_503_when_storage_is_down(svc: Service, route: str, make: _Outage) -> None:
    paths = _paths(svc)
    _break_get(svc, make)
    _assert_unavailable(TestClient(server.preview_app).get(paths[route]))


def test_healthy_share_preview_and_download_stay_200(svc: Service) -> None:
    paths = _paths(svc)
    client = TestClient(server.preview_app)
    for route in ("share-slash", "preview", "download"):
        response = client.get(paths[route])
        assert response.status_code == 200
        assert "HARBOR-17" in response.text
        assert "retry-after" not in response.headers


@pytest.mark.parametrize(
    "path",
    [
        "/s/missingshareid/",
        "/p/not-a-real-short-id",
        "/dl/forged.signature",
    ],
)
def test_missing_links_stay_404_while_storage_is_down(svc: Service, path: str) -> None:
    _break_get(svc, _endpoint)
    response = TestClient(server.preview_app).get(path)
    assert response.status_code == 404
    assert response.headers.get("retry-after") is None
    assert "storage:9000" not in response.text


def test_revoked_share_stays_404(svc: Service) -> None:
    aid, ver, body = _seed_done_artifact(svc)
    sid = _share(svc, aid, ver, body)
    svc.db.exec("UPDATE shares SET revoked_at=? WHERE id=?", time.time(), sid)
    _break_get(svc, _endpoint)
    response = TestClient(server.preview_app).get(f"/s/{sid}/")
    assert response.status_code == 404


def test_expired_preview_stays_404(svc: Service) -> None:
    aid, ver, _body = _seed_done_artifact(svc)
    svc.db.exec(
        "INSERT INTO short_links VALUES (?,?,?,?,?)",
        "expiredpreview1",
        aid,
        ver,
        "preview",
        time.time() - 10,
    )
    _break_get(svc, _endpoint)
    assert TestClient(server.preview_app).get("/p/expiredpreview1").status_code == 404


def test_missing_file_stays_404(svc: Service) -> None:
    aid, ver, _body = _seed_done_artifact(svc)
    _break_get(svc, _endpoint)
    tok = sign({"a": aid, "v": ver, "f": "missing.css", "n": "missing.css"}, ttl_s=60)
    response = TestClient(server.preview_app).get(f"/dl/{tok}")
    assert response.status_code == 404
    assert "retry-after" not in response.headers


@pytest.mark.parametrize(
    ("exc_type", "make"),
    [
        (ClientError, lambda: ClientError({"Error": {"Code": "NoSuchKey", "Message": _HOST}}, "GetObject")),
        (SSLError, lambda: SSLError(endpoint_url=_HOST, error="cert")),
        (HTTPClientError, lambda: HTTPClientError(error="bug at storage:9000")),
        (RuntimeError, lambda: RuntimeError("storage:9000")),
    ],
)
def test_other_store_errors_are_not_503(svc: Service, exc_type: type[BaseException], make: _Outage) -> None:
    paths = _paths(svc)
    _break_get(svc, make)
    with pytest.raises(exc_type):
        TestClient(server.preview_app).get(paths["share-slash"])


def test_delete_tool_returns_a_storage_error(svc: Service, monkeypatch: pytest.MonkeyPatch) -> None:
    aid, _ver, _body = _seed_done_artifact(svc)
    who = {"id": "tok_1", "name": "agent", "workspace": "alpha", "perms": set(ALL_PERMS)}
    monkeypatch.setattr(server, "_principal", lambda _ctx: who)
    first = server.delete(None, aid)  # type: ignore[arg-type]
    assert first["step"] == "confirm"

    def purge(_prefix: str) -> int:
        raise EndpointConnectionError(endpoint_url=_HOST)

    svc.store.purge_prefix = purge  # type: ignore[method-assign]
    out = server.delete(None, aid, confirm_token=first["confirm_token"])  # type: ignore[arg-type]
    assert out == {"error": _TOOL}
    for leak in _LEAKS:
        assert leak not in out["error"]
    assert svc.db.one("SELECT id FROM artifacts WHERE id=?", aid) is not None


@pytest.mark.asyncio
async def test_status_tool_returns_a_storage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Down:
        async def status(self, *_args, **_kwargs):
            raise ReadTimeoutError(endpoint_url=_IP)

    monkeypatch.setattr(server, "_principal", lambda _ctx: {"id": "tok"})
    monkeypatch.setattr(server, "_svc", lambda: _Down())
    out = await server.status(None)  # type: ignore[arg-type]
    assert out == {"error": _TOOL}
    assert "10.1.2.3" not in out["error"]


def test_run_reraises_unrelated_errors() -> None:
    def boom() -> dict[str, str]:
        raise RuntimeError("storage:9000")

    with pytest.raises(RuntimeError, match="storage:9000"):
        server._run(boom)


def test_is_storage_outage_ignores_client_and_ssl_errors() -> None:
    assert is_storage_outage(_endpoint())
    assert is_storage_outage(_connect_timeout())
    assert is_storage_outage(_read_timeout())
    assert is_storage_outage(_closed())
    assert is_storage_outage(_proxy())
    assert is_storage_outage(_stream())
    assert is_storage_outage(_wrapped())
    assert not is_storage_outage(SSLError(endpoint_url=_HOST, error="cert"))
    assert not is_storage_outage(ClientError({"Error": {"Code": "NoSuchKey", "Message": "x"}}, "GetObject"))
    assert not is_storage_outage(HTTPClientError(error="bug"))
    assert not is_storage_outage(RuntimeError("storage:9000"))
