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

Production layers `compose.prod.yaml` on `compose.yaml`:

```bash
docker compose --env-file .env -f compose.yaml -f compose.prod.yaml up -d --build
```

Secrets, hostnames, and bind addresses come from `.env`. `compose.prod.yaml` has no values of its own. The live production copy of that file was not available when this overlay was added; if the running file has more than the server restart policy, replace the committed file with a sanitized copy (still no secret values).

### Rollback past the floating-share release

`8dc0bfe` adds `pinned` to `shares`. The previous image inserts with `INSERT INTO shares VALUES (?,?,?,?,?,?,?,NULL)`. SQLite rejects that short positional insert once the column exists, and changing the current named insert does not change the image you roll back to. Stop the server, drop the column, then start the previous image:

```bash
docker compose --env-file .env -f compose.yaml -f compose.prod.yaml run --rm --no-deps server \
  python -c 'import sqlite3; c=sqlite3.connect("/data/state.sqlite"); c.execute("ALTER TABLE shares DROP COLUMN pinned"); c.commit()'
```

The database is `/data/state.sqlite` (`AM_DATA_DIR`, default `/data`). Run the `DROP COLUMN` before the previous image serves traffic. SQLite 3.35 or newer is required.

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

## Glama

Glama's inspector builds with `uv sync` and then launches the server through `mcp-proxy`. Use Python 3.12 or 3.13 so the `brotli` wheel installs. On an interpreter with no wheel, install `build-essential` before `uv sync`. Python 3.15 has no `brotli` 1.2.0 wheel; without a compiler that `uv sync` still fails.

Build step: `uv sync`

Command: `mcp-proxy -- uv run artifactsmith stdio`

```json
["mcp-proxy", "--", "uv", "run", "artifactsmith", "stdio"]
```

`mcp-proxy` is the process that listens. `artifactsmith stdio` is the child. It does not bind a port. Do not add `--port`.

Dummy environment, and nothing else:

```json
{
  "AM_STORE_ENDPOINT": "https://s3.example.com",
  "AM_STORE_KEY": "dummy",
  "AM_STORE_SECRET": "dummy",
  "OPENAI_API_KEY": "sk-dummy"
}
```

Leave `AM_DATA_DIR` and `AM_SECRETS_DIR` unset. The command then uses a private temp directory. Stdio is the owner of whatever data directory it is given, and only of workspace `local`. Do not point it at the production data or secrets directory: a share in that shared directory is a public URL, and export can mint a download URL when it can write the secrets directory. Store and model calls return an MCP error and do not open a socket.

## Backups

Copy these together:

1. SQLite at `AM_DATA_DIR/state.sqlite`, plus `-wal` and `-shm` if they exist. Stop writers, or use `sqlite3 .backup`, so the copy is consistent. Preview (`/p/`) and share (`/s/`) ids live here.
2. The `AM_STORE_BUCKET` contents, including object versions.
3. `AM_SECRETS_DIR/signing.key`. Losing the key invalidates outstanding `/dl/` download links only. Preview and share links use stored ids and keep working until expiry, `revoke_previews`, or `unshare`.

To restore: stop the server, replace the data dir and secrets, restore the bucket, start the server. Done versions are not rebuilt.

## Token rotation

```bash
artifactsmith token add --name agent-next --workspace alpha --perms create,read,edit,publish,export,share,delete
# point clients at the new token
artifactsmith token revoke --name agent
```

To grant or remove one permission without reissuing the token:

```bash
artifactsmith token perms --name agent
artifactsmith token perms --name agent --add publish
artifactsmith token perms --name agent --remove publish
```

The token value does not change. Each add or remove appends one audit line (`token_perm_add` or `token_perm_remove`) with the token id, name, workspace, permission, and the resulting set. Listing permissions does not. If `--name` matches more than one token, pass `--id`.

A revoked hash fails the next API call and the next My pages request. There is no grace window. Sign-out deletes that browser session. Revoking a token does not expire already-issued `/p/` preview links; call `revoke_previews` for that.

