PYTHON ?= .venv/bin/python
PIP ?= .venv/bin/pip
export PATH := $(CURDIR)/.venv/bin:$(PATH)

.PHONY: venv install lint format typecheck test smoke denylist docker-build ci help

help:
	@echo "make install     create .venv and install the package plus dev tools"
	@echo "make lint        ruff check"
	@echo "make format      ruff format"
	@echo "make typecheck   mypy --strict src"
	@echo "make test        unit tests with coverage (same command as CI)"
	@echo "make smoke       test compose stack + end-to-end smoke"
	@echo "make denylist    scan tracked files for private identifiers"
	@echo "make docker-build  build the server image"
	@echo "make ci          lint + typecheck + test + denylist"

venv:
	python3 -m venv .venv

install: venv
	$(PIP) install -U pip setuptools wheel
	$(PIP) install -e ".[dev]"
	$(PYTHON) -m pre_commit install || true

lint:
	ruff check src tests examples

format:
	ruff format src tests examples

typecheck:
	mypy --strict src

test:
	pytest

smoke:
	bash scripts/ensure-local-env.sh
	docker compose -f compose.yaml -f compose.test.yaml up -d --build
	python -m tests.e2e.test_end_to_end
	python -m tests.e2e.test_formats
	python -m tests.e2e.test_bind_address

denylist:
	bash scripts/denylist.sh

docker-build:
	docker build -f docker/Dockerfile -t artifactsmith:local .

ci: lint typecheck test denylist
