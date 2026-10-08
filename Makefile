.PHONY: local-setup format lint test run freeze clean

UV ?= $(shell command -v uv 2>/dev/null || echo "$(HOME)/.local/bin/uv")

local-setup:
	@if ! command -v uv >/dev/null 2>&1 && [ ! -x "$(HOME)/.local/bin/uv" ]; then \
		curl -LsSf https://astral.sh/uv/install.sh | sh; \
	fi
	$(UV) sync --locked --all-groups
	$(UV) run pre-commit install
	$(UV) export --locked --no-dev --no-hashes --no-editable -o requirements.txt
	@echo "local-setup complete. Use: make run | make test | make lint"

format:
	$(UV) run black .
	$(UV) run ruff check --fix .

lint:
	$(UV) run --locked pre-commit run --all-files --show-diff-on-failure

test:
	$(UV) run pytest

run:
	$(UV) run uvicorn billing_meter.main:app --host 127.0.0.1 --port 8000 --reload

freeze:
	$(UV) export --locked --no-dev --no-hashes --no-editable -o requirements.txt

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache dist build
	rm -rf src/*.egg-info *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -f data/billing-meter.db data/billing-meter.db-wal data/billing-meter.db-shm
