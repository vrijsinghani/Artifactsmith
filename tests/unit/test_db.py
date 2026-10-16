import sqlite3

import pytest

from artifactsmith.db import DB, jloads


def test_migrate_copies_artifact_format_onto_versions(tmp_path):
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE artifacts (
          id TEXT PRIMARY KEY,
          format TEXT NOT NULL
        );
        CREATE TABLE versions (
          artifact_id TEXT NOT NULL,
          version INTEGER NOT NULL,
          PRIMARY KEY (artifact_id, version)
        );
        INSERT INTO artifacts VALUES ('art_old', 'markdown');
        INSERT INTO versions VALUES ('art_old', 1);
        """
    )
    conn.commit()
    conn.close()
    db = DB(path)
    row = db.one("SELECT format FROM versions WHERE artifact_id=?", "art_old")
    assert row is not None
    assert row["format"] == "markdown"
    db.close()


def test_one_all_exec(tmp_path):
    db = DB(tmp_path / "state.sqlite")
    db.exec(
        "INSERT INTO tokens (id,token_hash,name,workspace,perms,created_at,revoked_at) VALUES (?,?,?,?,?,?,NULL)",
        "tok_1",
        "hash",
        "agent",
        "alpha",
        '["read"]',
        1.0,
    )
    row = db.one("SELECT name FROM tokens WHERE id=?", "tok_1")
    assert row == {"name": "agent"}
    rows = db.all("SELECT id FROM tokens")
    assert rows == [{"id": "tok_1"}]
    assert db.one("SELECT id FROM tokens WHERE id=?", "missing") is None
    assert db.must("SELECT name FROM tokens WHERE id=?", "tok_1")["name"] == "agent"
    with pytest.raises(RuntimeError, match="expected a row"):
        db.must("SELECT id FROM tokens WHERE id=?", "missing")


def test_tx_rollback(tmp_path):
    db = DB(tmp_path / "state.sqlite")
    try:
        with db.tx() as c:
            c.execute(
                "INSERT INTO tokens (id,token_hash,name,workspace,perms,created_at) VALUES (?,?,?,?,?,?)",
                ("tok_1", "h", "a", "ws", "[]", 1.0),
            )
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert db.one("SELECT id FROM tokens WHERE id=?", "tok_1") is None


def test_nested_tx_joins_the_outer(tmp_path):
    db = DB(tmp_path / "state.sqlite")
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO tokens (id,token_hash,name,workspace,perms,created_at) VALUES (?,?,?,?,?,?)",
            ("tok_outer", "h-outer", "a", "ws", "[]", 1.0),
        )
        db.exec(
            "INSERT INTO tokens (id,token_hash,name,workspace,perms,created_at) VALUES (?,?,?,?,?,?)",
            "tok_inner",
            "h-inner",
            "b",
            "ws",
            "[]",
            1.0,
        )
    assert db.one("SELECT id FROM tokens WHERE id=?", "tok_outer") is not None
    assert db.one("SELECT id FROM tokens WHERE id=?", "tok_inner") is not None
    try:
        with db.tx() as conn:
            conn.execute(
                "INSERT INTO tokens (id,token_hash,name,workspace,perms,created_at) VALUES (?,?,?,?,?,?)",
                ("tok_roll", "h-roll", "c", "ws", "[]", 1.0),
            )
            db.exec(
                "INSERT INTO tokens (id,token_hash,name,workspace,perms,created_at) VALUES (?,?,?,?,?,?)",
                "tok_roll_inner",
                "h-roll-inner",
                "d",
                "ws",
                "[]",
                1.0,
            )
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert db.one("SELECT id FROM tokens WHERE id=?", "tok_roll") is None
    assert db.one("SELECT id FROM tokens WHERE id=?", "tok_roll_inner") is None
    db.close()


def test_jloads():
    assert jloads(None, []) == []
    assert jloads("", {"x": 1}) == {"x": 1}
    assert jloads("[1, 2]") == [1, 2]
    assert jloads("not-json", 7) == 7
