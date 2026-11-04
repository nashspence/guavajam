# Press

An independent publishing imprint under the authority of Nash Spence.

Cite the HTML attachment named in an official edition’s release notes. [Editions and notices](https://github.com/nashspence/press/releases) · [Copyright and permissions](COPYRIGHT.md)

Start with the [manuscript template](templates/work/manuscript.md) at `works/<slug>/manuscript.md`. Run `make help` for commands. Retain section IDs when headings change. Keep embargoed manuscripts and builds in a private repository. A staging run in this public repository makes the HTML downloadable as a workflow artifact for 90 days.

Stage a draft through the [publication workflow](.github/workflows/prepare-edition.yml). Download the staging artifact and review the HTML, rights, and release notes. To issue it, run the workflow again with publication enabled, the same source commit and tag, the successful staging run ID, and the SHA-256 of the `provenance.json` you reviewed. The staging summary records both the run ID and digest.

Normally start at `v1.0.0`. Bump major for a substantially new edition, minor for a meaningful revision, and patch for corrections. Record corrections, supersessions, and withdrawals in the release notes.

Confirm with the issuer which work, edition, format, or identity each external identifier names. [Registration schema](scripts/identifier_schema.py) · [Asset storage](storage/README.md)
