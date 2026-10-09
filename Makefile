PYTHON ?= .venv/bin/python
PIP ?= .venv/bin/pip
export PATH := $(CURDIR)/.venv/bin:$(PATH)

.PHONY: venv install lint format typecheck test smoke denylist docker-build ci help

help:
	@echo "make install     create .venv and install the package plus dev tools"
	@echo "make lint        ruff check"
	@echo "make format      ruff format"
	@echo "make typecheck   mypy"
	@echo "make test        unit tests with coverage"
	@echo "make smoke       compose stack + checkpoint smoke test"
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
	$(PYTHON) -m ruff check src tests examples

format:
	$(PYTHON) -m ruff format src tests examples

typecheck:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest

smoke:
	docker compose --profile test up -d --build
	$(PYTHON) tests/smoke/test_checkpoint1.py
	$(PYTHON) tests/smoke/test_formats.py

denylist:
	bash scripts/denylist.sh

docker-build:
	docker build -f docker/Dockerfile -t artifactsmith:local .

ci: lint typecheck test denylist
