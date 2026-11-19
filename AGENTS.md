# Working on GuavaJam

Use `make setup` for pinned mise/uv tools and `make help` for commands.

`README.md` and `AGENTS.md` are the only prose documentation on `main`; license texts are legal artifacts. Keep onboarding minimal, primarily linking to executable tests. Tests are authoritative; prose is limited to policy that cannot be encoded as executable checks. Put release guides with release documentation and rationale/verification history in issues/PRs.

- Licensing: [REUSE and package contracts](tests/repository/test_licensing.py); `make check-licenses` runs REUSE lint.
- Repository policy: [documentation boundary and onboarding links](tests/repository/test_policy.py), [GuavaJam v1 identifiers and repository configuration](tests/repository/test_namespace.py).
- Packages: [core independence and single implementation ownership](tools/check_boundaries.py), [coordinated versions](tools/version.py), [isolated distributions](tools/check_artifacts.py), [release preparation](.github/workflows/prepare-release.yml).
- Publication: [versioned work snapshots, ordinary commit messages and merge identity](projects/core/tests/test_edition_history.py), [signed discovery and administration](projects/core/tests/test_discovery.py).
- Core: [source, signatures, capture, discovery and preservation](projects/core/tests/).
- Storage: [S3 contracts](projects/core/tests/test_s3.py), [identity/retention](projects/core/tests/test_asset_versions.py), [publication checks](projects/core/tests/test_publication_storage.py), [collection](projects/core/tests/test_asset_storage.py).
- Release: [private staging, signed-main authority and retries](projects/core/tests/test_asset_release.py), [session-isolated draft delivery](projects/core/tests/test_private_preview.mjs), [branch previews and readiness](projects/view/tests/test_preview.py), [private browser sessions](projects/view/tests/test_preview_browser.mjs), [hosted private workspaces](projects/view/tests/test_preview_service.py).
- View: [catalogs and citations](projects/view/tests/test_catalog.py), [first unsigned repository previews](projects/view/tests/test_preview_bootstrap.py), [complete proposed-site and exact signed parity previews](projects/view/tests/test_preview.py), [authenticated preview service](projects/view/tests/test_preview_service.py), [atomic updates](projects/view/tests/test_update.py), [service/trust](projects/view/tests/test_service.py), [browser journeys](projects/view/tests/test_catalog_browser.mjs).
- Deployment: [configuration](tests/deployment/test_configuration.py), [Compose lifecycle and media](tests/deployment/test_compose.py), [opt-in provider rehearsal and scoped recovery](tests/support/guavajam_test_support/provider_qualification.py).
