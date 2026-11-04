# Press

An independent publishing imprint under the authority of Nash Spence.

Cite the HTML edition named in the official release notes. The footer links to its permanent record and its online reading copy. Append a section ID to the reading URL to cite a theorem or passage. [Editions and notices](https://github.com/nashspence/press/releases) · [Copyright and permissions](COPYRIGHT.md)

Start with the [manuscript template](templates/work/manuscript.md) at `works/<slug>/manuscript.md`. Run `make help` for commands. Retain section IDs when headings change. This public workflow requires the manuscript in public main history before staging and keeps downloadable review artifacts for 90 days. Keep embargoed material private until that disclosure is authorized.

## Issue an edition

Before staging the first work, deploy the publisher-controlled HTTPS origin in `edition-origin.txt`. Each `/works/<slug>/vX.Y.Z` address leads to the edition record. Its `/read` address serves the authorized HTML inline. Keep control of this origin. Enable GitHub immutable releases. Install a GitHub App with only Administration read access on this repository and configure the protected `press-release` environment with its `PRESS_POLICY_APP_CLIENT_ID` variable and `PRESS_POLICY_APP_PRIVATE_KEY` secret. Promotion creates a short-lived token restricted to this repository, checks the live policy before issuing the draft, and revokes the token when the job finishes. Release writes use the built-in workflow token.

The [record gateway](records/worker.js) redirects edition records to GitHub Releases and serves the matching immutable attachment for readers. Its operator deployment uses `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_ZONE_ID`. Consult `make help` for deployment arguments. The origin must permit noninteractive public reads for publication and archive verification. If Cloudflare challenges the verifier, configure a supported exception or change the bot setting; [free Bot Fight Mode has no hostname exceptions](https://developers.cloudflare.com/bots/get-started/bot-fight-mode/).

1. Put edition-specific rights and exceptions in `works/<slug>/editions/vX.Y.Z.rights.txt` as one line of plain text. The statement travels with the HTML and appears in the release notes. Put other initial release prose in `works/<slug>/editions/vX.Y.Z.md` as plain-text paragraphs. Provenance preserves the exact reviewed initial release body.
2. Stage a public-safe proof through the [publication workflow](.github/workflows/prepare-edition.yml). Download `verified-edition` and `edition-review`. Read the HTML, rights and complete notes. Independently compare the authorization with the files you reviewed. If the proof needs revision, stage the corrected source under the same intended version.
3. Once ready to issue, merge the exact reviewed `authorization.json` at `registrations/editions/<slug>/vX.Y.Z.json`. This commit permanently reserves the version for those bytes. Promote from current `main` with the original source SHA, tag, staging run ID and reviewed provenance digest. If the approved proof artifact has expired, select the workflow’s rebuild option to reproduce it with the original builder and verify the same digest.
4. After public readback, commit `registration.json` from the promotion’s `edition-receipt` artifact over the pending registration. If that artifact is unavailable, regenerate the receipt from the public edition. The full archive audit reports an unresolved intent until the record is reconciled. To cancel an unissued intent, stop outstanding promotions, inspect and remove any unissued draft, verify the tag and public release are absent, then add a `cancellation_reason` to the registration. The version stays retired.

Keep an independent copy of the three edition files, the receipted registration and the notice history, including after withdrawal. At a recovery host, serve the public record at the same edition path and the unchanged HTML inline at its `/read` path. Keep the permanent reading link, edition identity, original publication timestamp, current identifiers and dated notices visible in the record. Point the gateway at the recovered archive, set the Actions repository variable `RECORD_TARGET` to the same base, then verify both public addresses against the trusted receipt and preserved notices.

Keep workflow files unchanged between staging and publication. GitHub restricts the built-in workflow token from creating or editing a release whose target has different `.github/workflows/` files from current `main`. If those files change after authorization, retire the unissued intent before staging a replacement. Changes after publication can also block automatic notice updates; the issuer must then perform the update with a credential authorized to modify workflows. See [GitHub's release API restrictions](https://docs.github.com/en/rest/releases/releases#update-a-release).

Normally start at `v1.0.0`. Bump major for a substantially new edition, minor for a meaningful revision, and patch for corrections.

## Publish an editorial notice

Append the next numbered file at `notices/<slug>/vX.Y.Z/0001.md` and merge it. Each file contains one dated line, for example `2026-11-04 Correction: The proof in § 2 cites Theorem 3.` Use `Supersession` or `Withdrawal` for those decisions and identify any replacement edition.

Confirm with the issuer which work, edition, format, or identity each external identifier names. Add edition identifiers after the receipt is committed. When replacing or retiring an assigned identifier, add the dated correction required by the [history check](scripts/check_editorial_history.py). The [editorial synchronization workflow](.github/workflows/sync-editorial-record.yml) updates the affected GitHub Release after merge and checks what readers can see. If it fails, inspect the failure and retry the edition from current main. The [archive audit](.github/workflows/audit-assets.yml) repeats the public checks.

[Registration schema](scripts/identifier_schema.py) · [Asset storage](storage/README.md)
