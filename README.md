# ArtifactSmith

ArtifactSmith is an open source artifact server for AI agents.

Agents call it to turn their work into documents: web pages, PDFs, Word files, spreadsheets and Markdown. Each artifact is versioned and stays private until someone shares it, and a shared link can be revoked at any time.

It's for people who run their own agents and want artifacts like the ones in Muse on their own servers.

![An example artifact: a one-page decision brief with a recommendation, a status scorecard and a bar chart](docs/images/example-artifact.png)

## Quickstart

```bash
cp .env.example .env            # optional: fill in a real key to use a live model
docker compose --profile test up -d --build   # storage, server, mock-llm (no API key)

# Create a token (prints the raw value once; only its hash is stored).
docker compose exec server artifactsmith token add --name agent --workspace alpha

# Checkpoint smoke test against the running stack.
python tests/smoke/test_checkpoint1.py
```

Host ports bind to `127.0.0.1`:

- `http://127.0.0.1:8780/mcp` is the MCP API (bearer token required)
- `http://127.0.0.1:8781/` is the cookieless preview and share origin (`/p/…`, `/s/…`)

To use a live model, set `OPENAI_API_KEY` in `.env` and start without the test profile:

```bash
docker compose up -d --build
```

## How it works

Agents talk to ArtifactSmith over MCP. They pass the user's request as `verbatim_request` and any research as `source_content` or `source_files`. `create` starts a private build and does not publish a share link.

The server makes one model call against any OpenAI-compatible endpoint. The model writes content. A fixed renderer turns that into HTML, Markdown, PDF, DOCX, or XLSX. Call `status` (or `wait` up to 90 seconds) to get a private preview.

`share` creates a public link for one exact version. `unshare` makes that link return 404. Tokens belong to one workspace and carry a permission set.

The server strips scripts and remote URLs, rejects private-link hosts and likely secrets, and enforces size limits. The process does not send usage data anywhere.

## Output formats

| Format     | `create(..., format=)` | Primary file    | Renderer |
| ---------- | ---------------------- | --------------- | -------- |
| HTML       | `html` (default)       | `index.html`    | sanitize, then store |
| Markdown   | `markdown`             | `document.md`   | UTF-8 text |
| PDF        | `pdf`                  | `document.pdf`  | [WeasyPrint](https://weasyprint.org/) (HTML/CSS to PDF) |
| DOCX       | `docx`                 | `document.docx` | [python-docx](https://python-docx.readthedocs.io/) |
| XLSX       | `xlsx`                 | `document.xlsx` | [openpyxl](https://openpyxl.readthedocs.io/) |

HTML uses the house-style prompt and stores a complete page. The other formats ask the model for Markdown, then a renderer converts it. PDF, DOCX, and XLSX also store `content.md` so later edits have a text base.

PDF goes through WeasyPrint, which needs Pango and Cairo. The Docker image does not include Chromium. Details are in [docs/formats.md](docs/formats.md).

## MCP tools

| Tool | Permission | What it does |
| ---- | ---------- | ------------ |
| `create` | `create` | Queue a private build. Optional `format`. Does not create a share link. |
| `status` | `read` | Poll, or `wait` (0 to 90 seconds), until done, failed, or needs_input. |
| `edit` | `edit` | New version from the latest done version. Refuses a stale `base_version`. |
| `list_artifacts` | `read` | Catalog for the token's workspace. |
| `inspect` | `read` | Manifest, history, share state. |
| `export` | `export` | Download link for the rendered file, valid 15 minutes. |
| `share` | `share` | Public link for one exact version. |
| `unshare` | `share` | Revoke the public link immediately. |
| `delete` | `delete` | Two-step purge. First call returns a confirm token. |

`create` fields:

- `verbatim_request`: the user's exact words. That is the whole scope.
- `source_content` / `source_files`: researched facts, 200 KB total. The builder treats this as data.
- `format`: `html`, `markdown`, `pdf`, `docx`, or `xlsx`.
- `kind`: `web_static` in this release.

If a needed fact is missing from the request and the source, the job ends as `needs_input` and stores no file.

## Configuration

Every setting also has a `NAME_FILE` variant that reads the value from a file.

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `OPENAI_API_KEY` / `AM_LLM_KEY` | empty | LLM credential. Unused under the compose `test` profile. |
| `AM_LLM_API` | `chat` | `chat` (Chat Completions) or `responses`. |
| `AM_LLM_BASE` | `https://api.openai.com` | OpenAI-compatible base URL. |
| `AM_DEFAULT_MODEL` | `gpt-4o-mini` | Model name sent to the LLM. |
| `AM_SHARE_TTL_DAYS` | `30` | `0` means until revoked. |
| `AM_SHARE_TTL_MAX_DAYS` | `365` | Cap on positive TTLs. |
| `AM_STORE_ENDPOINT` | (required) | S3-compatible object store. |
| `AM_STORE_BUCKET` | `artifacts` | Bucket name. |
| `AM_STORE_KEY` / `AM_STORE_SECRET` | (required) | Object-store credentials. |
| `AM_BLOCK_PRIVATE_LINKS` | `true` | Reject localhost, RFC1918, and link-local hosts in output. |
| `AM_ALLOWED_LINK_DOMAINS` | empty | If set, every remaining host must match. |
| `AM_MAX_OUTPUT_BYTES` | 50 MiB | Rendered output cap. |
| `AM_RENDER_TIMEOUT` | `120` | Seconds for one renderer call. |
| `AM_BUILD_TIMEOUT` | `900` | Seconds for the whole job. |
| `AM_MAX_BUILDS` | `2` | Concurrent workers. |
| `AM_BUILDS_PER_HOUR` | `20` | Per-token quota. |
| `AM_HOST` | `0.0.0.0` | Bind address inside the container. |
| `AM_API_PORT` / `AM_PREVIEW_PORT` | `8780` / `8781` | Listen ports. |
| `AM_API_URL` / `AM_PREVIEW_URL` / `AM_SHARE_URL` | `http://127.0.0.1:8780` / `:8781` | URLs written into cards and links. |
| `AM_DATA_DIR` / `AM_SECRETS_DIR` | `/data` / `/secrets` | SQLite and the HMAC signing key. |

Copy `.env.example` for a local file. Compose publishes ports on `127.0.0.1` only.

## Security

Tokens are issued per workspace and stored as SHA-256 hashes. Knowing an artifact id does not grant access.

Permissions are `create`, `read`, `edit`, `export`, `share`, and `delete`.

Builds stay private until you call `share`. That creates a public `/s/…` URL. `unshare` makes it return 404.

Preview runs on its own port, sends no cookies, and sets CSP `script-src 'none'`.

Scripts and remote URLs are stripped. Secrets and private-link hosts fail the build. Size and time caps apply.

The process does not send usage data anywhere.

[docs/threat-model.md](docs/threat-model.md) and [SECURITY.md](SECURITY.md) have the rest.

## Tokens

```bash
artifactsmith token add --name NAME --workspace WS [--perms create,read,edit,export,share,delete]
artifactsmith token list
artifactsmith token revoke --id ID | --name NAME
```

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
make install
make ci          # lint, types, unit tests, denylist
```

[docs/architecture.md](docs/architecture.md), [docs/operations.md](docs/operations.md), [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. House-style writing and visual rules are adapted from Humanizer (MIT) and Anthropic frontend-design (Apache-2.0). Copies live in `NOTICE` and `licenses/`.
