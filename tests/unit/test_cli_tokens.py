from __future__ import annotations

import hashlib
import json

from artifactsmith.cli import main
from artifactsmith.config import CFG
from artifactsmith.db import DB


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


def _audit_lines() -> list[dict[str, object]]:
    path = CFG.audit_path
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_token_perms_add_remove_and_list(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AM_SECRETS_DIR", str(tmp_path / "secrets"))
    raw = "asmb_testtokenvalue_long_enough"
    rc = main(["token", "add", "--name", "agent", "--workspace", "alpha", "--perms", "create,read", "--token", raw])
    assert rc == 0
    added = json.loads(capsys.readouterr().out)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()

    rc = main(["token", "perms", "--name", "agent"])
    assert rc == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["id"] == added["id"]
    assert listed["perms"] == ["create", "read"]
    assert _audit_lines() == []

    rc = main(["token", "perms", "--name", "agent", "--add", "publish"])
    assert rc == 0
    granted = json.loads(capsys.readouterr().out)
    assert granted["perms"] == ["create", "publish", "read"]
    assert granted["action"] == "token_perm_add"

    rc = main(["token", "perms", "--id", added["id"], "--remove", "create"])
    assert rc == 0
    removed = json.loads(capsys.readouterr().out)
    assert removed["perms"] == ["publish", "read"]
    assert removed["action"] == "token_perm_remove"

    row = DB(CFG.db_path).one("SELECT token_hash, perms FROM tokens WHERE id=?", added["id"])
    assert row is not None
    assert row["token_hash"] == token_hash
    assert json.loads(row["perms"]) == ["publish", "read"]

    lines = _audit_lines()
    assert [line["action"] for line in lines] == ["token_perm_add", "token_perm_remove"]
    assert lines[0]["perm"] == "publish"
    assert lines[0]["actor"] == "cli"
    assert lines[0]["token_id"] == added["id"]
    assert lines[1]["perm"] == "create"
    assert raw not in json.dumps(lines)

    rc = main(["token", "perms", "--name", "agent"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["perms"] == ["publish", "read"]
    assert len(_audit_lines()) == 2


def test_token_perms_rejects_bad_input(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AM_SECRETS_DIR", str(tmp_path / "secrets"))
    raw = "asmb_testtokenvalue_long_enough"
    assert main(["token", "add", "--name", "agent", "--workspace", "alpha", "--perms", "read", "--token", raw]) == 0
    capsys.readouterr()
    assert (
        main(["token", "add", "--name", "agent", "--workspace", "alpha", "--perms", "read", "--token", raw + "b"]) == 0
    )
    capsys.readouterr()

    rc = main(["token", "perms", "--name", "agent", "--add", "publish"])
    assert rc == 2
    assert "pass --id" in json.loads(capsys.readouterr().out)["error"]

    rc = main(["token", "perms", "--name", "missing", "--add", "publish"])
    assert rc == 1
    assert json.loads(capsys.readouterr().out)["error"] == "no such token"

    rc = main(["token", "perms"])
    assert rc == 2
    assert json.loads(capsys.readouterr().out)["error"] == "pass --id or --name"

    rc = main(["token", "perms", "--name", "nope", "--add", "publish", "--remove", "read"])
    assert rc == 1
    assert json.loads(capsys.readouterr().out)["error"] == "no such token"

    tok_id = DB(CFG.db_path).one("SELECT id FROM tokens LIMIT 1")
    assert tok_id is not None
    rc = main(["token", "perms", "--id", tok_id["id"], "--add", "publish", "--remove", "read"])
    assert rc == 2
    assert "only one" in json.loads(capsys.readouterr().out)["error"]

    rc = main(["token", "perms", "--id", tok_id["id"], "--add", "nonesuch"])
    assert rc == 2
    body = json.loads(capsys.readouterr().out)
    assert "unknown perm" in body["error"]
    assert _audit_lines() == []

    rc = main(["token", "perms", "--id", tok_id["id"], "--add", "publish"])
    assert rc == 0
    capsys.readouterr()
    rc = main(["token", "perms", "--id", tok_id["id"], "--add", "publish"])
    assert rc == 1
    assert "already has" in json.loads(capsys.readouterr().out)["error"]

    rc = main(["token", "perms", "--id", tok_id["id"], "--remove", "delete"])
    assert rc == 1
    assert "does not have" in json.loads(capsys.readouterr().out)["error"]
    assert len(_audit_lines()) == 1
