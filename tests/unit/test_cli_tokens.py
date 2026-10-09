from __future__ import annotations

import json

from artifactsmith.cli import main


def test_token_add_list_revoke(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AM_SECRETS_DIR", str(tmp_path / "secrets"))
    raw = "asmb_testtokenvalue_long_enough"
    rc = main(["token", "add", "--name", "agent", "--workspace", "alpha", "--token", raw])
    assert rc == 0
    added = json.loads(capsys.readouterr().out)
    assert added["token"] == raw
    assert added["workspace"] == "alpha"

    rc = main(["token", "list"])
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["name"] == "agent"

    rc = main(["token", "revoke", "--name", "agent"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["revoked"] == added["id"]
