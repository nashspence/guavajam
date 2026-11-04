.DEFAULT_GOAL := help
.PHONY: help setup check check-source check-assets test image build verify gateway-plan gateway-deploy

PYTHON := mise exec -- uv run --locked python
export FILE DIR TAG

help: ## Show this help.
	@printf '%s\n\n' 'Usage: make <target> [NAME=value]'
	@awk -F ':.*## ' '/^[a-z][a-z-]*:.*## / {printf "  %-16s %s\n", $$1, $$2}' Makefile

setup: ## Install the pinned tools and Python dependencies. Requires mise (https://mise.jdx.dev).
	mise install
	mise exec -- uv sync --locked

check: check-source check-assets ## Validate tracked sources and verify their images online.

check-source: ## Validate tracked manuscripts and registrations offline.
	@$(PYTHON) scripts/validate_sources.py

check-assets: ## Download and verify referenced images.
	@$(PYTHON) scripts/verify_source_assets.py

test: ## Run the Python and asset gateway test suites.
	@$(PYTHON) -m unittest discover -s tests
	@mise exec -- node --test tests/test_worker.mjs

image: ## FILE=figure.svg  Prepare an image and print its upload key and headers.
	@$(PYTHON) scripts/prepare_asset.py -- "$${FILE:?Set FILE to the image path}"

build: ## TAG=works/essay/v1.0.0  Build a local edition from HEAD into dist/<tag>/.
	@$(PYTHON) scripts/build_edition.py --commit "$$(git rev-parse HEAD)" \
		--tag "$${TAG:?Set TAG to the intended edition tag}" --output "dist/$$TAG"

verify: ## DIR=path/to/edition  Verify a downloaded or locally built edition offline.
	@$(PYTHON) scripts/verify_edition.py -- "$${DIR:?Set DIR to the edition directory}"

gateway-plan: ## Preview the asset gateway deployment.
	@$(PYTHON) scripts/deploy_asset_gateway.py

gateway-deploy: ## Apply the asset gateway deployment to Cloudflare.
	@$(PYTHON) scripts/deploy_asset_gateway.py --apply
