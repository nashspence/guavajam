# Publisher command recipes owned and shipped by guavajam.
.PHONY: check check-source check-assets capture asset-credentials image asset-upload assets-gc asset-retire assets-policy-plan assets-policy-apply publication-check chat-list chat-gfm gateway-plan gateway-deploy archive-export archive-check asset-pin publication-prepare catalog-export assets-qualify assets-release
export TITLE AUTHOR LANGUAGE
export FILE REV OUTPUT TRUSTED_GPG_FINGERPRINT SSH_ALLOWED_SIGNERS S3_ENV S3_RELEASE_ENV BRANCH HEAD_SHA APPLY ASSET_APP KEY RETAIN_UNTIL RETENTION_MODE CONVERSATION TIP METADATA CLOUDFLARE_ENV ASSET_WORKER SOURCE_REF PREVIOUS_CATALOG REPO REQUIRE_RETENTION MAIN_SHA REMOTE

check: check-source check-assets ## Validate public source/assets. Use assets-qualify for committed private drafts.

check-source: ## Validate tracked GFM manuscripts and supporting sources offline.
	@$(PYTHON) -m guavajam.validate_sources

check-assets: ## Verify anonymous public delivery after release; private drafts use assets-qualify.
	@$(PYTHON) -m guavajam.verify_source_assets

capture: ## OUTPUT=new-directory Export the installed capture extension for loading unpacked in Chrome.
	@$(PYTHON) -m guavajam resources capture --output "$${OUTPUT:?Set OUTPUT}"

asset-credentials: ## OUTPUT=.env.assets Write a new credential template; fill it locally and keep it owner-only.
	@$(PYTHON) -m guavajam resources credentials --output "$${OUTPUT:-.env.assets}"


image: ## FILE=asset  Print its content-addressed key and URL. Validated images retain their type; other files use opaque .bin downloads.
	@$(PYTHON) -m guavajam.prepare_asset -- "$${FILE:?Set FILE to the asset path}"

asset-upload: ## FILE=asset [S3_ENV=.env.assets]  Stage and privately verify bytes and integrity pins. Never makes assets public; canonical URLs remain unchanged.
	@$(PYTHON) -m guavajam.manage_assets upload -- "$${FILE:?Set FILE to the asset path}"

assets-gc: ## [APPLY=1]  Dispatch a cleanup preview on main. For APPLY=1, pause uploads, releases, lease renewals, and branch/PR changes until the queued job finishes.
	@$(PYTHON) -m guavajam.asset_workflow request-gc

asset-retire: ## BRANCH=feature/name HEAD_SHA=40-character-sha  Dispatch exact-tip branch retirement on main.
	@$(PYTHON) -m guavajam.asset_workflow request-retire

assets-policy-plan: ## ASSET_APP=app-slug  Preview deletion-only branch protection; requires independent no-bypass main protection.
	@$(PYTHON) -m guavajam.configure_asset_maintenance

assets-policy-apply: ## ASSET_APP=app-slug  Activate the previewed branch-deletion ruleset using the operator's gh credentials.
	@$(PYTHON) -m guavajam.configure_asset_maintenance --apply

publication-check: ## REV=full-sha TRUSTED_GPG_FINGERPRINT=hex|SSH_ALLOWED_SIGNERS=external-file  Verify signed source and all referenced bytes. Never signs or publishes.
	@set --; if [ -n "$$SSH_ALLOWED_SIGNERS" ]; then set -- --ssh-allowed-signers "$$SSH_ALLOWED_SIGNERS"; else set -- --trusted-gpg-fingerprint "$${TRUSTED_GPG_FINGERPRINT:?Set independent signer trust}"; fi; \
	$(PYTHON) -m guavajam.publication_commit --revision "$${REV:?Set REV to the full signed commit SHA}" "$$@"

chat-list: ## FILE=../export.zip  List conversation IDs and titles locally. Also accepts conversations JSON.
	@$(PYTHON) -m guavajam.extract_chatgpt --list -- "$${FILE:?Set FILE to the export path}"

chat-gfm: ## FILE=../export.zip CONVERSATION=id OUTPUT=../review.md [TIP=node-id] [METADATA=../fields.yml]  Convert one branch to anchored GFM for review, reporting losses. OUTPUT must be new and outside the checkout; no commit is made.
	@$(PYTHON) -m guavajam.extract_chatgpt --conversation="$${CONVERSATION:?Set CONVERSATION to the conversation ID}" \
		--output="$${OUTPUT:?Set OUTPUT to a new file path}" $${TIP:+"--tip=$$TIP"} $${METADATA:+"--metadata=$$METADATA"} -- "$${FILE:?Set FILE to the export path}"

gateway-plan: ## Preview authenticated, identity-pinned asset routing without reading credentials or making API calls.
	@$(PYTHON) -m guavajam.deploy_asset_gateway

gateway-deploy: ## Apply the asset gateway plan to Cloudflare. Same arguments as gateway-plan.
	@$(PYTHON) -m guavajam.deploy_asset_gateway --apply


