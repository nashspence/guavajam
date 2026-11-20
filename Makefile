.DEFAULT_GOAL := help
.PHONY: help setup test test-core test-view test-browser test-capture-browser test-compose test-deployment-config test-repository render preview site index update build check-artifacts version check-boundaries check-licenses
PYTHON := mise exec -- uv run --locked --all-packages --group dev python
export REPO REV OUTPUT TRUSTED_GPG_FINGERPRINT SSH_ALLOWED_SIGNERS PREVIEW PREVIOUS_CATALOG INDEX TRUST SOURCE_REF REMOTE NO_FETCH REBUILD SERVE PORT REQUIRE_RETENTION
help: ## Show commands.
	@awk -F ':.*## ' '/^[a-z][a-z-]*:.*## / {printf "  %-16s %s\n", $$1, $$2}' Makefile
setup: ## Install pinned project tools and dependencies.
	mise install
	mise exec -- uv sync --locked --all-packages --group dev
test: check-licenses check-boundaries test-core test-view test-deployment-config test-repository ## Run package, deployment configuration and repository contracts.
test-core: ## Run source, capture, storage and preservation contracts.
	@$(PYTHON) tools/run_tests.py core
	@mise exec -- node --test projects/core/tests/test_worker.mjs projects/core/tests/test_gateway_server.mjs projects/core/tests/test_private_preview.mjs projects/core/tests/test_chatgpt_capture.mjs
test-view: ## Run rendering, catalog and update contracts.
	@$(PYTHON) tools/run_tests.py view
test-browser: ## Run reader, keyboard, accessibility and print journeys; requires pinned Playwright browsers.
	@$(PYTHON) projects/view/tests/run_browser.py
	@$(PYTHON) projects/view/tests/run_catalog_browser.py
	@$(PYTHON) projects/view/tests/run_search_browser.py
	@$(PYTHON) projects/view/tests/run_work_search_browser.py
render: ## REPO=path REV=full-sha OUTPUT=new-directory TRUSTED_GPG_FINGERPRINT=fingerprint|SSH_ALLOWED_SIGNERS=path [PREVIEW=1]
	@set --; if [ "$$PREVIEW" = 1 ]; then set -- --unsigned-preview; elif [ -n "$$SSH_ALLOWED_SIGNERS" ]; then set -- --ssh-allowed-signers "$$SSH_ALLOWED_SIGNERS"; else set -- --trusted-gpg-fingerprint "$${TRUSTED_GPG_FINGERPRINT:?Set external signer trust or PREVIEW=1}"; fi; \
	$(PYTHON) -m guavajam render --repo "$${REPO:?Set REPO}" --revision "$${REV:?Set REV}" --output "$${OUTPUT:?Set OUTPUT}" "$$@"

site: ## REPO=path OUTPUT=new-directory [SOURCE_REF=refs/heads/main PREVIOUS_CATALOG=last.json]; independent trust as render.
	@test "$$PREVIEW" != 1 || { echo 'A public site cannot contain unsigned previews.' >&2; exit 1; }
	@set --; if [ -n "$$SSH_ALLOWED_SIGNERS" ]; then set -- --ssh-allowed-signers "$$SSH_ALLOWED_SIGNERS"; else set -- --trusted-gpg-fingerprint "$${TRUSTED_GPG_FINGERPRINT:?Set external signer trust}"; fi; \
	$(PYTHON) -m guavajam site --repo "$${REPO:?Set REPO}" --ref "$${SOURCE_REF:-refs/heads/main}" --output "$${OUTPUT:?Set OUTPUT}" $${PREVIOUS_CATALOG:+"--previous-catalog=$$PREVIOUS_CATALOG"} "$$@"

index: ## INDEX=public-url-or-yaml TRUST=operator-policy.yml OUTPUT=managed-directory; aggregate independently verified work repositories.
	@$(PYTHON) -m guavajam index --index "$${INDEX:?Set INDEX}" --trust "$${TRUST:?Set TRUST to an external policy file}" --output "$${OUTPUT:?Set OUTPUT}"

update: ## REPO=path [OUTPUT=.build/site] Fetch origin/main, verify GuavaJam conventions, reuse unchanged views, atomically select OUTPUT/current. External trust once; safe for periodic invocation.
	@set --; if [ "$$NO_FETCH" = 1 ]; then set -- --no-fetch; fi; if [ "$$REBUILD" = 1 ]; then set -- "$$@" --rebuild; fi; \
	$(PYTHON) -m guavajam update --repo "$${REPO:?Set REPO}" --output "$${OUTPUT:-.build/site}" --ref "$${SOURCE_REF:-refs/heads/main}" --remote "$${REMOTE:-origin}" "$$@"

test-capture-browser: ## Qualify the capture extension and installed import commands.
	@$(PYTHON) projects/core/tests/run_capture_browser.py
build: ## Build both wheels and source distributions without publishing.
	mise exec -- uv build --all-packages
check-artifacts: build ## Verify isolated wheels and rebuild standalone source distributions.
	@$(PYTHON) tools/check_artifacts.py
version: ## VERSION=x.y.z Update both package versions and their dependency; then regenerate uv.lock.
	@$(PYTHON) tools/version.py "$${VERSION:?Set VERSION}"
	mise exec -- uv lock

check-boundaries: ## Enforce core independence and single implementation ownership.
	@$(PYTHON) tools/check_boundaries.py

check-licenses: ## Verify REUSE copyright and license coverage.
	@UV_PROJECT_ENVIRONMENT=.build/licensing mise exec -- uv run --locked --only-group licensing reuse lint

test-repository: ## Verify licensing, the documentation boundary and onboarding links.
	@$(PYTHON) -m unittest discover -s tests/repository -v

test-deployment-config: ## Verify Compose boundaries and pinned build inputs without Docker.
	@$(PYTHON) -m unittest discover -s tests/deployment -p test_configuration.py -v

test-compose: test-deployment-config ## Build and qualify default/local-assets Compose with disposable signed source and real S3 media.
	docker compose build updater web assets private-preview
	@$(PYTHON) tests/deployment/test_compose.py
	$(PYTHON) tests/deployment/test_preview_compose.py

preview: ## REPO=path SOURCE_REF=refs/heads/draft OUTPUT=new-directory [SERVE=1 PORT=0]; explicit unsigned preview, no public writes.
	@set --; case "$${SERVE:-0}" in 1) set -- --serve;; 0) ;; *) echo 'SERVE must be 0 or 1'; exit 1;; esac; \
	$(PYTHON) -m guavajam preview --repo "$${REPO:?Set REPO}" --ref "$${SOURCE_REF:?Set SOURCE_REF}" --output "$${OUTPUT:?Set OUTPUT}" --port "$${PORT:-0}" "$$@"

.PHONY: test-provider
test-provider: ## Opt-in real S3 rehearsal; PROVIDER_ARGS='--help' for explicit credentials, gateway, run directory and apply options.
	@PYTHONPATH=tests/support $(PYTHON) -m guavajam_test_support.provider_qualification $(PROVIDER_ARGS)
