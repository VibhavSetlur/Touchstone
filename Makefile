# Touchstone — developer Makefile.
#
# Targets that matter:
#   make install         pip install -e the whole workspace
#   make dev             install + dev dependencies
#   make test            run unit + security tests
#   make test-integration run tests against the docker stack
#   make doctor          run `touchstone doctor` against the active config
#   make compose-up      bring up the local dev stack (postgres, mysql, mongo)
#   make compose-down    take the stack down
#   make typecheck       pyright on every package
#   make lint            ruff check + ruff format --check
#   make fix             ruff check --fix + ruff format
#   make verify          lint + typecheck + test (the CI gate, locally)
#   make build           build sdists + wheels for all three Python packages
#   make clean           remove venvs, caches, build artifacts

SHELL := /usr/bin/env bash
PY    ?= python3
UV    := $(shell command -v uv 2>/dev/null)

# Pick the install command.
ifneq ($(UV),)
INSTALL := uv pip install
SYNC    := uv sync --all-packages --extra all
PYTEST  := uv run pytest
PYRIGHT := uv run pyright
RUFF    := uv run ruff
else
INSTALL := $(PY) -m pip install
SYNC    := $(PY) -m pip install -e packages/touchstone-core[all] -e packages/touchstone-mcp -e packages/touchstone-cli
PYTEST  := $(PY) -m pytest
PYRIGHT := $(PY) -m pyright
RUFF    := $(PY) -m ruff
endif

.PHONY: help
help:  ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-22s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: install
install:  ## pip install -e the workspace with `quickstart` extras
	$(INSTALL) -e packages/touchstone-core[quickstart] -e packages/touchstone-mcp -e packages/touchstone-cli

.PHONY: install-all
install-all:  ## install with EVERY connector + LLM provider extra
	$(INSTALL) -e packages/touchstone-core[all] -e packages/touchstone-mcp -e packages/touchstone-cli

.PHONY: dev
dev:  ## install + dev tooling (pytest, ruff, pyright)
	$(SYNC)

.PHONY: test
test:  ## unit + security tests (no docker required)
	$(PYTEST) tests/unit tests/security -q

.PHONY: test-integration
test-integration:  ## tests that hit the docker stack
	$(PYTEST) tests/integration -q -m integration

.PHONY: test-verbose
test-verbose:  ## same as test, verbose
	$(PYTEST) tests/unit tests/security -v

.PHONY: doctor
doctor:  ## run touchstone doctor
	touchstone doctor

.PHONY: compose-up
compose-up:  ## bring up postgres + mysql + mongo + minio
	docker compose up -d
	@echo
	@echo "Dev stack:"
	@echo "  postgres://postgres:touchstone@localhost:5432/shop"
	@echo "  mysql://root:touchstone@localhost:3306/shop"
	@echo "  mongodb://touchstone:touchstone@localhost:27017"
	@echo "  http://localhost:9001  (MinIO console)"

.PHONY: compose-down
compose-down:  ## tear down the dev stack
	docker compose down

.PHONY: lint
lint:  ## ruff check + format check
	$(RUFF) check .
	$(RUFF) format --check .

.PHONY: fix
fix:  ## ruff check --fix + format
	$(RUFF) check --fix .
	$(RUFF) format .

.PHONY: typecheck
typecheck:  ## pyright
	$(PYRIGHT)

.PHONY: verify
verify: lint typecheck test  ## full local CI gate

.PHONY: build
build:  ## build sdists + wheels for all three Python packages
	rm -rf dist/
	mkdir -p dist
	cd packages/touchstone-core && $(PY) -m build --outdir ../../dist
	cd packages/touchstone-mcp  && $(PY) -m build --outdir ../../dist
	cd packages/touchstone-cli  && $(PY) -m build --outdir ../../dist
	@ls -la dist/

.PHONY: clean
clean:  ## remove build artifacts, caches, venvs
	rm -rf .venv build/ dist/ .pytest_cache .ruff_cache .pyright
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name '*.egg-info' -exec rm -rf {} + 2>/dev/null || true

.PHONY: smoke
smoke:  ## end-to-end smoke test (init + doctor + profile against DuckDB)
	$(PY) scripts/smoke_test.py
