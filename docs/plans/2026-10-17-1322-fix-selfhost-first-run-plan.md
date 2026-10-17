# Self-host first run - Plan

**Date:** 2026-10-17
**Branch:** `cursor/selfhost-first-run-9550`
**Base:** `main` (`df2bb12`)

## Goal

A stranger with Docker and a model key can follow the README from clone to a shared page. `storage-init` stops within 120 seconds and says which endpoint failed. `create` with no OpenAI key fails in `status.error` before an HTTP 401. Stdio install and its store/model limit are written down.

## Decisions

- The server image keeps `HEALTHCHECK` for `artifactsmith serve`. Compose sets `healthcheck.disable: true` on `storage-init` only.
- The init loop uses a 120-second wall clock. Progress goes to stderr. Failure names `AM_STORE_ENDPOINT` and exits 1. Success JSON stays on stdout.
- An empty key fails the job only when the model host is `api.openai.com`, which is the case that returns HTTP 401. A local base with an empty key still runs. `publish` does not take this path.
- `status.error` is exactly `No model key set: put OPENAI_API_KEY in .env (see README)`. Startup logs the same sentence.
- Docs stay factual. The GitHub URL is the first line of Quickstart. The word trial is not added.

## Work

1. README Quickstart: prerequisites, clone through token, MCP snippet, Python `tools/list` + `create` + `status` + `share`, `publish` with no key, storage-init troubleshooting, stdio install and Claude Desktop/Code config.
2. `compose.yaml` healthcheck disable. `cmd_storage_init` timeout and stderr.
3. Fail the non-publish job in `_run_job` before the model call.
4. Tests for the timeout message and the key error. A local base and `publish` still proceed.
