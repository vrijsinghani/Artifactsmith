from artifactsmith.db import DB, jloads


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
    try:
        db.must("SELECT id FROM tokens WHERE id=?", "missing")
    except RuntimeError as e:
        assert "expected a row" in str(e)
    else:
        raise AssertionError("must() should raise")


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


def test_jloads():
    assert jloads(None, []) == []
    assert jloads("", {"x": 1}) == {"x": 1}
    assert jloads("[1, 2]") == [1, 2]
    assert jloads("not-json", 7) == 7
