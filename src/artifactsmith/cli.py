"""Operator CLI:  artifactsmith serve | stdio | storage-init | token add|list|revoke|perms

Tokens are stored only as sha256 hashes in SQLite. `token add` prints the raw token once.
`token perms` changes permissions on an existing token and does not print or replace the secret.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .db import DB
from .service import ALL_PERMS, WS_RE, write_audit

_TOKEN_MIN = 24
STORAGE_INIT_ATTEMPTS = 60


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


def _resolve_token(db: DB, args: argparse.Namespace, *, multi: str) -> tuple[int, dict[str, Any] | None]:
    if args.id:
        rows = db.all("SELECT id, name, workspace, perms, revoked_at FROM tokens WHERE id=?", args.id)
    elif args.name:
        rows = db.all("SELECT id, name, workspace, perms, revoked_at FROM tokens WHERE name=?", args.name)
    else:
        print(json.dumps({"error": "pass --id or --name"}))
        return 2, None
    if not rows:
        print(json.dumps({"error": "no such token"}))
        return 1, None
    if len(rows) > 1:
        print(json.dumps({"error": multi, "ids": [r["id"] for r in rows]}))
        return 2, None
    row = rows[0]
    row["perms"] = json.loads(row["perms"])
    return 0, row


def _known_perm(raw: str) -> str | None:
    perm = raw.strip()
    if perm not in ALL_PERMS:
        print(json.dumps({"error": f"unknown perm: {perm}; allowed: {sorted(ALL_PERMS)}"}))
        return None
    return perm


def cmd_token_revoke(args: argparse.Namespace) -> int:
    db = _make_db()
    code, row = _resolve_token(db, args, multi="name matches multiple tokens; revoke by --id")
    if code or row is None:
        return code
    db.exec("UPDATE tokens SET revoked_at=? WHERE id=?", time.time(), row["id"])
    print(json.dumps({"revoked": row["id"]}))
    return 0


def cmd_token_perms(args: argparse.Namespace) -> int:
    """List permissions, or add or remove one, without changing the token secret."""
    db = _make_db()
    code, row = _resolve_token(db, args, multi="name matches multiple tokens; pass --id")
    if code or row is None:
        return code
    if args.add and args.remove:
        print(json.dumps({"error": "pass only one of --add or --remove"}))
        return 2
    if not args.add and not args.remove:
        print(
            json.dumps(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "workspace": row["workspace"],
                    "perms": row["perms"],
                    "revoked_at": row["revoked_at"],
                }
            )
        )
        return 0
    perm = _known_perm(args.add or args.remove)
    if perm is None:
        return 2
    current = list(row["perms"])
    if args.add:
        if perm in current:
            print(json.dumps({"error": f"token already has {perm}"}))
            return 1
        updated = sorted(set(current) | {perm})
        action = "token_perm_add"
    else:
        if perm not in current:
            print(json.dumps({"error": f"token does not have {perm}"}))
            return 1
        updated = sorted(p for p in current if p != perm)
        action = "token_perm_remove"
    db.exec("UPDATE tokens SET perms=? WHERE id=?", json.dumps(updated), row["id"])
    write_audit(
        "cli",
        action,
        token_id=row["id"],
        name=row["name"],
        workspace=row["workspace"],
        perm=perm,
        perms=updated,
    )
    print(json.dumps({"id": row["id"], "name": row["name"], "perms": updated, "perm": perm, "action": action}))
    return 0


def cmd_storage_init(args: argparse.Namespace) -> int:
    from .store import Store

    store = Store()
    limit = STORAGE_INIT_ATTEMPTS
    cause = "error"
    for attempt in range(1, limit + 1):
        # One line before the call so a hung attempt is visible. The cause stays a type name.
        print(f"storage-init: attempt {attempt}/{limit}", file=sys.stderr, flush=True)
        try:
            created = store.ensure_bucket()
        except Exception as e:  # noqa: BLE001
            cause = type(e).__name__
            if attempt == limit:
                break
            time.sleep(1)
            continue
        print(json.dumps({"bucket": store.bucket, "created": created, "versioning": "enabled", "attempts": attempt}))
        return 0
    print(json.dumps({"error": "object store not ready", "attempts": limit, "cause": cause}))
    return 1


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import run

    run()
    return 0


_stdio_dirs: list[tempfile.TemporaryDirectory[str]] = []


def _private_stdio_dir() -> Path:
    holder = tempfile.TemporaryDirectory(prefix="artifactsmith-stdio-")
    _stdio_dirs.append(holder)
    return Path(holder.name)


def cmd_stdio(args: argparse.Namespace) -> int:
    """Serve MCP on stdin/stdout for one local owner. Does not listen."""
    from . import stdio_mode
    from .config import CFG
    from .server import run_stdio

    if not os.environ.get("AM_DATA_DIR"):
        CFG.data_dir = _private_stdio_dir()
    if not os.environ.get("AM_SECRETS_DIR"):
        CFG.secrets_dir = _private_stdio_dir()
    stdio_mode.enable()
    run_stdio()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="artifactsmith",
        description="ArtifactSmith is an open source artifact server for AI agents.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("serve", help="run the API + preview server")
    sub.add_parser("stdio", help="serve MCP tools on stdin/stdout for a local client")
    sub.add_parser("storage-init", help="create the object-store bucket and enable versioning")

    tok = sub.add_parser("token", help="manage access tokens").add_subparsers(dest="token_cmd", required=True)
    add = tok.add_parser("add", help="add a token")
    add.add_argument("--name", required=True)
    add.add_argument("--workspace", required=True)
    add.add_argument("--perms", default="create,read,edit,publish,export,share,delete")
    add.add_argument("--token", default=None, help="use a specific token value instead of generating one")
    add.set_defaults(fn=cmd_token_add)
    lst = tok.add_parser("list", help="list tokens")
    lst.set_defaults(fn=cmd_token_list)
    rev = tok.add_parser("revoke", help="revoke a token by --id or --name")
    rev.add_argument("--id", default=None)
    rev.add_argument("--name", default=None)
    rev.set_defaults(fn=cmd_token_revoke)
    perms = tok.add_parser("perms", help="list, add, or remove one permission on an existing token")
    perms.add_argument("--id", default=None)
    perms.add_argument("--name", default=None)
    perms.add_argument("--add", default=None, help="grant this permission; the token value stays the same")
    perms.add_argument("--remove", default=None, help="remove this permission; the token value stays the same")
    perms.set_defaults(fn=cmd_token_perms)

    args = parser.parse_args(argv)
    if getattr(args, "cmd", None) == "token":
        fn: Callable[[argparse.Namespace], int] = args.fn
        return fn(args)
    return {"serve": cmd_serve, "stdio": cmd_stdio, "storage-init": cmd_storage_init}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
