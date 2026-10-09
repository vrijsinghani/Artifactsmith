"""Operator CLI:  artifactsmith serve | storage-init | token add|list|revoke

Tokens are stored only as sha256 hashes in SQLite. `token add` prints the raw token once.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import sys
import time
from collections.abc import Callable

from .db import DB
from .service import ALL_PERMS, WS_RE

_TOKEN_MIN = 24


def _make_db() -> DB:
    from .config import CFG

    return DB(CFG.db_path)


def _token_value() -> str:
    return "asmb_" + secrets.token_urlsafe(32)


def cmd_token_add(args: argparse.Namespace) -> int:
    db = _make_db()
    if not WS_RE.match(args.workspace or ""):
        print(json.dumps({"error": "workspace must be 2-41 chars of a-z, 0-9 and '-'"}))
        return 2
    if not re.match(r"^[A-Za-z0-9._-]{1,64}$", args.name or ""):
        print(json.dumps({"error": "name must be 1-64 chars of A-Za-z0-9._-"}))
        return 2
    requested = [p.strip() for p in args.perms.split(",") if p.strip()]
    unknown = sorted({p for p in requested if p not in ALL_PERMS})
    if unknown:
        print(json.dumps({"error": f"unknown perms: {unknown}; allowed: {sorted(ALL_PERMS)}"}))
        return 2
    perms = sorted(set(requested))
    if not perms:
        print(json.dumps({"error": f"no valid perms in {args.perms}; allowed: {sorted(ALL_PERMS)}"}))
        return 2
    raw = args.token or _token_value()
    if not raw or len(raw) < _TOKEN_MIN:
        print(json.dumps({"error": f"token must be at least {_TOKEN_MIN} characters"}))
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
        rows = db.all("SELECT id FROM tokens WHERE id=?", args.id)
    elif args.name:
        rows = db.all("SELECT id FROM tokens WHERE name=?", args.name)
    else:
        print(json.dumps({"error": "pass --id or --name"}))
        return 2
    if not rows:
        print(json.dumps({"error": "no such token"}))
        return 1
    if len(rows) > 1:
        print(
            json.dumps(
                {
                    "error": "name matches multiple tokens; revoke by --id",
                    "ids": [r["id"] for r in rows],
                }
            )
        )
        return 2
    row = rows[0]
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


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="artifactsmith",
        description="ArtifactSmith is an open source artifact server for AI agents.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("serve", help="run the API + preview server")
    sub.add_parser("storage-init", help="create the object-store bucket and enable versioning")

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
        fn: Callable[[argparse.Namespace], int] = args.fn
        return fn(args)
    return {"serve": cmd_serve, "storage-init": cmd_storage_init}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
