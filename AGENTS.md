# Working on GuavaJam

Use `make setup` for pinned mise/uv tools and `make help` for commands.

`README.md` and `AGENTS.md` are the only prose documentation on `main`; license texts are legal artifacts. Keep onboarding minimal, primarily linking to executable tests. Tests are authoritative; prose is limited to policy that cannot be encoded as executable checks. Put release guides with release documentation and rationale/verification history in issues/PRs.

- Licensing: [REUSE and package contracts](tests/repository/test_licensing.py); `make check-licenses` runs REUSE lint.
- Repository policy: [documentation boundary and onboarding links](tests/repository/test_policy.py).
- Packages: [core independence and single implementation ownership](tools/check_boundaries.py), [coordinated versions](tools/version.py), [isolated distributions](tools/check_artifacts.py), [release preparation](.github/workflows/prepare-release.yml).
- Core: [source, signatures, capture, discovery and preservation](projects/core/tests/).
- Storage: [S3 contracts](projects/core/tests/test_s3.py), [identity/retention](projects/core/tests/test_asset_versions.py), [publication checks](projects/core/tests/test_publication_storage.py), [collection](projects/core/tests/test_asset_storage.py).
- View: [catalogs and citations](projects/view/tests/test_catalog.py), [atomic updates](projects/view/tests/test_update.py), [service/trust](projects/view/tests/test_service.py), [browser journeys](projects/view/tests/test_catalog_browser.mjs).
- Deployment: [configuration](tests/deployment/test_configuration.py), [Compose lifecycle and media](tests/deployment/test_compose.py).
