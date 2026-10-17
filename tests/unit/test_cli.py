from __future__ import annotations

import json
from pathlib import Path

import pytest

from artifactsmith.cli import STORAGE_INIT_ATTEMPTS, main


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

    class Boom:
        bucket = "artifacts"

        def ensure_bucket(self) -> bool:
            raise ConnectionError(f"Could not connect to the endpoint URL: http://{host}/artifacts")

    monkeypatch.setattr("artifactsmith.store.Store", Boom)
    monkeypatch.setattr("artifactsmith.cli.time.sleep", lambda _s: None)
    assert main(["storage-init"]) == 1
    captured = capsys.readouterr()
    lines = [line for line in captured.err.splitlines() if line.startswith("storage-init:")]
    assert lines == [f"storage-init: attempt {n}/{STORAGE_INIT_ATTEMPTS}" for n in range(1, STORAGE_INIT_ATTEMPTS + 1)]
    payload = json.loads(captured.out)
    assert payload == {"error": "object store not ready", "attempts": STORAGE_INIT_ATTEMPTS, "cause": "ConnectionError"}
    combined = captured.out + captured.err
    assert host not in combined
    assert "http://" not in combined


def test_storage_init_returns_when_the_bucket_is_ready(monkeypatch, capsys):
    calls = {"n": 0}

    class Flaky:
        bucket = "artifacts"

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


def test_compose_disables_storage_init_healthcheck_only():
    text = Path("compose.yaml").read_text(encoding="utf-8")
    init = text.split("  storage-init:", 1)[1].split("\n  server:", 1)[0]
    server = text.split("\n  server:", 1)[1].split("\nvolumes:", 1)[0]
    assert "healthcheck:" in init
    assert "disable: true" in init
    assert "disable: true" not in server
