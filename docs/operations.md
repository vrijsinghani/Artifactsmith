# Operations

## Deploy

```bash
cp .env.example .env
# set OPENAI_API_KEY (or AM_LLM_KEY), AM_LLM_BASE, and AM_DEFAULT_MODEL
./scripts/ensure-local-env.sh   # fills empty AM_STORE_KEY / AM_STORE_SECRET
docker compose up -d --build
docker compose exec server artifactsmith token add --name agent --workspace alpha
```

`compose.yaml` reads LLM and object-store settings from `.env`. It does not ship credentials and does not start a stand-in model. Host ports default to `127.0.0.1:8780` and `127.0.0.1:8781`. Put a TLS reverse proxy in front if you expose them past the host.

## Backups

Copy these together:

1. SQLite at `AM_DATA_DIR/state.sqlite`, plus `-wal` and `-shm` if they exist. Stop writers, or use `sqlite3 .backup`, so the copy is consistent.
2. The `AM_STORE_BUCKET` contents, including object versions.
3. `AM_SECRETS_DIR/signing.key`. Losing the key invalidates outstanding preview and download links. Share links use stored ids and keep working.

To restore: stop the server, replace the data dir and secrets, restore the bucket, start the server. Done versions are not rebuilt.

## Token rotation

```bash
artifactsmith token add --name agent-next --workspace alpha --perms create,read,edit,export,share,delete
# point clients at the new token
artifactsmith token revoke --name agent
```

A revoked hash fails the next request. There is no grace window.

If the signing key may be leaked, replace `AM_SECRETS_DIR/signing.key` and restart. Existing `/p/` and `/dl/` links stop working. Call `status` or `export` for new preview and download URLs. Public `/s/` links are not HMAC-signed. They stay valid until `unshare` or TTL.

## Upgrades

1. Read `CHANGELOG.md`.
2. Rebuild the image: `docker compose up -d --build`.
3. SQLite schema is created with `CREATE TABLE IF NOT EXISTS` only. This release has no migrator. Do not assume automatic column adds.
4. Run `python tests/e2e/test_end_to_end.py` against a staging stack before moving clients.

The compose storage image is `rustfs/rustfs:1.0.1`. Pin a digest if you rebuild the stack for production.

## Health and logs

`GET /healthz` works on both origins.

Server logs go to process stdout (uvicorn and `logging`).

Audit lines append to `AM_DATA_DIR/audit.jsonl` with actor, action, and artifact id. That file stays on disk. The process does not send it anywhere.

## Capacity

`AM_MAX_BUILDS` is in-process concurrency. Run one server per machine (SQLite is single-writer), or move metadata to an external store, which this release does not include. `AM_BUILDS_PER_HOUR` is per token.
