# Development and package boundaries

`mise.toml` pins Python, uv, Node and Pandoc. `make setup` installs the shared uv workspace environment from `uv.lock`.

`projects/core` builds **guavajam-core** (`guavajam_core`, `guavajam-core`). It owns the Press source/signature protocols, repository validation, discovery/catalog data, authoring/capture, assets and preservation. Resources ship inside the distribution. Commands find the caller's Git root. The installed `commands.mk` supplies publisher Make targets; Press includes it rather than copying recipes. Use `guavajam-core new-work SLUG --title TITLE --author AUTHOR` to create a draft, and `guavajam-core resources --help` to export the capture extension, credential example or publisher-settings starter.

`projects/view` builds **guavajam-view** (`guavajam`, `guavajam`). It depends on the exact matching core version and owns HTML, CSS, rendering, catalogs, citations, feeds, cache and atomic updates. Core has no view dependency. The shared lock and editable development installation do not define the distribution boundary: CI installs each wheel outside the checkout, exercises a synthetic signed publisher, and rebuilds both source distributions independently.

[Asset storage configuration and guarantees](storage.md) covers S3 endpoints, optional versioning/retention, and maintenance.

`make test`, `make test-browser`, and `make test-capture-browser` run the contracts. `make check-artifacts` builds/verifies both distributions without publication. Browser checks use the pinned npm lock and Playwright engines.

Both packages carry the same version. `make version VERSION=x.y.z` updates both metadata files, the view's exact core dependency and the capture manifest, then updates the lock. CI rejects inconsistent versions. The manually dispatched release-preparation workflow checks an explicit version and retains selected wheels/source distributions and SHA-256 sums. It never creates tags or publishes. Package publication, registry credentials and release tags are supplied when an actual release is requested.

Before a registry release, consumers such as Press pin `guavajam-core` to a full GuavaJam Git commit and `projects/core` through `tool.uv.sources`; `uv.lock` records the resolved commit. The dependency has no path to a sibling checkout or floating branch. After a registry release, consumers can replace the Git source with a reviewed distribution version. Source protocol identifiers (`Press-Publication`, `Press-Site`, catalog schemas) are independent of software versions and remain v1 while unreleased. Implement the current contract directly, without migrations or compatibility adapters.

The workspace-only `tests/support` package owns shared signed-publisher and codec fixture builders. Both suites read the single corpus in `tests/fixtures` and the actual installed authoring resources. Test support is excluded from runtime distributions. `make check-boundaries` checks dependency/import direction and implementation ownership; isolated core artifact tests assert the view is unavailable.
