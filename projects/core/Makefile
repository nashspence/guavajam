.DEFAULT_GOAL := help
.PHONY: help setup test test-browser
PYTHON := mise exec -- uv run --locked --package guavajam-core python
include src/guavajam_core/resources/commands.mk

help: ## Show commands and installed publisher recipes.
	@awk -F ':.*## ' '/^[a-z][a-z-]*:.*## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
setup: ## Install the pinned toolchain and core package.
	mise install
	mise exec -- uv sync --locked --package guavajam-core
test: ## Run the core contracts using shared workspace test support.
	@$(PYTHON) ../../tools/run_tests.py core
test-browser: ## Qualify real capture extension downloads and imports.
	@$(PYTHON) tests/run_capture_browser.py
