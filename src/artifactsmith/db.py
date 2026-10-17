"""SQLite source of truth. Single-writer lock, WAL mode. Bytes live in the object store."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import CFG

SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY,
  workspace TEXT NOT NULL,
  slug TEXT NOT NULL,
  display_name TEXT NOT NULL,
  kind TEXT NOT NULL,
  format TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  deleting_at REAL,
  UNIQUE(workspace, slug)
);
CREATE TABLE IF NOT EXISTS versions (
  artifact_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  base_version INTEGER,
  verbatim_request TEXT NOT NULL,
  status TEXT NOT NULL,
  job_id TEXT NOT NULL,
  model TEXT,
  sha256 TEXT,
  primary_file TEXT,
  files_json TEXT,
  assumptions_json TEXT,
  summary TEXT,
  size INTEGER,
  source_key TEXT,
  publish_json TEXT,
  style TEXT,
  mode TEXT,
  format TEXT,
  note TEXT,
  requested_name TEXT,
  created_by TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY (artifact_id, version)
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  artifact_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  client TEXT NOT NULL,
  status TEXT NOT NULL,
  progress TEXT,
  error TEXT,
  created_at REAL NOT NULL,
  started_at REAL,
  finished_at REAL
);
CREATE TABLE IF NOT EXISTS idempotency (
  client TEXT NOT NULL,
  key TEXT NOT NULL,
  result_json TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY (client, key)
);
CREATE TABLE IF NOT EXISTS shares (
  id TEXT PRIMARY KEY,
  artifact_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  revoked_at REAL,
  pinned INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS delete_tokens (
  token TEXT PRIMARY KEY,
  artifact_id TEXT NOT NULL,
  client TEXT NOT NULL,
  expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS short_links (
  id TEXT PRIMARY KEY,
  artifact_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  created_by TEXT NOT NULL,
  expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
  id TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  workspace TEXT NOT NULL,
  perms TEXT NOT NULL,
  created_at REAL NOT NULL,
  revoked_at REAL
);
CREATE INDEX IF NOT EXISTS jobs_client_time ON jobs(client, created_at);
CREATE INDEX IF NOT EXISTS versions_artifact ON versions(artifact_id, version);
CREATE TABLE IF NOT EXISTS web_sessions (
  id_hash TEXT PRIMARY KEY,
  token_id TEXT NOT NULL,
  csrf TEXT NOT NULL,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS web_sessions_token ON web_sessions(token_id);
CREATE TABLE IF NOT EXISTS login_attempts (
  ip TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'ip',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS login_attempts_ip ON login_attempts(ip, created_at);
CREATE TABLE IF NOT EXISTS trial_links (
  code_hash TEXT PRIMARY KEY,
  email_hash TEXT NOT NULL,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  used_at REAL
);
CREATE TABLE IF NOT EXISTS trial_signups (
  email_hash TEXT PRIMARY KEY,
  token_id TEXT NOT NULL,
  created_at REAL NOT NULL,
  verified INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS trial_signup_ips (
  ip TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS trial_signup_ips_ip ON trial_signup_ips(ip, created_at);
CREATE TABLE IF NOT EXISTS trial_reservations (
  job_id TEXT PRIMARY KEY,
  token_id TEXT,
  day TEXT NOT NULL,
  reserved_micros INTEGER NOT NULL,
  actual_micros INTEGER,
  settled_at REAL,
  created_at REAL,
  counts_build INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS trial_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL,
  model TEXT NOT NULL,
  input_tokens INTEGER NOT NULL,
  output_tokens INTEGER NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS trial_usage_job ON trial_usage(job_id);
"""


