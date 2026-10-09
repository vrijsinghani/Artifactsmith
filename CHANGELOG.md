# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Version numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Removed the canned Chat Completions stand-in from the shipped package, the CLI, `compose.yaml`, and the server image. Tests use `tests/support/` and `compose.test.yaml`.
- `compose.yaml` reads `AM_LLM_BASE`, `AM_LLM_KEY` or `OPENAI_API_KEY`, `AM_DEFAULT_MODEL`, and object-store credentials from `.env`. `./scripts/ensure-local-env.sh` fills empty store keys.
- Storage image pinned to `rustfs/rustfs:1.0.1`.
- End-to-end smoke lives at `tests/e2e/test_end_to_end.py`.
- Coverage gate is 80%. Core modules type-check without mypy relaxations.

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

[Unreleased]: https://github.com/vrijsinghani/artifactsmith/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/vrijsinghani/artifactsmith/releases/tag/v0.1.0
