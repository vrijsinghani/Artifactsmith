"""Local stdio transport: same tool registry, no socket, no store or model calls."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

import artifactsmith.server as server
import artifactsmith.stdio_mode as stdio_mode
from artifactsmith.cli import main
from artifactsmith.db import DB
from artifactsmith.service import AMError

_DUMMY = {
    "AM_STORE_ENDPOINT": "https://s3.example.com",
    "AM_STORE_KEY": "dummy",
    "AM_STORE_SECRET": "dummy",
    "OPENAI_API_KEY": "sk-dummy",
}


def _dummy_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(_DUMMY)
    if extra:
        env.update(extra)
    return env


def _params(env: dict[str, str]) -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=["-m", "artifactsmith", "stdio"], env=env)


def _proc_pids() -> set[int]:
    found: set[int] = set()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if b"artifactsmith\x00stdio" in raw or raw.endswith(b"artifactsmith\x00stdio"):
            found.add(int(entry.name))
    return found


def _listening(pid: int) -> list[str]:
    inodes: set[str] = set()
    fd_dir = Path(f"/proc/{pid}/fd")
    if not fd_dir.is_dir():
        return []
    for fd in fd_dir.iterdir():
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        if target.startswith("socket:[") and target.endswith("]"):
            inodes.add(target[8:-1])
    hits: list[str] = []
    for name in ("tcp", "tcp6"):
        net = Path(f"/proc/{pid}/net/{name}")
        if not net.is_file():
            continue
        for line in net.read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 10 and parts[3] == "0A" and parts[9] in inodes:
                hits.append(parts[1])
    return hits


def _names(path: Path) -> list[str] | None:
    if not path.exists():
        return None
    return sorted(child.name for child in path.iterdir())


def _tool_blob(tool: Any) -> dict[str, Any]:
    return {"name": tool.name, "description": tool.description, "inputSchema": tool.inputSchema}


def _body(result: Any) -> Any:
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    text = result.content[0].text
    return json.loads(text)


class _Request:
    def __init__(self, principal: dict[str, Any] | None) -> None:
        self.scope = {"am_principal": principal} if principal is not None else {}


class _Context:
    def __init__(self, request: object | None) -> None:
        self.request_context = _RC(request)


class _RC:
    def __init__(self, request: object | None) -> None:
        self.request = request


class _Lookup:
    @property
    def request_context(self) -> object:
        raise LookupError("no request context")


def _seed(path: Path) -> None:
    db = DB(path / "state.sqlite")
    now = time.time()
    db.exec(
        "INSERT INTO artifacts (id,workspace,slug,display_name,kind,format,created_by,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        "art_done",
        "local",
        "done-page",
        "Done",
        "web_static",
        "html",
        "local",
        now,
        now,
    )
    db.exec(
        "INSERT INTO versions (artifact_id,version,base_version,verbatim_request,status,job_id,model,"
        "sha256,primary_file,files_json,assumptions_json,summary,size,source_key,created_by,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        "art_done",
        1,
        None,
        "publish it",
        "done",
        "job_done",
        "published",
        "abc",
        "index.html",
        "[]",
        "[]",
        "ok",
        1,
        None,
        "local",
        now,
    )
    db.exec(
        "INSERT INTO jobs (id,artifact_id,version,client,status,progress,error,created_at) VALUES (?,?,?,?,?,?,?,?)",
        "job_done",
        "art_done",
        1,
        "local",
        "done",
        None,
        None,
        now,
    )
    db.exec(
        "INSERT INTO artifacts (id,workspace,slug,display_name,kind,format,created_by,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        "art_queue",
        "local",
        "queued-page",
        "Queued",
        "web_static",
        "html",
        "local",
        now,
        now,
    )
    db.exec(
        "INSERT INTO versions (artifact_id,version,base_version,verbatim_request,status,job_id,model,"
        "sha256,primary_file,files_json,assumptions_json,summary,size,source_key,created_by,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        "art_queue",
        1,
        None,
        "still working",
        "queued",
        "job_q",
        "m",
        None,
        None,
        "[]",
        "[]",
        None,
        None,
        None,
        "local",
        now,
    )
    db.exec(
        "INSERT INTO jobs (id,artifact_id,version,client,status,progress,error,created_at) VALUES (?,?,?,?,?,?,?,?)",
        "job_q",
        "art_queue",
        1,
        "local",
        "queued",
        None,
        None,
        now,
    )
    db.conn.close()


@pytest.mark.asyncio
async def test_stdio_handshake_matches_http_registry_and_refuses_create() -> None:
    data_before = _names(Path("/data"))
    secrets_before = _names(Path("/secrets"))
    before = _proc_pids()
    async with stdio_client(_params(_dummy_env())) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            prompts = await session.list_prompts()
            resources = await session.list_resources()
            child = _proc_pids() - before
            assert child, "stdio process did not stay up for the handshake"
            for pid in child:
                assert _listening(pid) == []
            started = time.monotonic()
            created = await asyncio.wait_for(
                session.call_tool("create", {"verbatim_request": "hello", "slug": "pilot-note"}),
                timeout=5,
            )
            assert time.monotonic() - started < 3
            catalog = await session.call_tool("list_artifacts", {})
            other = await session.call_tool("list_artifacts", {"workspace": "other-ws"})
    assert not (_proc_pids() - before)
    assert _names(Path("/data")) == data_before
    assert _names(Path("/secrets")) == secrets_before

    http_tools = {blob["name"]: blob for blob in (_tool_blob(tool) for tool in await server.mcp.list_tools())}
    stdio_tools = {blob["name"]: blob for blob in (_tool_blob(tool) for tool in listed.tools)}
    assert stdio_tools == http_tools
    assert "publish" in stdio_tools
    assert prompts.prompts == []
    assert resources.resources == []
    assert created.isError
    assert "stdio" in created.content[0].text
    body = _body(catalog)
    assert body["count"] == 0
    assert other.isError or "forbidden" in other.content[0].text


@pytest.mark.asyncio
async def test_stdio_refuses_store_reads_and_delete_without_writing(tmp_path: Path) -> None:
    _seed(tmp_path)
    env = _dummy_env({"AM_DATA_DIR": str(tmp_path), "AM_SECRETS_DIR": str(tmp_path / "secrets")})
    async with stdio_client(_params(env)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            plain = await asyncio.wait_for(
                session.call_tool("inspect", {"artifact_id": "art_done", "include_content": False}),
                timeout=5,
            )
            with_content = await asyncio.wait_for(
                session.call_tool("inspect", {"artifact_id": "art_done", "include_content": True}),
                timeout=5,
            )
            started = time.monotonic()
            published = await asyncio.wait_for(
                session.call_tool("status", {"artifact_id": "art_done", "wait": 90}),
                timeout=5,
            )
            queued = await asyncio.wait_for(
                session.call_tool("status", {"artifact_id": "art_queue", "wait": 90}),
                timeout=5,
            )
            removed = await asyncio.wait_for(
                session.call_tool("delete", {"artifact_id": "art_done"}),
                timeout=5,
            )
            assert time.monotonic() - started < 3
    assert plain.isError
    assert "content_error" not in plain.content[0].text
    assert with_content.isError
    assert published.isError
    queued_body = _body(queued)
    assert queued.isError is False
    assert queued_body["status"] == "queued"
    assert removed.isError

    db = DB(tmp_path / "state.sqlite")
    row = db.one("SELECT deleting_at FROM artifacts WHERE id=?", "art_done")
    assert row is not None
    assert row["deleting_at"] is None
    assert db.one("SELECT COUNT(*) n FROM delete_tokens")["n"] == 0
    db.conn.close()


def test_flag_off_missing_request_stays_unauthenticated() -> None:
    assert stdio_mode.enabled is False
    with pytest.raises(AMError, match="unauthenticated"):
        server._principal(_Context(None))  # type: ignore[arg-type]


def test_lookup_error_is_not_the_local_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stdio_mode, "enabled", True)
    with pytest.raises(AMError, match="unauthenticated"):
        server._principal(_Lookup())  # type: ignore[arg-type]


def test_present_request_keeps_http_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stdio_mode, "enabled", True)
    principal = server._principal(
        _Context(_Request({"id": "tok", "name": "limited", "workspace": "alpha", "perms": {"read"}, "trial": False}))
    )  # type: ignore[arg-type]
    assert principal["name"] == "limited"
    assert "share" not in principal["perms"]


def test_flag_on_token_without_share_cannot_share(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class _Store:
        def __init__(self) -> None:
            return

    monkeypatch.setattr(stdio_mode, "enabled", True)
    monkeypatch.setattr("artifactsmith.service.Store", _Store)
    monkeypatch.setattr(server.CFG, "data_dir", tmp_path)
    monkeypatch.setattr(server.CFG, "secrets_dir", tmp_path)
    from artifactsmith.service import Service

    service = Service()
    server.SVC = service
    try:
        result = server.share(
            _Context(
                _Request({"id": "tok", "name": "limited", "workspace": "local", "perms": {"read"}, "trial": False})
            ),  # type: ignore[arg-type]
            "art_missing",
        )
    finally:
        server.SVC = None
    assert "not permitted to share" in result["error"]


@pytest.mark.asyncio
async def test_flag_on_trial_request_still_omits_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp.server.lowlevel.server import request_ctx
    from mcp.shared.context import RequestContext

    monkeypatch.setattr(stdio_mode, "enabled", True)

    async def names(request: object | None) -> list[str]:
        token = request_ctx.set(
            RequestContext(
                request_id="1",
                meta=None,
                session=None,  # type: ignore[arg-type]
                lifespan_context=None,
                request=request,
            )
        )
        try:
            return [tool.name for tool in await server.mcp.list_tools()]
        finally:
            request_ctx.reset(token)

    hidden = await names(_Request({"trial": True}))
    assert "publish" not in hidden
    assert "publish" in await names(_Request({"trial": False}))
    assert "publish" in await names(None)


def test_serve_is_still_the_http_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    def _run() -> None:
        called.append("serve")

    monkeypatch.setattr(server, "run", _run)
    assert main(["serve"]) == 0
    assert called == ["serve"]


def test_unknown_command_exits_2() -> None:
    with pytest.raises(SystemExit) as caught:
        main(["no-such-command"])
    assert caught.value.code == 2
