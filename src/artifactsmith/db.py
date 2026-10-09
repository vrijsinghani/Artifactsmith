"""SQLite source of truth. Single-writer lock, WAL mode. Bytes live in the object store."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

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
  revoked_at REAL
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
"""


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.lock = threading.RLock()

    @contextmanager
    def tx(self):
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise

    def one(self, sql: str, *args) -> dict | None:
        with self.lock:
            r = self.conn.execute(sql, args).fetchone()
        return dict(r) if r else None

    def all(self, sql: str, *args) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def exec(self, sql: str, *args) -> None:
        with self.tx() as c:
            c.execute(sql, args)


def jloads(s: str | None, default=None):
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default
