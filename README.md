# Press

An independent publishing imprint under the authority of Nash Spence.

Cite the HTML attachment named in an official edition’s release notes. [Editions and notices](https://github.com/nashspence/press/releases) · [Copyright and permissions](COPYRIGHT.md)

Start with the [manuscript template](templates/work/manuscript.md) at `works/<slug>/manuscript.md`. Run `make help` for commands. Retain section IDs when headings change. This public workflow requires the manuscript in public main history before staging and keeps downloadable review artifacts for 90 days. Keep embargoed material private until that disclosure is authorized.

## Issue an edition

1. Put any edition-specific rights terms or other initial release prose at `works/<slug>/editions/vX.Y.Z.md` before staging. Stage a public-safe draft through the [publication workflow](.github/workflows/prepare-edition.yml). Download `verified-edition` and `edition-review` from that run. Read the HTML, rights and complete release notes. Independently verify that the hashes in `authorization.json` match the files you reviewed.
2. Merge the reviewed authorization into `main` at `registrations/editions/<slug>/vX.Y.Z.json`. This later commit authorizes the original source. Keep the original source SHA, tag, staging run ID and reviewed provenance digest for promotion.
3. Dispatch the workflow from current `main` with publication enabled and those original inputs. After public readback succeeds, add GitHub’s exact `published_at` UTC timestamp to the registration in a follow-up commit. Retain the registration and an independent copy of the three edition files, including after withdrawal.

Normally start at `v1.0.0`. Bump major for a substantially new edition, minor for a meaningful revision, and patch for corrections.

## Publish an editorial notice

Append the next numbered file at `notices/<slug>/vX.Y.Z/0001.md` and merge it. Each file contains one dated line, for example `2026-11-04 Correction: The proof in § 2 cites Theorem 3.` Use `Supersession` or `Withdrawal` for those decisions and identify any replacement edition. Render the complete release body from the current checkout and the original verified edition, then update the release notes with that exact text. Run the [archive audit](.github/workflows/audit-assets.yml) to check the public copy.

Confirm with the issuer which work, edition, format, or identity each external identifier names. [Registration schema](scripts/identifier_schema.py) · [Asset storage](storage/README.md)
