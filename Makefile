PYTHON ?= python3.11
VENV ?= .venv
BIN := $(VENV)/bin

.PHONY: venv install lint format typecheck test test-network config check clean

venv:
	$(PYTHON) -m venv $(VENV)

install: venv
	$(BIN)/python -m pip install --upgrade pip
	$(BIN)/python -m pip install -e ".[dev]"

lint:
	$(BIN)/ruff check src tests
	$(BIN)/ruff format --check src tests

format:
	$(BIN)/ruff format src tests
	$(BIN)/ruff check --fix src tests

typecheck:
	$(BIN)/mypy

test:
	$(BIN)/pytest

test-network:
	$(BIN)/pytest -m network

config:
	$(BIN)/nem-forecast validate-config
	$(BIN)/nem-forecast show-config

check: lint typecheck test config

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache build dist src/*.egg-info
