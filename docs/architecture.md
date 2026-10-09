# Architecture

When you run ArtifactSmith you get two HTTP ports, a SQLite database, an object store, and a background worker. Agents connect over MCP. People open previews and share links in a browser.

```
your agent  --MCP/HTTP-->  API :8780 (bearer)  --> SQLite (metadata)
                                               --> object store (bytes)
                                               --> LLM (text only)
your browser            Preview :8781 (no cookies) --> object store
```

## Process

`artifactsmith serve` starts the MCP API (FastMCP streamable HTTP) behind bearer auth, a cookieless preview and share app on a second port, and `AM_MAX_BUILDS` worker tasks that pull jobs from an in-process queue.

SQLite holds artifacts, versions, jobs, tokens, and shares. The S3-compatible object store holds rendered files and the input `source.json`. On restart, unfinished jobs go back on the queue. Done versions stay as they are.

## Build pipeline

`create` and `edit` check the caller, write a `queued` version, and return immediately. The worker calls the LLM once and retries once if checks fail. The model returns marked-up text (`===FILE: …===`). The server parses that text and does not execute it.

Safety checks look for secrets, private hosts, allow-list misses, scripts, remote URLs, and size. The renderer for `format` then writes bytes. Files go to the object store and the version becomes `done`.

HTML uses the house-style system prompt and stores `index.html`. Markdown, PDF, DOCX, and XLSX use a content prompt. The model writes Markdown and the renderer converts it. PDF, DOCX, and XLSX also keep `content.md` so `edit` has a text base.

## Packages

| Module | Role |
| ------ | ---- |
| `server` | MCP tools, bearer auth, preview routes |
| `service` | Create, edit, share, delete, worker, cards |
| `builder` | Prompt, parse, sanitize, dispatch renderer |
| `renderers` | Format interface plus HTML, Markdown, PDF, DOCX, XLSX |
| `renderers.safety` | Shared checks for every format |
| `llm` | Chat Completions or Responses adapter |
| `db` / `store` / `config` / `cli` | Persistence, settings, operator tools |

A fake chat model for local and CI end-to-end tests lives under `tests/support/` and is started only by `compose.test.yaml`.

## Trust boundaries

The API origin authenticates every call except `/healthz`.

The preview origin serves bytes named by a signed short link or an unrevoked share id.

Compose keeps the object store on the internal network and does not publish its ports on the host.

Model output is treated as data. Renderers do not fetch URLs and do not run scripts.
