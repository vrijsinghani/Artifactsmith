from __future__ import annotations

import json
from pathlib import Path

import pytest

from artifactsmith.cli import _STORAGE_INIT_ERROR, STORAGE_INIT_ATTEMPTS, main


class _StoreStub:
    bucket = "artifacts"
    last_kwargs: dict = {}

    def __init__(self, *_args, **kwargs):
        _StoreStub.last_kwargs = kwargs


def test_token_help_exits_ok(capsys):
    with pytest.raises(SystemExit) as e:
        main(["token", "add", "--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "--workspace" in out


def test_unknown_perm_rejected(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AM_SECRETS_DIR", str(tmp_path / "secrets"))
    rc = main(["token", "add", "--name", "x", "--workspace", "alpha", "--perms", "nonesuch"])
    assert rc == 2
    assert "error" in json.loads(capsys.readouterr().out)


def test_removed_mock_llm_cli_command():
    with pytest.raises(SystemExit) as e:
        main(["mock-llm"])
    assert e.value.code == 2


def test_storage_init_prints_one_line_per_attempt_and_exits(monkeypatch, capsys):
    host = "storage:9000"

    class Boom(_StoreStub):
        def ensure_bucket(self) -> bool:
            raise ConnectionError(f"Could not connect to the endpoint URL: http://{host}/artifacts")

    monkeypatch.setattr("artifactsmith.store.Store", Boom)
    monkeypatch.setattr("artifactsmith.cli.time.sleep", lambda _s: None)
    assert main(["storage-init"]) == 1
    captured = capsys.readouterr()
    lines = [line for line in captured.err.splitlines() if line.startswith("storage-init:")]
    assert lines == [f"storage-init: attempt {n}/{STORAGE_INIT_ATTEMPTS}" for n in range(1, STORAGE_INIT_ATTEMPTS + 1)]
    payload = json.loads(captured.out)
    assert payload["attempts"] == STORAGE_INIT_ATTEMPTS
    assert payload["cause"] == "ConnectionError"
    assert payload["error"] == _STORAGE_INIT_ERROR
    combined = captured.out + captured.err
    assert host not in combined
    assert "http://" not in combined


def test_storage_init_returns_when_the_bucket_is_ready(monkeypatch, capsys):
    calls = {"n": 0}

    class Flaky(_StoreStub):
        def ensure_bucket(self) -> bool:
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("Could not connect to the endpoint URL: http://storage:9000/artifacts")
            return True

    monkeypatch.setattr("artifactsmith.store.Store", Flaky)
    monkeypatch.setattr("artifactsmith.cli.time.sleep", lambda _s: None)
    assert main(["storage-init"]) == 0
    captured = capsys.readouterr()
    assert captured.err.splitlines() == [
        f"storage-init: attempt 1/{STORAGE_INIT_ATTEMPTS}",
        f"storage-init: attempt 2/{STORAGE_INIT_ATTEMPTS}",
    ]
    payload = json.loads(captured.out)
    assert payload["created"] is True
    assert payload["attempts"] == 2
    assert "storage:9000" not in captured.out + captured.err


def test_storage_init_reports_the_underlying_error(monkeypatch, capsys):
    class ConnectTimeoutError(Exception):
        pass

    class EndpointConnectionError(Exception):
        pass

    from artifactsmith.store import StoreError

    def _wrapped(inner: Exception) -> None:
        raise StoreError("object store bucket unavailable") from inner

    class Boom(_StoreStub):
        def ensure_bucket(self) -> bool:
            _wrapped(ConnectTimeoutError("timed out"))

    monkeypatch.setattr("artifactsmith.cli.STORAGE_INIT_ATTEMPTS", 1)
    monkeypatch.setattr("artifactsmith.store.Store", Boom)
    monkeypatch.setattr("artifactsmith.cli.time.sleep", lambda _s: None)
    assert main(["storage-init"]) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["cause"] == "ConnectTimeoutError"
    assert payload["error"] == _STORAGE_INIT_ERROR
    assert "http://" not in captured.out + captured.err

    class EndpointBoom(Boom):
        def ensure_bucket(self) -> bool:
            _wrapped(EndpointConnectionError("could not connect"))

    monkeypatch.setattr("artifactsmith.store.Store", EndpointBoom)
    assert main(["storage-init"]) == 1
    second = capsys.readouterr()
    assert json.loads(second.out)["cause"] == "EndpointConnectionError"


def test_storage_init_keeps_storeerror_without_a_cause(monkeypatch, capsys):
    from artifactsmith.store import StoreError

    class Boom(_StoreStub):
        def ensure_bucket(self) -> bool:
            raise StoreError("bucket versioning is 'Suspended', expected 'Enabled'")

    monkeypatch.setattr("artifactsmith.cli.STORAGE_INIT_ATTEMPTS", 1)
    monkeypatch.setattr("artifactsmith.store.Store", Boom)
    monkeypatch.setattr("artifactsmith.cli.time.sleep", lambda _s: None)
    assert main(["storage-init"]) == 1
    assert json.loads(capsys.readouterr().out)["cause"] == "StoreError"
    assert _StoreStub.last_kwargs == {"connect_timeout": 2, "read_timeout": 5, "max_attempts": 0}


def test_storage_init_stops_when_the_deadline_passes(monkeypatch, capsys):
    clock = {"t": 0.0}

    class Boom(_StoreStub):
        def ensure_bucket(self) -> bool:
            clock["t"] = 61.0
            raise ConnectionError("down")

    monkeypatch.setattr("artifactsmith.cli.time.monotonic", lambda: clock["t"])
    monkeypatch.setattr("artifactsmith.cli.time.sleep", lambda _s: None)
    monkeypatch.setattr("artifactsmith.store.Store", Boom)
    assert main(["storage-init"]) == 1
    captured = capsys.readouterr()
    lines = [line for line in captured.err.splitlines() if line.startswith("storage-init:")]
    assert lines == [f"storage-init: attempt 1/{STORAGE_INIT_ATTEMPTS}"]
    payload = json.loads(captured.out)
    assert payload["attempts"] == 1
    assert payload["cause"] == "ConnectionError"


def test_compose_smoke_stops_storage_for_storage_init():
    text = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    smoke = text.split("name: compose smoke", 1)[1].split("\n  bind-smoke:", 1)[0]
    assert "docker compose -f compose.yaml -f compose.test.yaml stop storage" in smoke
    assert "timeout 150" in smoke
    assert "--no-deps" in smoke
    assert "storage-init" in smoke
    guidance = "check that the storage service is running and AM_STORE_ENDPOINT points at it"
    assert guidance in _STORAGE_INIT_ERROR
    assert guidance in smoke


def test_compose_disables_storage_init_healthcheck_only():
    text = Path("compose.yaml").read_text(encoding="utf-8")
    init = text.split("  storage-init:", 1)[1].split("\n  server:", 1)[0]
    server = text.split("\n  server:", 1)[1].split("\nvolumes:", 1)[0]
    assert "healthcheck:" in init
    assert "disable: true" in init
    assert "disable: true" not in server
