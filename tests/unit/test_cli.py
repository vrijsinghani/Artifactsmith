from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from artifactsmith.cli import STORAGE_INIT_TIMEOUT_S, cmd_storage_init, main
from artifactsmith.config import CFG


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


class _Store:
    bucket = "artifacts"

    def __init__(self, result, **kwargs):
        self._result = result
        self.kwargs = kwargs

    def ensure_bucket(self):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _install_store(monkeypatch, result):
    seen: dict[str, object] = {}

    def factory(**kwargs):
        seen.update(kwargs)
        return _Store(result, **kwargs)

    monkeypatch.setattr("artifactsmith.store.Store", factory)
    return seen


def _freeze_clock(monkeypatch):
    clock = {"t": 0.0}

    def monotonic():
        return clock["t"]

    def sleep(seconds):
        clock["t"] += seconds

    monkeypatch.setattr("artifactsmith.cli.time.monotonic", monotonic)
    monkeypatch.setattr("artifactsmith.cli.time.sleep", sleep)
    return clock


def test_storage_init_succeeds_without_waiting(monkeypatch, capsys):
    monkeypatch.setattr(CFG, "store_endpoint", "http://storage:9000")
    seen = _install_store(monkeypatch, True)
    _freeze_clock(monkeypatch)
    assert cmd_storage_init(argparse.Namespace()) == 0
    assert seen == {"connect_timeout": 10.0, "read_timeout": 30.0, "max_attempts": 0}
    out = capsys.readouterr()
    assert json.loads(out.out) == {
        "bucket": "artifacts",
        "created": True,
        "versioning": "enabled",
        "attempts": 1,
    }
    assert out.err == ""


def test_storage_init_times_out_and_names_the_endpoint(monkeypatch, capsys):
    endpoint = "http://storage:9000"
    monkeypatch.setattr(CFG, "store_endpoint", endpoint)
    _install_store(monkeypatch, ConnectionError("aws_secret_access_key=supersecretvalue"))
    clock = _freeze_clock(monkeypatch)
    assert cmd_storage_init(argparse.Namespace()) == 1
    assert clock["t"] == STORAGE_INIT_TIMEOUT_S
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"waiting for storage at {endpoint} (attempt 1)" in captured.err
    assert f"storage at {endpoint} was not ready after {STORAGE_INIT_TIMEOUT_S}s (ConnectionError)" in captured.err
    assert "supersecretvalue" not in captured.err


def test_storage_init_names_an_empty_endpoint(monkeypatch, capsys):
    monkeypatch.setattr(CFG, "store_endpoint", "")
    _install_store(monkeypatch, TimeoutError("down"))
    _freeze_clock(monkeypatch)
    assert cmd_storage_init(argparse.Namespace()) == 1
    err = capsys.readouterr().err
    assert "AM_STORE_ENDPOINT is empty" in err
    assert "TimeoutError" in err


def test_storage_init_disables_the_image_healthcheck():
    compose = Path("compose.yaml").read_text()
    init = compose.split("  storage-init:", 1)[1].split("\n  server:", 1)[0]
    assert "healthcheck:" in init
    assert "disable: true" in init
    dockerfile = Path("docker/Dockerfile").read_text()
    assert "HEALTHCHECK" in dockerfile
    assert "127.0.0.1:8780/healthz" in dockerfile


def test_quickstart_lists_prerequisites_and_the_stdio_limit():
    readme = Path("README.md").read_text()
    quick = readme.split("## Quickstart", 1)[1].split("## Serving", 1)[0]
    assert quick.startswith("\n\nhttps://github.com/vrijsinghani/Artifactsmith\n")
    assert "You need Docker with Compose" in quick
    assert "OPENAI_API_KEY" in quick
    assert "AM_LLM_BASE" in quick
    assert "git clone https://github.com/vrijsinghani/Artifactsmith" in quick
    assert "docker compose up -d --build" in quick
    assert "http://127.0.0.1:8780/mcp" in quick
    assert "list_tools" in quick
    assert 'call_tool("create"' in quick
    assert "publish" in quick
    assert "uv tool install git+https://github.com/vrijsinghani/Artifactsmith" in quick
    assert "pip install git+https://github.com/vrijsinghani/Artifactsmith" in quick
    assert '"args": ["stdio"]' in quick
    assert "stdio cannot use the object store or the model" in quick
    assert "after 120 seconds" in quick
    assert "AM_STORE_ENDPOINT" in quick
    assert "trial" not in quick.lower()
