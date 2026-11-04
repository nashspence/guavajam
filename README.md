# Press

An independent publishing imprint under the authority of Nash Spence.

Cite the HTML attachment named in an official edition’s release notes. Its footer gives the permanent address for the edition record and subsequent notices. [Editions and notices](https://github.com/nashspence/press/releases) · [Copyright and permissions](COPYRIGHT.md)

Start with the [manuscript template](templates/work/manuscript.md) at `works/<slug>/manuscript.md`. Run `make help` for commands. Retain section IDs when headings change. This public workflow requires the manuscript in public main history before staging and keeps downloadable review artifacts for 90 days. Keep embargoed material private until that disclosure is authorized.

## Issue an edition

Before staging the first work, commit the publisher-controlled HTTPS origin in `edition-origin.txt`. Arrange for its `/works/<slug>/vX.Y.Z` addresses to resolve to edition records. A redirect to the matching GitHub Release is sufficient. Keep control of that address and enable immutable releases on the publishing repository.

The [record gateway](records/worker.js) redirects the configured edition addresses to GitHub Releases. Its operator deployment uses `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_ZONE_ID`. Change the archive target to restore access at a second location while retaining the issued address; redirects are temporary and uncached. Consult `make help` for deployment arguments.

1. Put edition-specific rights and exceptions in `works/<slug>/editions/vX.Y.Z.rights.txt` as one line of plain text. The statement travels with the HTML and appears in the release notes. Other initial release prose belongs in `works/<slug>/editions/vX.Y.Z.md`.
2. Stage a public-safe proof through the [publication workflow](.github/workflows/prepare-edition.yml). Download `verified-edition` and `edition-review`. Read the HTML, rights and complete notes. Independently compare the authorization with the files you reviewed. If the proof needs revision, stage the corrected source under the same intended version.
3. Once ready to issue, merge the exact reviewed `authorization.json` at `registrations/editions/<slug>/vX.Y.Z.json`. This commit permanently reserves the version for those bytes. Promote from current `main` with the original source SHA, tag, staging run ID and reviewed provenance digest. If the approved proof artifact has expired, select the workflow’s rebuild option to reproduce it with the original builder and verify the same digest.
4. After public readback, commit `registration.json` from the promotion’s `edition-receipt` artifact over the pending registration. If that artifact is unavailable, regenerate the receipt from the public edition. The full archive audit reports an unresolved intent until the record is reconciled. To cancel an unissued intent, stop outstanding promotions, inspect and remove any unissued draft, verify the tag and public release are absent, then add a `cancellation_reason` to the registration. The version stays retired.

Keep an independent copy of the three edition files, the receipted registration and the notice history, including after withdrawal. To restore public access, verify the recovered package against that trusted receipt, restore the record and notices, and point the permanent address to the recovered location. Preserve the original publication timestamp.

Normally start at `v1.0.0`. Bump major for a substantially new edition, minor for a meaningful revision, and patch for corrections.

## Publish an editorial notice

Append the next numbered file at `notices/<slug>/vX.Y.Z/0001.md` and merge it. Each file contains one dated line, for example `2026-11-04 Correction: The proof in § 2 cites Theorem 3.` Use `Supersession` or `Withdrawal` for those decisions and identify any replacement edition.

Confirm with the issuer which work, edition, format, or identity each external identifier names. Add edition identifiers after the receipt is committed. When replacing or retiring an assigned identifier, add the dated correction required by the [history check](scripts/check_editorial_history.py). After any notice or identifier change, render the full release body from the current checkout and the original verified edition, update the release notes with the exact text, and run the [archive audit](.github/workflows/audit-assets.yml).

[Registration schema](scripts/identifier_schema.py) · [Asset storage](storage/README.md)
