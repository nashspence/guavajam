.DEFAULT_GOAL := help
.PHONY: help setup check check-source check-assets test image build verify authorization receipt release-notes gateway-plan gateway-deploy records-plan records-deploy

PYTHON := mise exec -- uv run --locked python
export FILE DIR TAG REGISTRATION RECORD_TARGET RECORD_WORKER

help: ## Show this help.
	@printf '%s\n\n' 'Usage: make <target> [NAME=value]'
	@awk -F ':.*## ' '/^[a-z][a-z-]*:.*## / {printf "  %-16s %s\n", $$1, $$2}' Makefile

setup: ## Install the pinned tools and Python dependencies. Requires mise (https://mise.jdx.dev).
	mise install
	mise exec -- uv sync --locked

check: check-source check-assets ## Validate tracked sources and verify their images online. Stage new files first.

check-source: ## Validate tracked manuscripts and registrations offline.
	@$(PYTHON) scripts/validate_sources.py

check-assets: ## Download and verify referenced images.
	@$(PYTHON) scripts/verify_source_assets.py

test: ## Run the Python and asset gateway test suites.
	@$(PYTHON) -m unittest discover -s tests
	@mise exec -- node --test tests/test*worker.mjs

image: ## FILE=figure.svg  Print the upload key, headers, and manuscript URL.
	@$(PYTHON) scripts/prepare_asset.py -- "$${FILE:?Set FILE to the image path}"

build: ## TAG=works/essay/v1.0.0  Build a local edition from HEAD into dist/<tag>/.
	@$(PYTHON) scripts/build_edition.py --commit "$$(git rev-parse HEAD)" \
		--tag "$${TAG:?Set TAG to the intended edition tag}" --output "dist/$$TAG"

verify: ## DIR=path/to/edition [REGISTRATION=trusted.json]  Verify the package and optionally its authorization offline.
	@set --; if [ -n "$$REGISTRATION" ]; then set -- "--registration=$$REGISTRATION"; fi; \
		$(PYTHON) scripts/verify_edition.py "$$@" -- "$${DIR:?Set DIR to the edition directory}"

authorization: ## DIR=path/to/edition  Print the registration for a reviewed staging package.
	@$(PYTHON) scripts/issuance.py prepare -- "$${DIR:?Set DIR to the edition directory}"

receipt: ## DIR=path/to/edition  Read back the public release and print its verified registration with timestamp.
	@$(PYTHON) scripts/issuance.py receipt -- "$${DIR:?Set DIR to the edition directory}"

release-notes: ## DIR=path/to/edition  Print the public body with registered identifiers and editorial notices.
	@$(PYTHON) scripts/editorial_notices.py -- "$${DIR:?Set DIR to the edition directory}"

gateway-plan: ## Preview the asset gateway deployment.
	@$(PYTHON) scripts/deploy_asset_gateway.py

gateway-deploy: ## Apply the asset gateway deployment to Cloudflare.
	@$(PYTHON) scripts/deploy_asset_gateway.py --apply

records-plan: ## [RECORD_TARGET=https://archive/base] [RECORD_WORKER=press-records]  Preview edition-record routing.
	@$(PYTHON) scripts/deploy_record_gateway.py

records-deploy: ## Apply edition-record routing and its Cloudflare TLS Custom Domain. Same arguments as records-plan.
	@$(PYTHON) scripts/deploy_record_gateway.py --apply
