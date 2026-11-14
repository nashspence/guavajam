# Publisher command recipes owned and shipped by guavajam-core.
.PHONY: check check-source check-assets work capture asset-credentials site-template image asset-upload assets-gc asset-retire assets-policy-plan assets-policy-apply publication-check chat-list chat-gfm gateway-plan gateway-deploy archive-export archive-check asset-pin publication-prepare site-export
export TITLE AUTHOR LANGUAGE
export FILE REV WORK OUTPUT TRUSTED_GPG_FINGERPRINT SSH_ALLOWED_SIGNERS S3_ENV BRANCH HEAD_SHA APPLY ASSET_APP KEY RETAIN_UNTIL RETENTION_MODE CONVERSATION TIP METADATA CLOUDFLARE_ENV ASSET_WORKER PUBLISHED LABEL SOURCE_REF PREVIOUS_CATALOG REPO REQUIRE_RETENTION

check: check-source check-assets ## Validate tracked sources and verify their assets online. Stage new files first.

check-source: ## Validate tracked GFM manuscripts and supporting sources offline.
	@$(PYTHON) -m guavajam_core.validate_sources

check-assets: ## Download and verify referenced assets.
	@$(PYTHON) -m guavajam_core.verify_source_assets

work: ## WORK=slug TITLE='Work title' AUTHOR='Author name' [LANGUAGE=en] Create a new draft using the installed core starter.
	@$(PYTHON) -m guavajam_core new-work "$${WORK:?Set WORK}" --title "$${TITLE:?Set TITLE}" --author "$${AUTHOR:?Set AUTHOR}" --language "$${LANGUAGE:-en}"

capture: ## OUTPUT=new-directory Export the installed capture extension for loading unpacked in Chrome.
	@$(PYTHON) -m guavajam_core resources capture --output "$${OUTPUT:?Set OUTPUT}"

asset-credentials: ## OUTPUT=.env.assets Write a new credential template; fill it locally and keep it owner-only.
	@$(PYTHON) -m guavajam_core resources credentials --output "$${OUTPUT:-.env.assets}"

site-template: ## [OUTPUT=.build/press-site.json] Export an unsigned publisher settings starter for review.
	@mkdir -p "$$(dirname "$${OUTPUT:-.build/press-site.json}")"
	@$(PYTHON) -m guavajam_core resources site-settings --output "$${OUTPUT:-.build/press-site.json}"

image: ## FILE=asset  Print its content-addressed key and URL. Validated images retain their type; other files use opaque .bin downloads.
	@$(PYTHON) -m guavajam_core.prepare_asset -- "$${FILE:?Set FILE to the asset path}"

asset-upload: ## FILE=asset [S3_ENV=.env.assets]  Validate, upload, pin and verify original image, MP3, MP4 or WebVTT bytes. Other files are bounded opaque downloads. Create the owner-only credential template with make asset-credentials.
	@$(PYTHON) -m guavajam_core.manage_assets upload -- "$${FILE:?Set FILE to the asset path}"

assets-gc: ## [APPLY=1]  Dispatch a cleanup preview on main. For APPLY=1, pause uploads, lease renewals, and branch/PR changes until the queued job finishes.
	@$(PYTHON) -m guavajam_core.asset_workflow request-gc

asset-retire: ## BRANCH=feature/name HEAD_SHA=40-character-sha  Dispatch exact-tip branch retirement on main.
	@$(PYTHON) -m guavajam_core.asset_workflow request-retire

assets-policy-plan: ## ASSET_APP=app-slug  Preview deletion-only branch protection; requires independent no-bypass main protection.
	@$(PYTHON) -m guavajam_core.configure_asset_maintenance

assets-policy-apply: ## ASSET_APP=app-slug  Activate the previewed branch-deletion ruleset using the operator's gh credentials.
	@$(PYTHON) -m guavajam_core.configure_asset_maintenance --apply

publication-check: ## REV=full-sha TRUSTED_GPG_FINGERPRINT=hex|SSH_ALLOWED_SIGNERS=external-file  Verify signed source and all referenced bytes. Never signs or publishes.
	@set --; if [ -n "$$SSH_ALLOWED_SIGNERS" ]; then set -- --ssh-allowed-signers "$$SSH_ALLOWED_SIGNERS"; else set -- --trusted-gpg-fingerprint "$${TRUSTED_GPG_FINGERPRINT:?Set independent signer trust}"; fi; \
	$(PYTHON) -m guavajam_core.publication_commit --revision "$${REV:?Set REV to the full signed commit SHA}" "$$@"

chat-list: ## FILE=../export.zip  List conversation IDs and titles locally. Also accepts conversations JSON.
	@$(PYTHON) -m guavajam_core.extract_chatgpt --list -- "$${FILE:?Set FILE to the export path}"

