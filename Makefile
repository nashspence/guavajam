.DEFAULT_GOAL := help
.PHONY: help setup test test-guavajam test-a-guavajam-static-website-generator test-browser test-archiver-browser test-compose test-deployment-config test-repository build check-artifacts version check-boundaries check-licenses
PYTHON := mise exec -- uv run --locked --package guavajam --no-dev python
GENERATOR_PYTHON := mise exec -- uv run --locked --package a-guavajam-static-website-generator --group dev python
EXTRACTOR_PYTHON := mise exec -- uv run --locked --package a-chatgpt-conversation-archive-gfm-extractor python
export REPO REV OUTPUT TRUSTED_GPG_FINGERPRINT SSH_ALLOWED_SIGNERS PREVIEW PREVIOUS_CATALOG INDEX TRUST SOURCE_REF REMOTE NO_FETCH REBUILD SERVE PORT REQUIRE_RETENTION
include src/guavajam/resources/commands.mk

help: ## Show commands.
	@awk -F ':.*## ' '/^[a-z][a-z-]*:.*## / {printf "  %-16s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
setup: ## Install pinned project tools and dependencies.
	mise install
	mise exec -- uv sync --locked --package guavajam --no-dev
test: check-licenses check-boundaries test-guavajam test-a-guavajam-static-website-generator test-a-chatgpt-conversation-archive-gfm-extractor test-a-chatgpt-conversation-archiver-browser-extension test-deployment-config test-repository ## Run package, deployment configuration and repository contracts.
test-guavajam: ## Run source, storage and preservation contracts.
	@$(PYTHON) tools/run_tests.py guavajam
	@mise exec -- node --test tests/guavajam/test_worker.mjs tests/guavajam/test_gateway_server.mjs tests/guavajam/test_private_preview.mjs
test-a-guavajam-static-website-generator: ## Run rendering, catalog and update contracts.
	@$(GENERATOR_PYTHON) tools/run_tests.py a-guavajam-static-website-generator
	@mise exec -- node --test some-implementations/a-guavajam-static-website-generator/tests/test_download_transport.mjs some-implementations/a-guavajam-static-website-generator/tests/test_search_hash.mjs some-implementations/a-guavajam-static-website-generator/tests/test_search_chunks.mjs some-implementations/a-guavajam-static-website-generator/tests/test_search_state.mjs
test-browser: ## Qualify the optional website implementation.
	@$(MAKE) -C some-implementations/a-guavajam-static-website-generator test-browser PYTHON="$(GENERATOR_PYTHON)"

test-archiver-browser: ## Qualify the independently built archive extension in Chrome.
	@$(MAKE) -C some-implementations/a-chatgpt-conversation-archiver-browser-extension test-browser PYTHON="$(PYTHON)"
build: ## Build separately owned Python distributions and the extension ZIP.
	mise exec -- uv build --all-packages
	@$(MAKE) -C some-implementations/a-chatgpt-conversation-archiver-browser-extension build OUTPUT="../../dist/a-chatgpt-conversation-archiver-browser-extension-$(shell $(PYTHON) tools/version.py).zip" PYTHON="$(PYTHON)"
check-artifacts: build ## Verify isolated wheels and rebuild standalone source distributions.
	@$(PYTHON) tools/check_artifacts.py
version: ## VERSION=x.y.z Update coordinated project versions and exact GuavaJam dependencies; then regenerate uv.lock.
	@$(PYTHON) tools/version.py "$${VERSION:?Set VERSION}"
	mise exec -- uv lock

check-boundaries: ## Enforce GuavaJam independence and single implementation ownership.
	@$(PYTHON) tools/check_boundaries.py

check-licenses: ## Verify REUSE copyright and license coverage.
	@UV_PROJECT_ENVIRONMENT=.build/licensing mise exec -- uv run --locked --only-group licensing reuse lint

test-repository: ## Verify licensing, the documentation boundary and onboarding links.
	@$(PYTHON) -m unittest discover -s tests/repository -v

test-deployment-config: ## Verify Compose boundaries and pinned build inputs without Docker.
	@$(PYTHON) -m unittest discover -s tests/deployment -p test_configuration.py -v

test-compose: ## Qualify the optional website implementation.
	@$(MAKE) -C some-implementations/a-guavajam-static-website-generator test-compose PYTHON="$(GENERATOR_PYTHON)"

.PHONY: test-provider
test-provider: ## Opt-in real S3 rehearsal; PROVIDER_ARGS='--help' for explicit credentials, gateway, run directory and apply options.
	@PYTHONPATH=tests/support $(PYTHON) -m guavajam_test_support.provider_qualification $(PROVIDER_ARGS)

.PHONY: test-a-chatgpt-conversation-archive-gfm-extractor test-a-chatgpt-conversation-archiver-browser-extension test-chatgpt-handoff
test-a-chatgpt-conversation-archive-gfm-extractor: ## Verify selected-branch extraction and review-GFM output.
	@$(EXTRACTOR_PYTHON) tools/run_tests.py a-chatgpt-conversation-archive-gfm-extractor
test-a-chatgpt-conversation-archiver-browser-extension: ## Verify archive capture and the independent extension ZIP.
	@$(MAKE) -C some-implementations/a-chatgpt-conversation-archiver-browser-extension test PYTHON="$(PYTHON)"
test-chatgpt-handoff: ## Verify downloaded archives through isolated extractor artifacts.
	@$(PYTHON) tools/check_chatgpt_handoff.py
