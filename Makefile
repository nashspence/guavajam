.DEFAULT_GOAL := help
.PHONY: help setup test test-browser render site
PYTHON := mise exec -- uv run --locked --group dev python
export REPO REV WORK OUTPUT TRUSTED_GPG_FINGERPRINT SSH_ALLOWED_SIGNERS PREVIEW CATALOG PREVIOUS_CATALOG INITIAL_CATALOG
help: ## Show commands.
	@awk -F ':.*## ' '/^[a-z][a-z-]*:.*## / {printf "  %-16s %s\n", $$1, $$2}' Makefile
setup: ## Install pinned project tools and dependencies.
	mise install
	mise exec -- uv sync --locked --group dev
test: ## Run source, renderer and installation contracts.
	@$(PYTHON) -m unittest discover -s tests
test-browser: ## Run reader, keyboard, accessibility and print journeys; requires pinned Playwright browsers.
	@$(PYTHON) tests/run_browser.py
	@$(PYTHON) tests/run_catalog_browser.py
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
