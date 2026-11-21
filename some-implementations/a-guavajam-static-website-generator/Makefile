.DEFAULT_GOAL := help
.PHONY: help setup test test-browser test-compose test-deployment-config render preview preview-docker site index update benchmark-search-download
PYTHON := mise exec -- uv run --locked --package a-guavajam-static-website-generator --group dev python
export REPO REV OUTPUT TRUSTED_GPG_FINGERPRINT SSH_ALLOWED_SIGNERS PREVIEW PREVIOUS_CATALOG INDEX TRUST SOURCE_REF REMOTE NO_FETCH REBUILD SERVE PORT REQUIRE_RETENTION S3_ENV GUAVAJAM_PREVIEW_IMAGE
help: ## Show implementation commands.
	@awk -F ':.*## ' '/^[a-z][a-z-]*:.*## / {printf "  %-20s %s\n", $$1, $$2}' Makefile
setup: ## Install this implementation and GuavaJam using pinned workspace tools.
	mise install
	mise exec -- uv sync --locked --package a-guavajam-static-website-generator --group dev
test: ## Run generator contracts with workspace-only fixtures.
	@$(PYTHON) ../../tools/run_tests.py a-guavajam-static-website-generator
	@mise exec -- node --test tests/test_download_transport.mjs
test-deployment-config: ## Verify this implementation's Compose and image contracts.
	@$(PYTHON) -m unittest discover -s ../../tests/deployment -p test_configuration.py -v
preview-docker: ## REPO=path [PORT=8082 S3_ENV=private-read.env]; launch a disposable complete website preview.
	@$(PYTHON) -m a_guavajam_static_website_generator.preview_docker --repo "$${REPO:-.}" --port "$${PORT:-8082}"

render: ## REPO=path REV=full-sha OUTPUT=new-directory TRUSTED_GPG_FINGERPRINT=fingerprint|SSH_ALLOWED_SIGNERS=path [PREVIEW=1]
	@set --; if [ "$$PREVIEW" = 1 ]; then set -- --unsigned-preview; elif [ -n "$$SSH_ALLOWED_SIGNERS" ]; then set -- --ssh-allowed-signers "$$SSH_ALLOWED_SIGNERS"; else set -- --trusted-gpg-fingerprint "$${TRUSTED_GPG_FINGERPRINT:?Set external signer trust or PREVIEW=1}"; fi; \
	$(PYTHON) -m a_guavajam_static_website_generator render --repo "$${REPO:?Set REPO}" --revision "$${REV:?Set REV}" --output "$${OUTPUT:?Set OUTPUT}" "$$@"

site: ## REPO=path OUTPUT=new-directory [SOURCE_REF=refs/heads/main PREVIOUS_CATALOG=last.json]; independent trust as render.
	@test "$$PREVIEW" != 1 || { echo 'A public site cannot contain unsigned previews.' >&2; exit 1; }
	@set --; if [ -n "$$SSH_ALLOWED_SIGNERS" ]; then set -- --ssh-allowed-signers "$$SSH_ALLOWED_SIGNERS"; else set -- --trusted-gpg-fingerprint "$${TRUSTED_GPG_FINGERPRINT:?Set external signer trust}"; fi; \
	$(PYTHON) -m a_guavajam_static_website_generator site --repo "$${REPO:?Set REPO}" --ref "$${SOURCE_REF:-refs/heads/main}" --output "$${OUTPUT:?Set OUTPUT}" $${PREVIOUS_CATALOG:+"--previous-catalog=$$PREVIOUS_CATALOG"} "$$@"

index: ## INDEX=public-url-or-yaml TRUST=operator-policy.yml OUTPUT=managed-directory; aggregate independently verified work repositories.
	@$(PYTHON) -m a_guavajam_static_website_generator index --index "$${INDEX:?Set INDEX}" --trust "$${TRUST:?Set TRUST to an external policy file}" --output "$${OUTPUT:?Set OUTPUT}"

update: ## REPO=path [OUTPUT=.build/site] Fetch origin/main, verify GuavaJam conventions, reuse unchanged views, atomically select OUTPUT/current. External trust once; safe for periodic invocation.
	@set --; if [ "$$NO_FETCH" = 1 ]; then set -- --no-fetch; fi; if [ "$$REBUILD" = 1 ]; then set -- "$$@" --rebuild; fi; \
	$(PYTHON) -m a_guavajam_static_website_generator update --repo "$${REPO:?Set REPO}" --output "$${OUTPUT:-.build/site}" --ref "$${SOURCE_REF:-refs/heads/main}" --remote "$${REMOTE:-origin}" "$$@"

preview: ## REPO=path SOURCE_REF=refs/heads/draft OUTPUT=new-directory [SERVE=1 PORT=0]; explicit unsigned preview, no public writes.
	@set --; case "$${SERVE:-0}" in 1) set -- --serve;; 0) ;; *) echo 'SERVE must be 0 or 1'; exit 1;; esac; \
	$(PYTHON) -m a_guavajam_static_website_generator preview --repo "$${REPO:?Set REPO}" --ref "$${SOURCE_REF:?Set SOURCE_REF}" --output "$${OUTPUT:?Set OUTPUT}" --port "$${PORT:-0}" "$$@"

benchmark-search-download: ## Report browser transfer, cache, memory and timing evidence; OUTPUT=directory, BENCHMARK_ARGS='--help' for variants.
	@$(PYTHON) tests/run_search_download_browser.py --output "$${OUTPUT:-.build/search-download}" $(BENCHMARK_ARGS)

test-browser: ## Run reader, keyboard, accessibility and print journeys; requires pinned Playwright browsers.
	@$(PYTHON) tests/run_browser.py
	@$(PYTHON) tests/run_catalog_browser.py
	@$(PYTHON) tests/run_search_browser.py
	@$(PYTHON) tests/run_work_search_browser.py
	@$(PYTHON) tests/run_search_download_browser.py --profiles local --repetitions 1

test-compose: test-deployment-config ## Build and qualify default/local-assets Compose with disposable signed source and real S3 media.
	docker compose build updater web assets private-preview
	@$(PYTHON) ../../tests/deployment/test_compose.py
	$(PYTHON) ../../tests/deployment/test_preview_compose.py

