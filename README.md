# ArtifactSmith

A self-hosted MCP server. An agent sends the user's exact words plus research; one model call drafts content;
fixed renderers produce a versioned, self-contained page. `create` builds privately and never publishes. Waiting
returns a private preview. `share` is a separate, explicit call. `unshare` revokes immediately.

## Quickstart

```bash
cp .env.example .env            # optional: fill in a real key to use a live model
docker compose --profile test up -d --build   # storage, storage-init, server, mock-llm (no API key needed)

# Create a token for a workspace (prints the raw token once; only its hash is stored).
docker compose exec server artifactsmith token add --name agent --workspace alpha

# Run the end-to-end checkpoint smoke test against the running stack.
python tests/smoke/test_checkpoint1.py
```

Endpoints (bound to 127.0.0.1 on the host):
- `http://127.0.0.1:8780/mcp` — MCP API (bearer token required)
- `http://127.0.0.1:8781/` — cookieless preview and public share origin (`/p/…`, `/s/…`)

## Tokens and permissions

```bash
artifactsmith token add --name NAME --workspace WS [--perms create,read,edit,export,share,delete] [--token VALUE]
artifactsmith token list
artifactsmith token revoke --id ID | --name NAME
```

A token is scoped to one workspace and carries an explicit permission set. A valid token for workspace `A` gets
nothing from workspace `B` even if it knows the artifact id.

## Layout

`src/artifactsmith/` (package), `tests/smoke/` (checkpoint test), `docker/Dockerfile`, `compose.yaml`,
`scripts/denylist.sh`. The `test` compose profile adds `mock-llm`, a canned chat-completions service so the flow
runs with no API key.

## Notes

- No generated JavaScript in output; pages are served with `script-src 'none'`.
- SQLite is the source of truth; the object store holds bytes. Builds run through a job queue and a worker.
- `AM_SHARE_TTL_DAYS=0` means links live until revoked (reported as "until revoked").