archive-export: ## REV=full-sha OUTPUT=new-directory TRUSTED_GPG_FINGERPRINT=hex|SSH_ALLOWED_SIGNERS=path  Preserve signed Git objects and one copy per referenced asset outside the checkout.
	@set --; if [ -n "$$SSH_ALLOWED_SIGNERS" ]; then set -- --ssh-allowed-signers "$$SSH_ALLOWED_SIGNERS"; else set -- --trusted-gpg-fingerprint "$${TRUSTED_GPG_FINGERPRINT:?Set independent signer trust}"; fi; \
	$(PYTHON) -m guavajam.source_archive export --revision "$${REV:?Set REV}" --directory "$${OUTPUT:?Set OUTPUT}" "$$@"
archive-check: ## REV=independently-retained-sha OUTPUT=archive-directory TRUSTED_GPG_FINGERPRINT=hex|SSH_ALLOWED_SIGNERS=path  Restore Git objects and verify every saved dependency against external signer trust.
	@set --; if [ -n "$$SSH_ALLOWED_SIGNERS" ]; then set -- --ssh-allowed-signers "$$SSH_ALLOWED_SIGNERS"; else set -- --trusted-gpg-fingerprint "$${TRUSTED_GPG_FINGERPRINT:?Set independent signer trust}"; fi; \
	$(PYTHON) -m guavajam.source_archive check --revision "$${REV:?Set REV}" --directory "$${OUTPUT:?Set OUTPUT}" "$$@"
asset-pin: ## KEY=sha256/digest.type [RETAIN_UNTIL=epoch-ms RETENTION_MODE=governance|compliance]  Hash a stored object, inspect its bytes, optionally extend explicit retention, and write a private integrity pin; never releases publicly.
	@set --; if [ -n "$$RETAIN_UNTIL$$RETENTION_MODE" ]; then set -- --retain-until "$${RETAIN_UNTIL:?Set an explicit retention time}" --retention-mode "$${RETENTION_MODE:?Set an explicit retention mode}"; fi; \
	$(PYTHON) -m guavajam.asset_versions "$${KEY:?Set KEY}" "$$@"

publication-prepare: ## REV=full-draft-sha [S3_ENV=.env.assets REQUIRE_RETENTION=1] Check exact committed content and private assets; never sign or release.
	@set --; case "$${REQUIRE_RETENTION:-0}" in 1) set -- --require-retention;; 0) ;; *) echo "REQUIRE_RETENTION must be 0 or 1" >&2; exit 1;; esac; \
	$(PYTHON) -m guavajam.prepare_publication --revision "$${REV:?Set REV}" "$$@"

catalog-export: ## [SOURCE_REF=refs/heads/main OUTPUT=.build/guavajam/CATALOG.json] external signer trust as publication-check; discover signed editions from first-parent Git history.
	@test -z "$$SSH_ALLOWED_SIGNERS" -o -z "$$TRUSTED_GPG_FINGERPRINT" || { echo 'Choose exactly one external trust policy.' >&2; exit 1; }
	@set --; if [ -n "$$SSH_ALLOWED_SIGNERS" ]; then set -- --ssh-allowed-signers "$$SSH_ALLOWED_SIGNERS"; else set -- --trusted-gpg-fingerprint "$${TRUSTED_GPG_FINGERPRINT:?Set independent signer trust}"; fi; \
	$(PYTHON) -m guavajam.export_catalog $${REPO:+"--repo=$$REPO"} --ref "$${SOURCE_REF:-refs/heads/main}" --output "$${OUTPUT:-.build/guavajam/CATALOG.json}" $${PREVIOUS_CATALOG:+"--previous-catalog=$$PREVIOUS_CATALOG"} "$$@"

assets-qualify: ## REV=full-draft-sha [S3_ENV=private-read.env REQUIRE_RETENTION=1] Verify committed draft dependencies without exposing them.
	@set --; case "$${REQUIRE_RETENTION:-0}" in 1) set -- --require-retention;; 0) ;; *) echo 'REQUIRE_RETENTION must be 0 or 1'; exit 1;; esac; \
	$(PYTHON) -m guavajam.qualify_assets --revision "$${REV:?Set exact REV}" "$$@"

assets-release: ## REV=signed-sha MAIN_SHA=exact-remote-main S3_RELEASE_ENV=release.env [APPLY=1 S3_ENV=private-read.env REQUIRE_RETENTION=1] External signer trust required; default read-only plan, APPLY=1 irreversibly authorizes public delivery.
	@test -z "$$SSH_ALLOWED_SIGNERS" -o -z "$$TRUSTED_GPG_FINGERPRINT" || { echo 'Choose exactly one external trust policy.'; exit 1; }
	@set --; if [ -n "$$SSH_ALLOWED_SIGNERS" ]; then set -- --ssh-allowed-signers "$$SSH_ALLOWED_SIGNERS"; else set -- --trusted-gpg-fingerprint "$${TRUSTED_GPG_FINGERPRINT:?Set external signer trust}"; fi; \
	case "$${REQUIRE_RETENTION:-0}" in 1) set -- "$$@" --require-retention;; 0) ;; *) echo 'REQUIRE_RETENTION must be 0 or 1'; exit 1;; esac; \
	case "$${APPLY:-0}" in 1) set -- "$$@" --apply;; 0) ;; *) echo 'APPLY must be 0 or 1'; exit 1;; esac; \
	$(PYTHON) -m guavajam.asset_release --revision "$${REV:?Set signed REV}" --release-env "$${S3_RELEASE_ENV:?Set separate release-prefix credential file}" --expected-main "$${MAIN_SHA:?Set exact MAIN_SHA}" --remote "$${REMOTE:-origin}" "$$@"
