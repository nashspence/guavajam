.DEFAULT_GOAL := help
.PHONY: help setup check check-source check-assets test image build verify check-reader check-backup editorial-checkpoint authorization receipt release-notes record gateway-plan gateway-deploy records-plan records-deploy

PYTHON := mise exec -- uv run --locked python
export FILE DIR STAGED_RUN_ID TAG REGISTRATION EDITORIAL_SHA256 WORK_SHA256 ARCHIVE_ROOT BACKUP_BASE RECORD_TARGET RECORD_WORKER

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

authorization: ## DIR=path/to/edition [STAGED_RUN_ID=id]  Recompute the staging authorization or print an offline proof without its run ID.
	@set --; if [ -n "$$STAGED_RUN_ID" ]; then set -- "--staged-run-id=$$STAGED_RUN_ID"; fi; \
		$(PYTHON) scripts/issuance.py prepare "$$@" -- "$${DIR:?Set DIR to the edition directory}"

editorial-checkpoint: ## TAG=works/essay/v1.0.0  Print the checkpoint to retain independently from the current approved commit after receipt and each editorial change.
	@$(PYTHON) scripts/audit_assets.py --checkpoint "$${TAG:?Set TAG to the issued edition tag}"

check-reader: ## DIR=edition REGISTRATION=receipt.json ARCHIVE_ROOT=archive EDITORIAL_SHA256=digest [RECORD_TARGET=https://archive/base] [WORK_SHA256=digest]  Verify edition routes and optionally the work page against retained checkpoints.
	@$(PYTHON) scripts/audit_assets.py --reader "$${DIR:?Set DIR to the edition directory}" \
		--registration "$${REGISTRATION:?Set REGISTRATION to the trusted receipt}" \
		--archive-root "$${ARCHIVE_ROOT:?Set ARCHIVE_ROOT to the directory containing the notice history}" \
		--editorial-sha256 "$${EDITORIAL_SHA256:?Set EDITORIAL_SHA256 to the independently retained current checkpoint}" \
		$${WORK_SHA256:+"--work-sha256=$$WORK_SHA256"}

check-backup: ## BACKUP_BASE=https://archive/base REGISTRATION=receipt.json ARCHIVE_ROOT=archive EDITORIAL_SHA256=digest [WORK_SHA256=digest]  Verify the backup package, edition routes and optionally the work page before cutover.
	@$(PYTHON) scripts/audit_assets.py --backup-base "$${BACKUP_BASE:?Set BACKUP_BASE to the recovery host base}" \
		--registration "$${REGISTRATION:?Set REGISTRATION to the trusted receipt}" \
		--archive-root "$${ARCHIVE_ROOT:?Set ARCHIVE_ROOT to the directory containing the notice history}" \
		--editorial-sha256 "$${EDITORIAL_SHA256:?Set EDITORIAL_SHA256 to the independently retained current checkpoint}" \
		$${WORK_SHA256:+"--work-sha256=$$WORK_SHA256"}

receipt: ## DIR=path/to/edition  Read back the public release and print its verified registration with timestamp.
	@$(PYTHON) scripts/issuance.py receipt -- "$${DIR:?Set DIR to the edition directory}"

record: ## TAG=works/essay/v1.0.0 [DIR=edition]  Generate the edition and work records; provide the verified package with the first receipt.
	@set --; if [ -n "$$DIR" ]; then set -- "--directory=$$DIR"; fi; \
		$(PYTHON) scripts/public_record.py --write "$${TAG:?Set TAG to the issued edition tag}" "$$@"

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
