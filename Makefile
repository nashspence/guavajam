.DEFAULT_GOAL := help
.PHONY: help setup check check-source check-assets test test-browser image asset-upload assets-gc asset-retire assets-policy-plan assets-policy-apply chat-list chat-extract chat-gfm build preview preview-build verify check-reader check-backup check-backup-archive archive-export editorial-checkpoint authorization receipt release-notes record gateway-plan gateway-deploy records-plan records-deploy

PYTHON := mise exec -- uv run --locked python
export FILE DIR STAGED_RUN_ID TAG REGISTRATION EDITORIAL_SHA256 WORK_SHA256 ARCHIVE_ROOT ARCHIVE_COMMIT BACKUP_BASE RECORD_TARGET RECORD_WORKER READING_BUCKET
export CONVERSATION TIP OUTPUT PACKAGE_ROOT PORT
export B2_ENV BRANCH HEAD_SHA APPLY ASSET_APP
export ASSET_TARGET

help: ## Show this help.
	@printf '%s\n\n' 'Usage: make <target> [NAME=value]'
	@awk -F ':.*## ' '/^[a-z][a-z-]*:.*## / {printf "  %-16s %s\n", $$1, $$2}' Makefile

setup: ## Install the pinned tools and Python dependencies. Requires mise (https://mise.jdx.dev).
	mise install
	mise exec -- uv sync --locked

check: check-source check-assets ## Validate tracked sources and verify their assets online. Stage new files first.

check-source: ## Validate tracked manuscripts and registrations offline.
	@$(PYTHON) scripts/validate_sources.py

check-assets: ## Download and verify referenced assets.
	@$(PYTHON) scripts/verify_source_assets.py

test: ## Run the Python and asset gateway test suites.
	@$(PYTHON) -m unittest discover -s tests
	@mise exec -- node --test tests/test*worker.mjs
	@mise exec -- node --test tests/test_chatgpt_capture.mjs

test-browser: ## Check reader journeys and accessibility in three engines, plus the real Chrome capture extension and imports. First run npm ci --ignore-scripts and npx playwright install chromium firefox webkit; browser runtime libraries must be available.
	@$(PYTHON) tests/run_offline_browser.py
	@$(PYTHON) tests/run_capture_browser.py

image: ## FILE=asset  Print its content-addressed key and URL. Validated images retain their type; other files use opaque .bin downloads.
	@$(PYTHON) scripts/prepare_asset.py -- "$${FILE:?Set FILE to the asset path}"

asset-upload: ## FILE=asset [B2_ENV=.env.assets]  Upload bounded original bytes to B2 and verify storage and public delivery. Non-images are opaque downloads. Use an owner-only credential file from storage/credentials.env.example with a bucket/prefix-scoped key.
	@$(PYTHON) scripts/manage_assets.py upload -- "$${FILE:?Set FILE to the asset path}"

assets-gc: ## [APPLY=1]  Dispatch a cleanup preview on main. For APPLY=1, pause uploads, lease renewals, and branch/PR changes until the queued job finishes.
	@$(PYTHON) scripts/asset_workflow.py request-gc

asset-retire: ## BRANCH=feature/name HEAD_SHA=40-character-sha  Dispatch exact-tip branch retirement on main.
	@$(PYTHON) scripts/asset_workflow.py request-retire

assets-policy-plan: ## ASSET_APP=app-slug  Preview deletion-only branch protection; requires independent no-bypass main protection.
	@$(PYTHON) scripts/configure_asset_maintenance.py

assets-policy-apply: ## ASSET_APP=app-slug  Activate the previewed branch-deletion ruleset using the operator's gh credentials.
	@$(PYTHON) scripts/configure_asset_maintenance.py --apply

chat-list: ## FILE=../export.zip  List conversation IDs and titles locally. Also accepts conversations JSON.
	@$(PYTHON) scripts/extract_chatgpt.py --list -- "$${FILE:?Set FILE to the export path}"

