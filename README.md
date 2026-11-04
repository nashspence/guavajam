# Press

An independent publishing imprint for original works by Nash Spence. Browse the [catalog](CATALOG.md) or read the [copyright and permissions notice](COPYRIGHT.md).

## Start a manuscript

Copy the [starter manuscript](templates/work/manuscript.md) into `works/<slug>/manuscript.md`. Edit its front matter, title, and text. Give each section a stable anchor and keep that ID when renaming the heading so citations keep working. Keep drafts private elsewhere if a prospective publisher requires first publication.

For figures, follow the [asset upload guide](ASSETS.md). For an assigned DOI, ISBN, or other external identifier, see [registrations](REGISTRATIONS.md).

## Check your work

Install the Pandoc version used by [source CI](.github/workflows/validate-source.yml). From the repository root, stage new source files so the validator can see them, then run these checks.

```sh
mise install
mise x -- uv run --locked python scripts/validate_sources.py
mise x -- uv run --locked python scripts/verify_source_assets.py
```

When the manuscript is ready to issue, follow the [publication procedure](PUBLICATION.md).
