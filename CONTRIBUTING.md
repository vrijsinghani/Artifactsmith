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

`make ci` runs those in one go.

`tests/unit/test_house_style.py` locks the house-style prompt. Do not reword `SYSTEM` in `builder.py` unless you mean to change the style and you update that test.

Compose smoke (needs Docker):

```bash
make smoke
```

That command uses the compose `test` profile and the bundled mock LLM. It does not need an API key.

## Layout

`src/artifactsmith/` is the library and server.

`src/artifactsmith/renderers/` holds one module per format.

`tests/unit/` is what `pytest` runs by default.

`tests/smoke/` talks to the compose stack.

`docs/` covers architecture, the threat model, operations, and formats.

Keep new modules under about 300 lines. Add a renderer module instead of growing `builder.py`.

## Pull requests

Use the PR template. Name the format you touched and the command you ran. Do not commit `.env`, tokens, or a private `reference/` tree. `scripts/denylist.sh` must stay green.