chat-extract: ## FILE=../export.zip CONVERSATION=id OUTPUT=../review.json [TIP=node-id]  Extract one branch for local review. OUTPUT must be new and outside the checkout.
	@$(PYTHON) scripts/extract_chatgpt.py --conversation="$${CONVERSATION:?Set CONVERSATION to the conversation ID}" \
		--output="$${OUTPUT:?Set OUTPUT to a new file path}" $${TIP:+"--tip=$$TIP"} -- "$${FILE:?Set FILE to the export path}"

chat-gfm: ## FILE=../export.zip CONVERSATION=id OUTPUT=../review.md [TIP=node-id]  Convert one branch to anchored GFM for review, reporting losses. OUTPUT must be new and outside the checkout; no commit is made.
	@$(PYTHON) scripts/extract_chatgpt.py --gfm --conversation="$${CONVERSATION:?Set CONVERSATION to the conversation ID}" \
		--output="$${OUTPUT:?Set OUTPUT to a new file path}" $${TIP:+"--tip=$$TIP"} -- "$${FILE:?Set FILE to the export path}"

build: ## TAG=works/essay/v1.0.0  Build from HEAD into dist/<tag>/. Downloads add a complete offline ZIP containing canonical HTML, provenance, checksums, reading copy and original files. Extract and open index.html; make verify DIR=the.zip verifies it.
	@$(PYTHON) scripts/build_edition.py --commit "$$(git rev-parse HEAD)" \
		--tag "$${TAG:?Set TAG to the intended edition tag}" --output "dist/$$TAG"

preview: ## TAG=works/essay/v1.0.0 [PORT=8000]  Preview committed HEAD on loopback; Ctrl-C removes temporary files. PORT=0 selects a free port.
	@$(PYTHON) scripts/preview_site.py --tag "$${TAG:?Set TAG to the proposed edition tag}" --port "$${PORT:-8000}"

preview-build: ## TAG=works/essay/v1.0.0 OUTPUT=../preview  Build a simulated site in a new external directory without serving it.
	@$(PYTHON) scripts/preview_site.py --tag "$${TAG:?Set TAG to the proposed edition tag}" --output "$${OUTPUT:?Set OUTPUT to a new external directory}"

verify: ## DIR=edition-directory-or-complete.zip [REGISTRATION=trusted.json]  Verify all issued bytes offline; a complete ZIP includes its canonical HTML and verification files. A trusted registration also checks authorization.
	@set --; if [ -n "$$REGISTRATION" ]; then set -- "--registration=$$REGISTRATION"; fi; \
		$(PYTHON) scripts/verify_edition.py "$$@" -- "$${DIR:?Set DIR to the edition directory}"

authorization: ## DIR=path/to/edition [STAGED_RUN_ID=id]  Recompute the staging authorization or print an offline proof without its run ID.
	@set --; if [ -n "$$STAGED_RUN_ID" ]; then set -- "--staged-run-id=$$STAGED_RUN_ID"; fi; \
		$(PYTHON) scripts/issuance.py prepare "$$@" -- "$${DIR:?Set DIR to the edition directory}"

editorial-checkpoint: ## TAG=works/essay/v1.0.0  Print the checkpoint to retain independently from the current approved commit after receipt and each metadata or edition-page change.
	@$(PYTHON) scripts/audit_assets.py --checkpoint "$${TAG:?Set TAG to the issued edition tag}"

check-reader: ## DIR=edition REGISTRATION=receipt.json ARCHIVE_ROOT=archive EDITORIAL_SHA256=digest [RECORD_TARGET=https://archive/base] [WORK_SHA256=digest]  Verify edition routes using archived notices and the approved edition page; optionally check the work page.
	@$(PYTHON) scripts/audit_assets.py --reader "$${DIR:?Set DIR to the edition directory}" \
		--registration "$${REGISTRATION:?Set REGISTRATION to the trusted receipt}" \
		--archive-root "$${ARCHIVE_ROOT:?Set ARCHIVE_ROOT to the root containing notices and records/editions}" \
		--editorial-sha256 "$${EDITORIAL_SHA256:?Set EDITORIAL_SHA256 to the independently retained current checkpoint}" \
		$${WORK_SHA256:+"--work-sha256=$$WORK_SHA256"}

