"""Operator CLI:  artifactsmith serve | storage-init | token add|list|revoke | mock-llm

Tokens are stored only as sha256 hashes in SQLite. `token add` prints the raw token once.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
import time

from .service import ALL_PERMS


def _make_db():
    from .config import CFG
    from .db import DB

    return DB(CFG.db_path)


def _token_value() -> str:
    return "asmb_" + secrets.token_urlsafe(32)


def cmd_token_add(args: argparse.Namespace) -> int:
    db = _make_db()
    perms = sorted({p.strip() for p in args.perms.split(",") if p.strip()} & ALL_PERMS)
    if not perms:
        print(json.dumps({"error": f"no valid perms in {args.perms}; allowed: {sorted(ALL_PERMS)}"}))
        return 2
    raw = args.token or _token_value()
    if not raw or len(raw) < 8:
        print(json.dumps({"error": "token must be at least 8 characters"}))
        return 2
    tok_id = "tok_" + secrets.token_urlsafe(8)
    db.exec(
        "INSERT INTO tokens (id,token_hash,name,workspace,perms,created_at,revoked_at) VALUES (?,?,?,?,?,?,NULL)",
        tok_id,
        hashlib.sha256(raw.encode()).hexdigest(),
        args.name,
        args.workspace,
        json.dumps(perms),
        time.time(),
    )
    print(json.dumps({"id": tok_id, "name": args.name, "workspace": args.workspace, "perms": perms, "token": raw}))
    return 0


def cmd_token_list(args: argparse.Namespace) -> int:
    db = _make_db()
    rows = db.all("SELECT id,name,workspace,perms,created_at,revoked_at FROM tokens ORDER BY created_at")
    for r in rows:
        r["perms"] = json.loads(r["perms"])
    print(json.dumps(rows, indent=2))
    return 0


def cmd_token_revoke(args: argparse.Namespace) -> int:
    db = _make_db()
    if args.id:
        row = db.one("SELECT id FROM tokens WHERE id=?", args.id)
    else:
        row = db.one("SELECT id FROM tokens WHERE name=?", args.name)
    if not row:
        print(json.dumps({"error": "no such token"}))
        return 1
    db.exec("UPDATE tokens SET revoked_at=? WHERE id=?", time.time(), row["id"])
    print(json.dumps({"revoked": row["id"]}))
    return 0


def cmd_storage_init(args: argparse.Namespace) -> int:
    from .store import Store

    store = Store()
    last = None
    for attempt in range(60):
        try:
            created = store.ensure_bucket()
            print(
                json.dumps(
                    {"bucket": store.bucket, "created": created, "versioning": "enabled", "attempts": attempt + 1}
                )
            )
            return 0
        except Exception as e:  # noqa: BLE001
            last = str(e)
            time.sleep(1)
    print(json.dumps({"error": f"object store not ready: {last}"}))
    return 1


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import run

    run()
    return 0


def cmd_mock_llm(args: argparse.Namespace) -> int:
    from .mock_llm import run

    run()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="artifactsmith",
        description="ArtifactSmith is an open source artifact server for AI agents.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("serve", help="run the API + preview server")
    sub.add_parser("storage-init", help="create the object-store bucket and enable versioning")
    sub.add_parser("mock-llm", help="run the dev/test mock chat-completions endpoint")

    tok = sub.add_parser("token", help="manage access tokens").add_subparsers(dest="token_cmd", required=True)
    add = tok.add_parser("add", help="add a token")
    add.add_argument("--name", required=True)
    add.add_argument("--workspace", required=True)
    add.add_argument("--perms", default="create,read,edit,export,share,delete")
    add.add_argument("--token", default=None, help="use a specific token value instead of generating one")
    add.set_defaults(fn=cmd_token_add)
    lst = tok.add_parser("list", help="list tokens")
    lst.set_defaults(fn=cmd_token_list)
    rev = tok.add_parser("revoke", help="revoke a token by --id or --name")
    rev.add_argument("--id", default=None)
    rev.add_argument("--name", default=None)
    rev.set_defaults(fn=cmd_token_revoke)

    args = parser.parse_args(argv)
    if getattr(args, "cmd", None) == "token":
        return args.fn(args)
    return {"serve": cmd_serve, "storage-init": cmd_storage_init, "mock-llm": cmd_mock_llm}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
