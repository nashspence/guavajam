# Press

An independent publishing imprint under the authority of Nash Spence.

Cite the HTML attachment named in an official edition’s release notes. [Editions and notices](https://github.com/nashspence/press/releases) · [Copyright and permissions](COPYRIGHT.md)

Start with the [manuscript template](templates/work/manuscript.md) at `works/<slug>/manuscript.md`. Run `make help` for commands. Retain section IDs when headings change. Work privately if a prospective publisher requires first publication.

Issue official editions through the [publication workflow](.github/workflows/prepare-edition.yml) after reviewing the HTML and rights. Normally start at `v1.0.0`. Bump major for a substantially new edition, minor for a meaningful revision, and patch for corrections. Record corrections, supersessions, and withdrawals in the release notes.

Confirm with the issuer which work, edition, format, or identity each external identifier names. [Registration schema](scripts/identifier_schema.py) · [Asset storage](storage/README.md)
