.DEFAULT_GOAL := help
.PHONY: help setup check check-source check-assets test test-browser image asset-upload assets-gc asset-retire assets-policy-plan assets-policy-apply chat-list chat-gfm publication-check gateway-plan gateway-deploy archive-export archive-check asset-pin publication-prepare

PYTHON := mise exec -- uv run --locked python
export FILE REV WORK OUTPUT TRUSTED_GPG_FINGERPRINT SSH_ALLOWED_SIGNERS B2_ENV BRANCH HEAD_SHA APPLY ASSET_APP KEY RETAIN_UNTIL RETENTION_MODE CONVERSATION TIP METADATA CLOUDFLARE_ENV ASSET_WORKER

help: ## Show this help.
	@printf '%s\n\n' 'Usage: make <target> [NAME=value]'
	@awk -F ':.*## ' '/^[a-z][a-z-]*:.*## / {printf "  %-16s %s\n", $$1, $$2}' Makefile

setup: ## Install the pinned tools and Python dependencies. Requires mise (https://mise.jdx.dev).
	mise install
	mise exec -- uv sync --locked

check: check-source check-assets ## Validate tracked sources and verify their assets online. Stage new files first.

check-source: ## Validate tracked GFM manuscripts and supporting sources offline.
	@$(PYTHON) scripts/validate_sources.py

check-assets: ## Download and verify referenced assets.
	@$(PYTHON) scripts/verify_source_assets.py

test: ## Run source, capture, storage, signature and asset gateway contracts.
	@$(PYTHON) -m unittest discover -s tests
	@mise exec -- node --test tests/test_worker.mjs
	@mise exec -- node --test tests/test_chatgpt_capture.mjs

test-browser: ## Exercise real Chrome capture permissions, downloads and reviewed GFM import. First install the pinned Playwright browsers with npm ci --ignore-scripts and npx playwright install chromium.
	@$(PYTHON) tests/run_capture_browser.py

image: ## FILE=asset  Print its content-addressed key and URL. Validated images retain their type; other files use opaque .bin downloads.
	@$(PYTHON) scripts/prepare_asset.py -- "$${FILE:?Set FILE to the asset path}"

asset-upload: ## FILE=asset [B2_ENV=.env.assets]  Validate, upload, pin and verify original image, MP3, MP4 or WebVTT bytes. Other files are bounded opaque downloads. Use the owner-only credential template in storage/.
	@$(PYTHON) scripts/manage_assets.py upload -- "$${FILE:?Set FILE to the asset path}"

assets-gc: ## [APPLY=1]  Dispatch a cleanup preview on main. For APPLY=1, pause uploads, lease renewals, and branch/PR changes until the queued job finishes.
	@$(PYTHON) scripts/asset_workflow.py request-gc

asset-retire: ## BRANCH=feature/name HEAD_SHA=40-character-sha  Dispatch exact-tip branch retirement on main.
	@$(PYTHON) scripts/asset_workflow.py request-retire

assets-policy-plan: ## ASSET_APP=app-slug  Preview deletion-only branch protection; requires independent no-bypass main protection.
	@$(PYTHON) scripts/configure_asset_maintenance.py

assets-policy-apply: ## ASSET_APP=app-slug  Activate the previewed branch-deletion ruleset using the operator's gh credentials.
	@$(PYTHON) scripts/configure_asset_maintenance.py --apply

publication-check: ## REV=full-sha TRUSTED_GPG_FINGERPRINT=hex|SSH_ALLOWED_SIGNERS=external-file  Verify signed source and all referenced bytes. Never signs or publishes.
	@set --; if [ -n "$$SSH_ALLOWED_SIGNERS" ]; then set -- --ssh-allowed-signers "$$SSH_ALLOWED_SIGNERS"; else set -- --trusted-gpg-fingerprint "$${TRUSTED_GPG_FINGERPRINT:?Set independent signer trust}"; fi; \
	$(PYTHON) scripts/publication_commit.py --revision "$${REV:?Set REV to the full signed commit SHA}" "$$@"

chat-list: ## FILE=../export.zip  List conversation IDs and titles locally. Also accepts conversations JSON.
	@$(PYTHON) scripts/extract_chatgpt.py --list -- "$${FILE:?Set FILE to the export path}"

chat-gfm: ## FILE=../export.zip CONVERSATION=id OUTPUT=../review.md [TIP=node-id] [METADATA=../fields.yml]  Convert one branch to anchored GFM for review, reporting losses. OUTPUT must be new and outside the checkout; no commit is made.
	@$(PYTHON) scripts/extract_chatgpt.py --conversation="$${CONVERSATION:?Set CONVERSATION to the conversation ID}" \
		--output="$${OUTPUT:?Set OUTPUT to a new file path}" $${TIP:+"--tip=$$TIP"} $${METADATA:+"--metadata=$$METADATA"} -- "$${FILE:?Set FILE to the export path}"

gateway-plan: ## Preview authenticated, version-pinned asset routing without reading credentials or making API calls.
	@$(PYTHON) scripts/deploy_asset_gateway.py

gateway-deploy: ## Apply the asset gateway plan to Cloudflare. Same arguments as gateway-plan.
	@$(PYTHON) scripts/deploy_asset_gateway.py --apply


archive-export: ## REV=full-sha OUTPUT=new-directory TRUSTED_GPG_FINGERPRINT=hex|SSH_ALLOWED_SIGNERS=path  Preserve signed Git objects and one copy per referenced asset outside the checkout.
	@set --; if [ -n "$$SSH_ALLOWED_SIGNERS" ]; then set -- --ssh-allowed-signers "$$SSH_ALLOWED_SIGNERS"; else set -- --trusted-gpg-fingerprint "$${TRUSTED_GPG_FINGERPRINT:?Set independent signer trust}"; fi; \
	$(PYTHON) scripts/source_archive.py export --revision "$${REV:?Set REV}" --directory "$${OUTPUT:?Set OUTPUT}" "$$@"
archive-check: ## REV=independently-retained-sha OUTPUT=archive-directory TRUSTED_GPG_FINGERPRINT=hex|SSH_ALLOWED_SIGNERS=path  Restore Git objects and verify every saved dependency against external signer trust.
	@set --; if [ -n "$$SSH_ALLOWED_SIGNERS" ]; then set -- --ssh-allowed-signers "$$SSH_ALLOWED_SIGNERS"; else set -- --trusted-gpg-fingerprint "$${TRUSTED_GPG_FINGERPRINT:?Set independent signer trust}"; fi; \
	$(PYTHON) scripts/source_archive.py check --revision "$${REV:?Set REV}" --directory "$${OUTPUT:?Set OUTPUT}" "$$@"
asset-pin: ## KEY=sha256/digest.type [RETAIN_UNTIL=epoch-ms RETENTION_MODE=governance|compliance]  Hash an exact stored version, inspect its bytes, optionally extend explicit retention, and publish its private pin sidecar.
	@set --; if [ -n "$$RETAIN_UNTIL" ]; then set -- --retain-until "$$RETAIN_UNTIL" --retention-mode "$${RETENTION_MODE:?Set an explicit retention mode}"; fi; \
	$(PYTHON) scripts/asset_versions.py "$${KEY:?Set KEY}" "$$@"

publication-prepare: ## REV=full-draft-sha WORK=slug [B2_ENV=.env.assets]  Verify committed source, complete public bytes and private B2 retention before the owner manually signs an empty publication commit.
	@$(PYTHON) scripts/prepare_publication.py --revision "$${REV:?Set REV}" --work "$${WORK:?Set WORK}"
