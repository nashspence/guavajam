# Press

An independent publishing imprint under the authority of Nash Spence.

Cite the issued HTML edition. Use the ¶ link beside a heading to copy the permanent address of a theorem or passage, including from a downloaded copy. Follow “Corrections and withdrawal status” to check the current editorial record. [Edition catalog](https://github.com/nashspence/press/releases) · [Copyright and permissions](COPYRIGHT.md)

Start with the [manuscript template](templates/work/manuscript.md) at `works/<slug>/manuscript.md`. Run `make help` for commands. Retain section IDs when headings change. This public workflow requires the manuscript in public main history before staging and keeps downloadable review artifacts for 90 days. Keep embargoed material private until that disclosure is authorized.

## Cite a work across editions

The permanent address for a work as a whole is `https://press.nash.spence.0819870.xyz/works/<slug>`, without a trailing slash. Keep the slug for the life of the work, including after a title change. Use this address to refer to the work across editions. To cite a particular text, choose its numbered edition and use the reading copy or its section permalink.

The work page first appears when the issuer merges a publication receipt. Each subsequent receipt adds an edition with its reviewed title and publication date, ordered by numeric version, highest first. Each entry links to the edition record with its corrections, supersession and withdrawal notices. Withdrawn editions remain listed, and the work address stays available even if every edition is withdrawn.

If the issuer later registers a concept DOI, use this permanent work address as its landing URL. First verify the work page and its edition links on the publisher origin and on the recovery host. Record an assigned concept DOI at work scope. A DOI for an individual edition belongs to that edition and points to its versioned record.

## Issue an edition

Before staging the first work, deploy the publisher-controlled HTTPS origin in `edition-origin.txt`. Each `/works/<slug>/vX.Y.Z` address leads to the edition record. Its `/read` address serves the authorized HTML inline. Keep control of this origin. Enable GitHub immutable releases. Install a GitHub App with only Administration read access on this repository and configure the protected `press-release` environment with its `PRESS_POLICY_APP_CLIENT_ID` variable and `PRESS_POLICY_APP_PRIVATE_KEY` secret. Promotion creates a short-lived token restricted to this repository, checks the live policy before issuing the draft, and revokes the token when the job finishes. Release writes use the built-in workflow token.

The [record gateway](records/worker.js) serves each receipted edition’s checked-in public record and streams the immutable reading copy. During initial readback, the record temporarily points to the verified GitHub Release until the receipt and generated page merge. Deploy the current Worker before issuing an edition. Its operator deployment uses `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_ZONE_ID`. Consult `make help` for deployment arguments. The origin must permit noninteractive public reads for publication and archive verification. If Cloudflare challenges the verifier, configure a supported exception or change the bot setting; [free Bot Fight Mode has no hostname exceptions](https://developers.cloudflare.com/bots/get-started/bot-fight-mode/).

1. Put edition-specific rights and exceptions in `works/<slug>/editions/vX.Y.Z.rights.txt` as one line of plain text. The statement travels with the HTML and appears in the release notes. Put other initial release prose in `works/<slug>/editions/vX.Y.Z.md` as plain-text paragraphs. Provenance preserves the exact reviewed initial release body.
2. Stage a public-safe proof through the [publication workflow](.github/workflows/prepare-edition.yml). Download `verified-edition` and `edition-review`. Read the HTML, rights and complete notes. Independently recompute the authorization from those files using the staging run ID, and compare it with the one supplied. If the proof needs revision, stage the corrected source under the same intended version.
3. Once ready to issue, merge the exact reviewed `authorization.json` at `registrations/editions/<slug>/vX.Y.Z.json`. The required check downloads the named run’s proof and verifies the registration before merge permanently reserves that version. If the proof expires before reservation, stage and review a new proof. Promote from current `main` with the original source SHA, tag, staging run ID and reviewed provenance digest. If the proof artifact expires after reservation, select the workflow’s rebuild option to reproduce it with the original builder and verify the same digest.
4. After public readback, copy the four files from `edition-receipt` into the checkout at their repository-relative paths. Merge the publication receipt at the registration path, the edition page at `records/editions/<slug>/vX.Y.Z/index.html`, its verified `provenance.json`, and the work page at `records/works/<slug>/index.html` together. If another receipt for the work has merged in the meantime, bring the checkout up to date and regenerate the pages so both editions are listed. If the artifact is unavailable, regenerate the receipt from the public edition, then generate both pages from the verified package and current checkout.

The full archive audit reports an unresolved intent until the record is reconciled. To cancel an unissued intent, stop outstanding promotions, inspect and remove any unissued draft, verify the tag and public release are absent, then add a `cancellation_reason` to the registration. The version stays retired.

The issuer is responsible for keeping the independent editorial backup current. For each edition, preserve the latest receipt, numbered notice history, generated edition page and three edition files, including after withdrawal. After the receipt and each subsequent editorial change, retain the edition’s editorial checkpoint from the approved main commit in a separate trusted location. For each work, also preserve its generated page and retain its SHA-256 digest independently whenever the page changes.

At a recovery host, serve both generated pages at their exact work and edition paths without trailing slashes. Serve the unchanged edition HTML inline at its `/read` path and the three named attachments directly beneath the edition path. Before switching the gateway, verify each edition directly on the backup host against its independently retained checkpoint. Include the work-page digest when checking at least one edition of each work. After cutover set the Actions variable `RECORD_TARGET` to the same base and repeat these checks through the permanent URLs. Republish changed pages after each receipt or editorial update. The GitHub archive audit continues to report loss of the primary release independently.

Keep workflow files compatible between staging and publication. A change after reservation can still require retiring an unissued intent. After issuance, GitHub mirror writes can still fail because of permissions or service availability. The current publisher record is served from the merged page even if the GitHub mirror requires an operator update. [GitHub release API restrictions](https://docs.github.com/en/rest/releases/releases#update-a-release)

Normally start at `v1.0.0`. Bump major for a substantially new edition, minor for a meaningful revision, and patch for corrections.

## Publish an editorial notice

Append the next numbered file at `notices/<slug>/vX.Y.Z/0001.md` and regenerate the edition record in the same change. Each file contains one dated line, for example `2026-11-04 Correction: The proof in § 2 cites Theorem 3.` Use `Supersession` or `Withdrawal` for those decisions and identify any replacement edition.

Confirm with the issuer which work, edition, format, or identity each external identifier names. Add edition identifiers after the receipt is committed. When replacing or retiring an assigned identifier, add the dated correction required by the [history check](scripts/check_editorial_history.py). Regenerate the public record whenever edition identifiers change. The [editorial synchronization workflow](.github/workflows/sync-editorial-record.yml) checks the publisher’s record and reading copy after merge, then mirrors the approved notes to GitHub Releases. If it fails, inspect the failure and retry the edition from current main. The [archive audit](.github/workflows/audit-assets.yml) repeats the public checks.

[Registration schema](scripts/identifier_schema.py) · [Asset storage](storage/README.md)
