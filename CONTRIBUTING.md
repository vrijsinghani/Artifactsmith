# Contributing

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
make install
```

`make install` installs the package in editable mode, plus ruff, mypy, pytest, and pre-commit hooks.

## Checks

```bash
make lint
make format
make typecheck
make test
make denylist
```

`make ci` runs those in one go. `make typecheck` is `mypy --strict src`. `make test` is the same `pytest` line CI runs; use plain `pytest` before you push.

`tests/unit/test_house_style.py` locks the house-style prompt. Do not reword `SYSTEM` in `builder.py` unless you mean to change the style and you update that test.

Compose smoke (needs Docker):

```bash
make smoke
```

That command starts `compose.yaml` plus `compose.test.yaml`. The override adds a fake chat model from `tests/support/` so the suite can run without a live API key.

## Layout

`src/artifactsmith/` is the library and server.

`src/artifactsmith/renderers/` holds one module per format.

`tests/unit/` is what `pytest` runs by default.

`tests/e2e/` talks to the test compose stack.

`docs/` covers architecture, the threat model, operations, and formats.

Prefer focused modules. New renderers belong in `src/artifactsmith/renderers/`, not in `builder.py`. `service.py` is still a large single-process orchestration module; do not grow it further without extracting a coherent slice. The server is one process per data directory (SQLite single-writer).

## Pull requests

Use the PR template. Name the format you touched and the command you ran. Do not commit `.env`, tokens, or a private `reference/` tree. `scripts/denylist.sh` must stay green.
