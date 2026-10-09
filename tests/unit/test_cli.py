from __future__ import annotations

import json

import pytest

from artifactsmith.cli import main


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