chat-gfm: ## FILE=../export.zip CONVERSATION=id OUTPUT=../review.md [TIP=node-id] [METADATA=../fields.yml]  Convert one branch to anchored GFM for review, reporting losses. OUTPUT must be new and outside the checkout; no commit is made.
	@$(PYTHON) -m guavajam_core.extract_chatgpt --conversation="$${CONVERSATION:?Set CONVERSATION to the conversation ID}" \
		--output="$${OUTPUT:?Set OUTPUT to a new file path}" $${TIP:+"--tip=$$TIP"} $${METADATA:+"--metadata=$$METADATA"} -- "$${FILE:?Set FILE to the export path}"

gateway-plan: ## Preview authenticated, identity-pinned asset routing without reading credentials or making API calls.
	@$(PYTHON) -m guavajam_core.deploy_asset_gateway

gateway-deploy: ## Apply the asset gateway plan to Cloudflare. Same arguments as gateway-plan.
	@$(PYTHON) -m guavajam_core.deploy_asset_gateway --apply


archive-export: ## REV=full-sha OUTPUT=new-directory TRUSTED_GPG_FINGERPRINT=hex|SSH_ALLOWED_SIGNERS=path  Preserve signed Git objects and one copy per referenced asset outside the checkout.
	@set --; if [ -n "$$SSH_ALLOWED_SIGNERS" ]; then set -- --ssh-allowed-signers "$$SSH_ALLOWED_SIGNERS"; else set -- --trusted-gpg-fingerprint "$${TRUSTED_GPG_FINGERPRINT:?Set independent signer trust}"; fi; \
	$(PYTHON) -m guavajam_core.source_archive export --revision "$${REV:?Set REV}" --directory "$${OUTPUT:?Set OUTPUT}" "$$@"
archive-check: ## REV=independently-retained-sha OUTPUT=archive-directory TRUSTED_GPG_FINGERPRINT=hex|SSH_ALLOWED_SIGNERS=path  Restore Git objects and verify every saved dependency against external signer trust.
	@set --; if [ -n "$$SSH_ALLOWED_SIGNERS" ]; then set -- --ssh-allowed-signers "$$SSH_ALLOWED_SIGNERS"; else set -- --trusted-gpg-fingerprint "$${TRUSTED_GPG_FINGERPRINT:?Set independent signer trust}"; fi; \
	$(PYTHON) -m guavajam_core.source_archive check --revision "$${REV:?Set REV}" --directory "$${OUTPUT:?Set OUTPUT}" "$$@"
asset-pin: ## KEY=sha256/digest.type [RETAIN_UNTIL=epoch-ms RETENTION_MODE=governance|compliance]  Hash a stored object, inspect its bytes, optionally extend explicit retention, and publish its private pin sidecar.
	@set --; if [ -n "$$RETAIN_UNTIL$$RETENTION_MODE" ]; then set -- --retain-until "$${RETAIN_UNTIL:?Set an explicit retention time}" --retention-mode "$${RETENTION_MODE:?Set an explicit retention mode}"; fi; \
	$(PYTHON) -m guavajam_core.asset_versions "$${KEY:?Set KEY}" "$$@"

publication-prepare: ## REV=full-draft-sha WORK=slug PUBLISHED=YYYY-MM-DD [LABEL=edition S3_ENV=.env.assets REQUIRE_RETENTION=1]  Verify committed source, complete public bytes and private object identity before the owner manually signs an empty publication commit.
	@set --; case "$${REQUIRE_RETENTION:-0}" in 1) set -- --require-retention;; 0) ;; *) echo "REQUIRE_RETENTION must be 0 or 1" >&2; exit 1;; esac; \
	$(PYTHON) -m guavajam_core.prepare_publication --revision "$${REV:?Set REV}" --work "$${WORK:?Set WORK}" --published "$${PUBLISHED:?Set PUBLISHED to the reviewed edition date}" $${LABEL:+"--label=$$LABEL"} "$$@"

site-export: ## [SOURCE_REF=refs/heads/main OUTPUT=.build/guavajam/CATALOG.json] external signer trust as publication-check; emit exact GuavaJam input from signed commits and press-site.json.
	@test -z "$$SSH_ALLOWED_SIGNERS" -o -z "$$TRUSTED_GPG_FINGERPRINT" || { echo 'Choose exactly one external trust policy.' >&2; exit 1; }
	@set --; if [ -n "$$SSH_ALLOWED_SIGNERS" ]; then set -- --ssh-allowed-signers "$$SSH_ALLOWED_SIGNERS"; else set -- --trusted-gpg-fingerprint "$${TRUSTED_GPG_FINGERPRINT:?Set independent signer trust}"; fi; \
	$(PYTHON) -m guavajam_core.export_site $${REPO:+"--repo=$$REPO"} --ref "$${SOURCE_REF:-refs/heads/main}" --output "$${OUTPUT:-.build/guavajam/CATALOG.json}" $${PREVIOUS_CATALOG:+"--previous-catalog=$$PREVIOUS_CATALOG"} "$$@"
