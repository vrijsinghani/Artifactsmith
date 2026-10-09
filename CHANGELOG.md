# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Version numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Removed

- The canned Chat Completions endpoint from the shipped package, the CLI, `compose.yaml`, and the server image. Tests keep a fake chat model under `tests/support/` started by `compose.test.yaml`.

### Changed

- `compose.yaml` restores `env_file: .env` so documented settings (`AM_SHARE_URL`, limits, `*_FILE`) reach the server.
- `compose.yaml` reads LLM and object-store credentials from `.env`. `./scripts/ensure-local-env.sh` fills empty store keys without corrupting a final line that lacks a trailing newline.
- Storage image pinned to `rustfs/rustfs:1.0.1` by digest.
- `AM_LLM_BASE` accepts a host root with or without a trailing `/v1`.
- HTML exports use an nh3 allowlist sanitizer; XLSX cells are written as literals; WeasyPrint uses a deny-all URL fetcher.
- Renderers run in killable subprocesses; delete is serialized against new builds; job enqueue is thread-safe.
- Invalid integer/boolean env settings fail closed. `revoke_previews` expires private `/p/` links.
- Unit tests run on Python 3.11 and 3.12. Type checking uses `mypy --strict`.

### Security

- Configurable MCP Host/Origin allow-lists via `AM_ALLOWED_HOSTS` / `AM_ALLOWED_ORIGINS`.

## [0.1.0] - 2026-10-09

### Added

- MCP server tools: create, status, edit, inspect, export, share, unshare, delete.
- Workspace-scoped hashed tokens with permission sets.
- HTML house-style builder. Attribution for Humanizer and frontend-design is in `NOTICE`.
- Renderers for Markdown, PDF (WeasyPrint), DOCX (python-docx), and XLSX (openpyxl).
- Shared output checks: script and URL strip, private-link and secret rejection, size and time caps.
- Compose stack with an internal object store.
- GitHub Actions: ruff, mypy, pytest with coverage, denylist, Docker build, pip-audit, Trivy, compose smokes.
- Docs: architecture, threat model, operations, formats.
- CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, issue and PR templates, Makefile, pre-commit.

### Security

- Preview origin sends no cookies and sets `script-src 'none'`.
- Compose publishes API and preview on `127.0.0.1` only.
- The process does not send usage data anywhere.

[Unreleased]: https://github.com/vrijsinghani/Artifactsmith/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/vrijsinghani/Artifactsmith/releases/tag/v0.1.0
