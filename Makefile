.DEFAULT_GOAL := help
.PHONY: help setup test test-core test-view test-browser test-capture-browser render site update build check-artifacts version check-boundaries
PYTHON := mise exec -- uv run --locked --all-packages --group dev python
export REPO REV WORK OUTPUT TRUSTED_GPG_FINGERPRINT SSH_ALLOWED_SIGNERS PREVIEW CATALOG PREVIOUS_CATALOG INITIAL_CATALOG SOURCE_REF REMOTE NO_FETCH REBUILD
help: ## Show commands.
	@awk -F ':.*## ' '/^[a-z][a-z-]*:.*## / {printf "  %-16s %s\n", $$1, $$2}' Makefile
setup: ## Install pinned project tools and dependencies.
	mise install
	mise exec -- uv sync --locked --all-packages --group dev
test: check-boundaries test-core test-view ## Run both package contracts.
test-core: ## Run source, capture, storage and preservation contracts.
	@$(PYTHON) tools/run_tests.py core
	@mise exec -- node --test projects/core/tests/test_worker.mjs projects/core/tests/test_chatgpt_capture.mjs
test-view: ## Run rendering, catalog and update contracts.
	@$(PYTHON) tools/run_tests.py view
test-browser: ## Run reader, keyboard, accessibility and print journeys; requires pinned Playwright browsers.
	@$(PYTHON) projects/view/tests/run_browser.py
	@$(PYTHON) projects/view/tests/run_catalog_browser.py
render: ## REPO=path REV=full-sha WORK=slug OUTPUT=new-directory TRUSTED_GPG_FINGERPRINT=fingerprint|SSH_ALLOWED_SIGNERS=path [PREVIEW=1]
	@set --; if [ "$$PREVIEW" = 1 ]; then set -- --unsigned-preview; elif [ -n "$$SSH_ALLOWED_SIGNERS" ]; then set -- --ssh-allowed-signers "$$SSH_ALLOWED_SIGNERS"; else set -- --trusted-gpg-fingerprint "$${TRUSTED_GPG_FINGERPRINT:?Set external signer trust or PREVIEW=1}"; fi; \
	$(PYTHON) -m guavajam --repo "$${REPO:?Set REPO}" --revision "$${REV:?Set REV}" --work "$${WORK:?Set WORK}" --output "$${OUTPUT:?Set OUTPUT}" "$$@"

site: ## REPO=path CATALOG=reviewed.json OUTPUT=new-directory; PREVIOUS_CATALOG=last-deployed.json|INITIAL_CATALOG=1; same external trust as render (no preview)
	@test "$$PREVIEW" != 1 || { echo 'A catalog cannot contain unsigned previews.' >&2; exit 1; }
	@test -z "$$SSH_ALLOWED_SIGNERS" -o -z "$$TRUSTED_GPG_FINGERPRINT" || { echo 'Choose one external trust policy.' >&2; exit 1; }
	@test -z "$$PREVIOUS_CATALOG" -o "$$INITIAL_CATALOG" != 1 || { echo 'Choose one catalog baseline.' >&2; exit 1; }
	@set --; if [ -n "$$SSH_ALLOWED_SIGNERS" ]; then set -- --ssh-allowed-signers "$$SSH_ALLOWED_SIGNERS"; else set -- --trusted-gpg-fingerprint "$${TRUSTED_GPG_FINGERPRINT:?Set external signer trust}"; fi; \
	if [ -n "$$PREVIOUS_CATALOG" ]; then set -- "$$@" --previous-catalog "$$PREVIOUS_CATALOG"; elif [ "$$INITIAL_CATALOG" = 1 ]; then set -- "$$@" --initial-catalog; else echo 'Set PREVIOUS_CATALOG or INITIAL_CATALOG=1.' >&2; exit 1; fi; \
	$(PYTHON) -m guavajam site --repo "$${REPO:?Set REPO}" --catalog "$${CATALOG:?Set CATALOG}" --output "$${OUTPUT:?Set OUTPUT}" "$$@"

update: ## [REPO=../press OUTPUT=.build/site] Fetch origin/main, verify Press conventions, reuse unchanged views, atomically select OUTPUT/current. External trust once; safe for periodic invocation.
	@set --; if [ "$$NO_FETCH" = 1 ]; then set -- --no-fetch; fi; if [ "$$REBUILD" = 1 ]; then set -- "$$@" --rebuild; fi; \
	$(PYTHON) -m guavajam update --repo "$${REPO:-../press}" --output "$${OUTPUT:-.build/site}" --ref "$${SOURCE_REF:-refs/heads/main}" --remote "$${REMOTE:-origin}" "$$@"

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
