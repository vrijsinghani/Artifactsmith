# Checkpoint 1 (this is the only gate for this session)

Build a local git repo that passes this flow with `docker compose --profile test`. Do not publish, push, or create a GitHub repo.

## Product
Self-hosted MCP server. An agent sends the user's exact words plus research. One model call drafts content. Fixed renderers produce a versioned page. `create` starts the build and never publishes. Waiting returns a verified private preview. `share` is a separate call. `unshare` revokes immediately.

## Flow the test must run, end to end, against compose
1. A real MCP client (the Python client in `examples/python_client.py` or `tests/smoke/`) calls `create` with `verbatim_request` and `source_content` that contains a unique fact, for example the sentence "The pilot store code is HARBOR-17."
2. `status(wait=...)` returns done. The rendered page contains HARBOR-17. There is no public `/s/` link yet. `curl` of any share URL is 404.
3. `edit` from that version changes one line. The new page contains the new fact and is still private.
4. `share` returns a public URL. `curl` that URL returns 200 and the page.
5. `unshare` makes that URL 404.
6. Restart the server container. The artifact is still there (`inspect` works, no rebuild).
7. A second token that is valid but lacks rights, or an unauthenticated call that only knows the artifact id, cannot read, edit, export, share, or delete. HTTP 401 or a structured error. Guessing the id is not access.
8. `create` whose request needs a fact that is not in `source_content` returns status `needs_input` and writes no done page.

## v0.1 output rules
- No generated JavaScript. CSP uses `script-src 'none'`.
- No remote resources (no http(s) images, scripts, fonts, or fetches in the page).
- Enforce an output size cap, a renderer memory cap, and a render time cap.
- `source_content`, `source_files`, and `needs_input` are in the MCP tool schema and in `examples/`.

## Share lifetime
- Default `AM_SHARE_TTL_DAYS=30`.
- `AM_SHARE_TTL_DAYS=0` means the link lasts until revoked (store a far-future expiry so queries keep working, and report lifetime "until revoked").
- Positive values are capped by `AM_SHARE_TTL_MAX_DAYS` (default 365).

## Permissions
Token permissions cover ownership, workspace access, edit, export, share, and delete. Not just "may this client call the tool."

## What to port
`reference/` is the private server this product is ported from. Read it. Do not copy it verbatim.
- Drop the chat-approval integration and any personal hostnames, names, or IPs.
- LLM adapter: Chat Completions by default (`AM_LLM_API=chat`), Responses optional.
- Tokens live hashed in SQLite. CLI: `artifactsmith token add|list|revoke`.
- Config from env, each with a `NAME_FILE` variant.
- Signing key generated on first boot.
- Link allow-list is `AM_ALLOWED_LINK_DOMAINS` (empty default). Block private-host links when `AM_BLOCK_PRIVATE_LINKS=true`.
- Package name `artifactsmith`, import package `artifactsmith`, CLI `artifactsmith`.

## Compose (`compose.yaml`)
- `server`: build the Dockerfile, ports bound to 127.0.0.1:8780 and 127.0.0.1:8781, healthcheck `GET /healthz`.
- `storage`: `rustfs/rustfs:latest`, internal only. Credentials generated, not the well-known admin pair.
- `storage-init`: one-shot, creates bucket `artifacts` and enables versioning.
- `mock-llm` under profile `test`: canned OpenAI chat-completions replies so the flow runs with no API key. The canned reply must include the supplied source facts in the HTML and must not invent a page when the user message says facts are missing (return a marker the server turns into `needs_input`, or have the server detect a missing-fact sentinel).
- `caddy` under profile `public` is optional for this checkpoint. If present, it proxies only GET/HEAD `/s/*`.

`docker compose --profile test up -d` must be enough. `.env.example` has `OPENAI_API_KEY=` and comments. The test profile must not need a real key.

## Layout
Follow a normal Python src layout: `src/artifactsmith/`, `tests/`, `docker/Dockerfile`, `compose.yaml`, `pyproject.toml`, `README.md` with the quickstart only (no announcement text). MIT LICENSE.

## Denylist
`reference/` is gitignored and must stay untracked. Do not copy tokens, keys, personal names, or private hostnames out of it. Tracked files may mention localhost and the default OpenAI host only. Add `scripts/denylist.sh` that scans tracked files for private-looking hosts and personal names (build the pattern from fragments so the script does not itself contain a forbidden string) and exits 1 on a hit. Run it and keep it green.

## Done means
Write `tests/smoke/test_checkpoint1.py` (pytest, or a shell script `tests/smoke/checkpoint1.sh` if pytest against compose is awkward) that performs the eight steps above and exits 0. Run it. Leave the stack up only if the test passed. Commit locally. Do not push.