My pages is `http://127.0.0.1:8780/app/` (the API origin). Paste the token from `token add`. The browser stores an HttpOnly SameSite=Strict cookie on path `/app` and the address bar never contains the token. Set `AM_API_URL` to `https://…` so the cookie is Secure, or set `AM_COOKIE_SECURE=true` when TLS terminates at a proxy. Failed sign-ins are limited per IPv4 address or IPv6 `/64` (`AM_LOGIN_LIMIT`, `AM_LOGIN_WINDOW_S`) and per hashed token prefix (`AM_LOGIN_PREFIX_LIMIT`). An IPv4-mapped IPv6 address counts as that IPv4 address. The address is the TCP peer unless that peer is listed in `AM_TRUSTED_PROXIES` (comma-separated IPs or CIDRs). A trusted peer may name the browser with `X-Real-IP`, or with the rightmost `X-Forwarded-For` hop. For the SWAG proxy on this network set `AM_TRUSTED_PROXIES=192.168.30.160`. Leave the setting empty anywhere else so a client cannot pick its own limit bucket. The catalog pages 50 at a time. Times are America/New_York unless the viewer sets a zone; the label names the zone. The preview port stays cookieless.

Private `/p/` preview URLs are database-backed capabilities (short ids in SQLite), not HMAC-signed. Replacing `AM_SECRETS_DIR/signing.key` invalidates `/dl/` download links only. `/p/` links stay usable until their expiry unless you call `revoke_previews(artifact_id)` (or delete the artifact). Public `/s/` links are also not HMAC-signed; they stay valid until `unshare` or TTL.

Separate API and preview ports create separate browser origins. The preview app sets no cookies; that does not stop a browser from sending cookies that already exist for the host.

## Upgrades

1. Read `CHANGELOG.md`.
2. Rebuild the image: `docker compose up -d --build`.
3. SQLite schema uses `CREATE TABLE IF NOT EXISTS` plus a small column migrator (`deleting_at` on artifacts, `kind` on `login_attempts`). Do not assume every future column add is automatic.
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

`AM_MAX_BUILDS` is in-process concurrency. Workers take internal jobs first, and at most one hosted-trial job runs at a time, so trial traffic leaves a slot for internal tokens when `AM_MAX_BUILDS` is 2. Run one server process per data directory (SQLite is single-writer). Do not share the same SQLite file or secrets volume across processes. External metadata stores are not part of this release. `AM_BUILDS_PER_HOUR` is per internal token (default 20). Trial tokens use `AM_TRIAL_BUILDS_PER_HOUR` (default 10) and do not change the internal limit.

`AM_TRIAL_ENABLED` defaults to false. While it is false, `/try` says the hosted copy is closed and a build fails with that same sentence. `GET /trial` redirects to `/try`. `AM_TRIAL_EMAIL_VERIFY` defaults to true. Set it to false to issue the token on submit without mail; the sign-up row is then `verified=0`. The per-IP limit, build quotas, daily spend cap, and kill switch stay the same. `AM_TRIAL_SIGNUPS_PER_DAY` (default 300) is how many trials can be minted per UTC day. Once it is reached, `/try` says no more hosted tokens today, back at 00:00 UTC. Sign-up attempts share an IPv6 `/48`. My pages login stays a `/64`. Both use `AM_TRUSTED_PROXIES` the same way. The daily spend cap (`AM_TRIAL_DAILY_USD`, default 10) counts only trial holds. Each trial build reserves its worst-case cost before it runs and replaces that hold with the measured token cost when it finishes. Up to AM_TRIAL_FREE_NEEDS_INPUT (default 5) needs_input jobs per token do not use a build; after that they do. The reservation stays, so the hourly limit and the daily spend cap still include it. A failed job still uses one build. Prices are `AM_TRIAL_PRICE_IN` and `AM_TRIAL_PRICE_OUT` (USD per 1M tokens). Magic-link mail needs `AM_SMTP_HOST` and `AM_SMTP_FROM`. The `Message-ID` domain is the domain of `AM_SMTP_FROM`. Set `AM_API_URL` to the public origin so the link and the client snippet do not point at loopback. Served trial HTML adds `noindex` even when the page already has a robots meta that does not say noindex. A trial token's tool list omits `publish`. Idle trial workspaces are deleted `AM_TRIAL_RETENTION_DAYS` after their last build (default 30).

`artifactsmith serve` binds `AM_HOST` (default `127.0.0.1`). The compose image sets `AM_HOST=0.0.0.0` inside the container; host publish still uses `AM_BIND_ADDRESS` (default loopback). On restart, queued and building jobs are requeued; claim stays atomic.
