from __future__ import annotations

import json

from artifactsmith.cli import main


def test_token_help_exits_ok(capsys):
    try:
        main(["token", "add", "--help"])
    except SystemExit as e:
        assert e.code == 0
    out = capsys.readouterr().out
    assert "--workspace" in out


def test_unknown_perm_rejected(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AM_SECRETS_DIR", str(tmp_path / "secrets"))
    rc = main(["token", "add", "--name", "x", "--workspace", "alpha", "--perms", "nonesuch"])
    assert rc == 2
    assert "error" in json.loads(capsys.readouterr().out)


def test_mock_llm_command_removed():
    try:
        main(["mock-llm"])
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError("mock-llm must not be a CLI command")
