.DEFAULT_GOAL := help
.PHONY: help setup test test-browser render
PYTHON := mise exec -- uv run --locked --group dev python
export REPO REV WORK OUTPUT TRUSTED_GPG_FINGERPRINT SSH_ALLOWED_SIGNERS PREVIEW
help: ## Show commands.
	@awk -F ':.*## ' '/^[a-z][a-z-]*:.*## / {printf "  %-16s %s\n", $$1, $$2}' Makefile
setup: ## Install pinned project tools and dependencies.
	mise install
	mise exec -- uv sync --locked --group dev
test: ## Run source, renderer and installation contracts.
	@$(PYTHON) -m unittest discover -s tests
test-browser: ## Run reader, keyboard, accessibility and print journeys; requires pinned Playwright browsers.
	@$(PYTHON) tests/run_browser.py
render: ## REPO=path REV=full-sha WORK=slug OUTPUT=new-directory TRUSTED_GPG_FINGERPRINT=fingerprint|SSH_ALLOWED_SIGNERS=path [PREVIEW=1]
	@set --; if [ "$$PREVIEW" = 1 ]; then set -- --unsigned-preview; elif [ -n "$$SSH_ALLOWED_SIGNERS" ]; then set -- --ssh-allowed-signers "$$SSH_ALLOWED_SIGNERS"; else set -- --trusted-gpg-fingerprint "$${TRUSTED_GPG_FINGERPRINT:?Set external signer trust or PREVIEW=1}"; fi; \
	$(PYTHON) -m guavajam --repo "$${REPO:?Set REPO}" --revision "$${REV:?Set REV}" --work "$${WORK:?Set WORK}" --output "$${OUTPUT:?Set OUTPUT}" "$$@"