check-backup: ## BACKUP_BASE=https://archive/base REGISTRATION=receipt.json ARCHIVE_ROOT=archive EDITORIAL_SHA256=digest [WORK_SHA256=digest]  Verify standby attachments, archived notices and edition page; optionally check the work page.
	@$(PYTHON) scripts/audit_assets.py --backup-base "$${BACKUP_BASE:?Set BACKUP_BASE to the recovery host base}" \
		--registration "$${REGISTRATION:?Set REGISTRATION to the trusted receipt}" \
		--archive-root "$${ARCHIVE_ROOT:?Set ARCHIVE_ROOT to the root containing notices and records/editions}" \
		--editorial-sha256 "$${EDITORIAL_SHA256:?Set EDITORIAL_SHA256 to the independently retained current checkpoint}" \
		$${WORK_SHA256:+"--work-sha256=$$WORK_SHA256"}

archive-export: ## ARCHIVE_COMMIT=sha PACKAGE_ROOT=../packages OUTPUT=../standby  Export verified saved packages, approved pages, full Git history and packaged downloads under public/sha256, without the asset origin.
	@$(PYTHON) scripts/export_archive.py --commit="$${ARCHIVE_COMMIT:?Set ARCHIVE_COMMIT to the independently retained current commit SHA}" \
		--package-root="$${PACKAGE_ROOT:?Set PACKAGE_ROOT to the directory containing saved packages by tag}" \
		--output="$${OUTPUT:?Set OUTPUT to a new external directory}"

check-backup-archive: ## BACKUP_BASE=https://archive/base ARCHIVE_COMMIT=sha  Verify every issued edition, reading copy and work page against an independently retained current commit.
	@$(PYTHON) scripts/check_backup_archive.py --backup-base "$${BACKUP_BASE:?Set BACKUP_BASE to the recovery host base}" \
		--commit "$${ARCHIVE_COMMIT:?Set ARCHIVE_COMMIT to the independently retained current commit SHA}"

receipt: ## DIR=path/to/edition  Read back the public release and print its verified registration with timestamp.
	@$(PYTHON) scripts/issuance.py receipt -- "$${DIR:?Set DIR to the edition directory}"

record: ## TAG=works/essay/v1.0.0 [DIR=edition]  Generate the edition and work records; provide the verified package with the first receipt.
	@set --; if [ -n "$$DIR" ]; then set -- "--directory=$$DIR"; fi; \
		$(PYTHON) scripts/public_record.py --write "$${TAG:?Set TAG to the issued edition tag}" "$$@"

release-notes: ## DIR=path/to/edition  Print the public body with registered identifiers and editorial notices.
	@$(PYTHON) scripts/editorial_notices.py -- "$${DIR:?Set DIR to the edition directory}"

gateway-plan: ## [ASSET_TARGET=https://archive/base]  Preview asset routing; optionally serve independently exported sha256 objects from standby instead of B2.
	@$(PYTHON) scripts/deploy_asset_gateway.py

gateway-deploy: ## Apply the asset gateway plan to Cloudflare. Same arguments as gateway-plan.
	@$(PYTHON) scripts/deploy_asset_gateway.py --apply

records-plan: ## [RECORD_TARGET=https://archive/base] [RECORD_WORKER=press-records] [READING_BUCKET=name]  Preview edition-record routing and optional private R2 replica.
	@$(PYTHON) scripts/deploy_record_gateway.py

records-deploy: ## Apply edition-record routing and its Cloudflare TLS Custom Domain. Same arguments as records-plan.
	@$(PYTHON) scripts/deploy_record_gateway.py --apply