class DB:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.lock = threading.RLock()
        self._tx_owner: int | None = None

    def _migrate(self) -> None:
        art_cols = {str(r[1]) for r in self.conn.execute("PRAGMA table_info(artifacts)").fetchall()}
        if "deleting_at" not in art_cols:
            self.conn.execute("ALTER TABLE artifacts ADD COLUMN deleting_at REAL")
        ver_cols = {str(r[1]) for r in self.conn.execute("PRAGMA table_info(versions)").fetchall()}
        if "style" not in ver_cols:
            self.conn.execute("ALTER TABLE versions ADD COLUMN style TEXT")
        if "publish_json" not in ver_cols:
            self.conn.execute("ALTER TABLE versions ADD COLUMN publish_json TEXT")
        if "mode" not in ver_cols:
            self.conn.execute("ALTER TABLE versions ADD COLUMN mode TEXT")
        if "format" not in ver_cols:
            self.conn.execute("ALTER TABLE versions ADD COLUMN format TEXT")
        if "note" not in ver_cols:
            self.conn.execute("ALTER TABLE versions ADD COLUMN note TEXT")
        if "requested_name" not in ver_cols:
            self.conn.execute("ALTER TABLE versions ADD COLUMN requested_name TEXT")
        share_cols = {str(r[1]) for r in self.conn.execute("PRAGMA table_info(shares)").fetchall()}
        if "pinned" not in share_cols:
            self.conn.execute("ALTER TABLE shares ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
        attempt_cols = {str(r[1]) for r in self.conn.execute("PRAGMA table_info(login_attempts)").fetchall()}
        if attempt_cols and "kind" not in attempt_cols:
            self.conn.execute("ALTER TABLE login_attempts ADD COLUMN kind TEXT NOT NULL DEFAULT 'ip'")
        if attempt_cols:
            self.conn.execute("CREATE INDEX IF NOT EXISTS login_attempts_kind ON login_attempts(kind, ip, created_at)")
        token_cols = {str(r[1]) for r in self.conn.execute("PRAGMA table_info(tokens)").fetchall()}
        if "kind" not in token_cols:
            self.conn.execute("ALTER TABLE tokens ADD COLUMN kind TEXT NOT NULL DEFAULT 'internal'")
        if "email_hash" not in token_cols:
            self.conn.execute("ALTER TABLE tokens ADD COLUMN email_hash TEXT")
        hold_cols = {str(r[1]) for r in self.conn.execute("PRAGMA table_info(trial_reservations)").fetchall()}
        if hold_cols and "token_id" not in hold_cols:
            self.conn.execute("ALTER TABLE trial_reservations ADD COLUMN token_id TEXT")
        if hold_cols and "created_at" not in hold_cols:
            self.conn.execute("ALTER TABLE trial_reservations ADD COLUMN created_at REAL")
        if hold_cols and "counts_build" not in hold_cols:
            self.conn.execute("ALTER TABLE trial_reservations ADD COLUMN counts_build INTEGER NOT NULL DEFAULT 1")
            self._waive_stored_needs_input()
        signup_cols = {str(r[1]) for r in self.conn.execute("PRAGMA table_info(trial_signups)").fetchall()}
        if signup_cols and "verified" not in signup_cols:
            self.conn.execute("ALTER TABLE trial_signups ADD COLUMN verified INTEGER NOT NULL DEFAULT 1")
        if hold_cols:
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS trial_reservations_token ON trial_reservations(token_id, created_at)"
            )
        # Format was fixed on the artifact, so that value is each existing version's format.
        self.conn.execute(
            "UPDATE versions SET format = ("
            "SELECT artifacts.format FROM artifacts WHERE artifacts.id = versions.artifact_id"
            ") WHERE format IS NULL"
        )

    def _waive_stored_needs_input(self) -> None:
        """Waive at most AM_TRIAL_FREE_NEEDS_INPUT existing needs_input rows per token, oldest first."""
        self.conn.execute(
            "UPDATE trial_reservations SET counts_build=0 WHERE job_id IN ("
            "SELECT job_id FROM ("
            "SELECT r.job_id AS job_id, ROW_NUMBER() OVER ("
            "PARTITION BY r.token_id ORDER BY COALESCE(r.created_at, j.created_at), r.job_id"
            ") AS rn "
            "FROM trial_reservations r JOIN jobs j ON j.id = r.job_id "
            "WHERE j.status='needs_input') ranked WHERE rn <= ?)",
            (int(CFG.trial_free_needs_input),),
        )

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Write transaction. A nested call on this thread joins the open one."""
        with self.lock:
            if self._tx_owner == threading.get_ident():
                yield self.conn
                return
            self.conn.execute("BEGIN IMMEDIATE")
            self._tx_owner = threading.get_ident()
            try:
                yield self.conn
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            finally:
                self._tx_owner = None

    def one(self, sql: str, *args: Any) -> dict[str, Any] | None:
        with self.lock:
            r = self.conn.execute(sql, args).fetchone()
        return dict(r) if r else None

    def must(self, sql: str, *args: Any) -> dict[str, Any]:
        row = self.one(sql, *args)
        if row is None:
            raise RuntimeError(f"expected a row for {sql!r}")
        return row

    def all(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def exec(self, sql: str, *args: Any) -> None:
        with self.tx() as c:
            c.execute(sql, args)

    def close(self) -> None:
        with self.lock:
            self.conn.close()


def jloads(s: str | None, default: Any = None) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default
