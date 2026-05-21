# auladcanto-mcp — developer task runner
#
# All targets assume an activated virtualenv (or the project's `.venv/`).
# Use `make install` once after cloning; then `make test`, `make lint`, …

PYTHON ?= python
PKG := src/auladcanto

.PHONY: help install test test-unit test-integration test-golden lint format typecheck clean

help:
	@echo "Targets:"
	@echo "  install            install package + dev extras in editable mode"
	@echo "  test               run the full pytest suite"
	@echo "  test-unit          run unit tests only"
	@echo "  test-integration   run integration tests only"
	@echo "  test-golden        run golden-file tests only"
	@echo "  lint               ruff check (no fixes)"
	@echo "  format             ruff format + ruff check --fix"
	@echo "  typecheck          mypy on the source package"
	@echo "  clean              remove caches and build artifacts"

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest -q

test-unit:
	$(PYTHON) -m pytest -q tests/unit

test-integration:
	$(PYTHON) -m pytest -q tests/integration

test-golden:
	$(PYTHON) -m pytest -q tests/golden

lint:
	$(PYTHON) -m ruff check src tests
	$(PYTHON) -m ruff format --check src tests

format:
	$(PYTHON) -m ruff format src tests
	$(PYTHON) -m ruff check --fix src tests

typecheck:
	$(PYTHON) -m mypy $(PKG)

clean:
	rm -rf build dist .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name "*.egg-info" -prune -exec rm -rf {} +
