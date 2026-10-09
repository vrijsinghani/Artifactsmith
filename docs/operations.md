# Operations

## Deploy

```bash
cp .env.example .env
# set OPENAI_API_KEY (or AM_LLM_KEY); change AM_LLM_BASE only if not using OpenAI
./scripts/ensure-local-env.sh   # fills empty AM_STORE_KEY / AM_STORE_SECRET
docker compose up -d --build
# --workspace scopes the token (artifacts in other workspaces are unreachable).
docker compose exec server artifactsmith token add --name agent --workspace alpha
```

`compose.yaml` reads LLM and object-store settings from `.env`. Every `docker compose` command needs those store keys set, including `docker compose down`. Host ports default to `127.0.0.1:8780` and `127.0.0.1:8781` via `AM_BIND_ADDRESS`. Put a TLS reverse proxy in front if you expose them past the host. Exposing the ports without TLS puts bearer tokens and artifact content on the wire in cleartext.

## Serving on your network or behind a proxy

Compose publishes API (`8780`) and preview/share (`8781`) on `AM_BIND_ADDRESS` (default `127.0.0.1`). Cards and links use `AM_API_URL`, `AM_PREVIEW_URL`, and `AM_SHARE_URL` (empty `AM_SHARE_URL` falls back to `AM_PREVIEW_URL`). MCP DNS-rebinding protection is on by default for `127.0.0.1` / `localhost` on the API port; set `AM_ALLOWED_HOSTS` / `AM_ALLOWED_ORIGINS` when clients use another hostname or a browser Origin.

### LAN access

```bash
# .env  (192.0.2.10 is documentation-range; substitute your LAN IP)
AM_BIND_ADDRESS=0.0.0.0
AM_API_URL=http://192.0.2.10:8780
AM_PREVIEW_URL=http://192.0.2.10:8781
AM_SHARE_URL=http://192.0.2.10:8781
AM_ALLOWED_HOSTS=192.0.2.10:8780,127.0.0.1:8780,localhost:8780
# Browser MCP clients also need:
# AM_ALLOWED_ORIGINS=http://192.0.2.10:8780
```

Clients open `http://192.0.2.10:8780/mcp`. Preview and share links use the LAN host, not `127.0.0.1`.

Share and preview URLs embed `AM_SHARE_URL` / `AM_PREVIEW_URL` when returned. After you change those settings for LAN serving, previously copied links still point at the old base host — call `share` again or copy the URL from `inspect` / `status`.

### Reverse proxy with TLS

Keep the bind on loopback and terminate TLS on the proxy:

```bash
# .env
AM_BIND_ADDRESS=127.0.0.1
AM_API_URL=https://artifacts.example.com
AM_PREVIEW_URL=https://preview.artifacts.example.com
AM_SHARE_URL=https://preview.artifacts.example.com
AM_ALLOWED_HOSTS=artifacts.example.com
AM_ALLOWED_ORIGINS=https://artifacts.example.com
```

Caddy:

```caddy
artifacts.example.com {
  reverse_proxy 127.0.0.1:8780
}
preview.artifacts.example.com {
  reverse_proxy 127.0.0.1:8781
}
```

nginx (API host):

```nginx
server {
  listen 443 ssl;
  server_name artifacts.example.com;
  location / {
    proxy_pass http://127.0.0.1:8780;
    proxy_set_header Host $host;
    proxy_set_header Authorization $http_authorization;
    proxy_read_timeout 120s;
    proxy_send_timeout 120s;
  }
}
```

With `AM_ALLOWED_HOSTS` limited to the public hostname, `curl http://127.0.0.1:8780/mcp` returns HTTP 421. Use the proxied URL. Browser-based MCP clients need `AM_ALLOWED_ORIGINS` set; an empty list rejects any request that carries an Origin header.

### MCP client on another machine

```text
URL:    {AM_API_URL}/mcp
Header: Authorization: Bearer <token from artifactsmith token add>
```

## Backups

Copy these together:

1. SQLite at `AM_DATA_DIR/state.sqlite`, plus `-wal` and `-shm` if they exist. Stop writers, or use `sqlite3 .backup`, so the copy is consistent. Preview (`/p/`) and share (`/s/`) ids live here.
2. The `AM_STORE_BUCKET` contents, including object versions.
3. `AM_SECRETS_DIR/signing.key`. Losing the key invalidates outstanding `/dl/` download links only. Preview and share links use stored ids and keep working until expiry, `revoke_previews`, or `unshare`.

To restore: stop the server, replace the data dir and secrets, restore the bucket, start the server. Done versions are not rebuilt.

## Token rotation

```bash
artifactsmith token add --name agent-next --workspace alpha --perms create,read,edit,export,share,delete
# point clients at the new token
artifactsmith token revoke --name agent
```

A revoked hash fails the next request. There is no grace window. Revoking a token does not expire already-issued `/p/` preview links; call `revoke_previews` for that.

Private `/p/` preview URLs are database-backed capabilities (short ids in SQLite), not HMAC-signed. Replacing `AM_SECRETS_DIR/signing.key` invalidates `/dl/` download links only. `/p/` links stay usable until their expiry unless you call `revoke_previews(artifact_id)` (or delete the artifact). Public `/s/` links are also not HMAC-signed; they stay valid until `unshare` or TTL.

Separate API and preview ports create separate browser origins. The preview app sets no cookies; that does not stop a browser from sending cookies that already exist for the host.

## Upgrades

1. Read `CHANGELOG.md`.
2. Rebuild the image: `docker compose up -d --build`.
3. SQLite schema uses `CREATE TABLE IF NOT EXISTS` plus a small column migrator (`deleting_at` on artifacts). Do not assume every future column add is automatic.
4. Run `python -m tests.e2e.test_end_to_end` against a staging stack before moving clients.

The compose storage image is `rustfs/rustfs:1.0.1`, pinned by digest.

## Uninstall / reset

```bash
docker compose down --volumes --rmi all
docker image rm artifactsmith-server 2>/dev/null || true
```

That removes the containers and named volumes (SQLite, object store, secrets). Recreate `.env` from `.env.example` and follow Deploy above to start clean.

## Health and logs

`GET /healthz` works on both origins.

Server logs go to process stdout (uvicorn and `logging`).

Audit lines append to `AM_DATA_DIR/audit.jsonl` with actor, action, and artifact id. That file stays on disk. The process does not send it anywhere.

## Capacity

`AM_MAX_BUILDS` is in-process concurrency. Run one server process per data directory (SQLite is single-writer). Do not share the same SQLite file or secrets volume across processes. External metadata stores are not part of this release. `AM_BUILDS_PER_HOUR` is per token.

`artifactsmith serve` binds `AM_HOST` (default `127.0.0.1`). The compose image sets `AM_HOST=0.0.0.0` inside the container; host publish still uses `AM_BIND_ADDRESS` (default loopback). On restart, queued and building jobs are requeued; claim stays atomic.
