# Working on GuavaJam

Use `make setup` for pinned mise/uv tools and `make help` for commands.

`README.md` and `AGENTS.md` are the only prose documentation on `main`; license texts are legal artifacts. Keep onboarding minimal, primarily linking to executable tests. Tests are authoritative; prose is limited to policy that cannot be encoded as executable checks. Put release guides with release documentation and rationale/verification history in issues/PRs.

- Licensing: [REUSE and package contracts](tests/repository/test_licensing.py); `make check-licenses` runs REUSE lint.
- Repository policy: [documentation boundary and onboarding links](tests/repository/test_policy.py), [GuavaJam v1 identifiers and work metadata](tests/repository/test_namespace.py).
- Packages: [core independence and single implementation ownership](tools/check_boundaries.py), [coordinated versions](tools/version.py), [isolated distributions](tools/check_artifacts.py), [release preparation](.github/workflows/prepare-release.yml).
- Core: [source, signatures, capture, discovery and preservation](projects/core/tests/).
- Storage: [S3 contracts](projects/core/tests/test_s3.py), [identity/retention](projects/core/tests/test_asset_versions.py), [publication checks](projects/core/tests/test_publication_storage.py), [collection](projects/core/tests/test_asset_storage.py).
- Release: [private staging, signed-main authority and retries](projects/core/tests/test_asset_release.py), [session-isolated draft delivery](projects/core/tests/test_private_preview.mjs), [unsigned draft previews](projects/a-guavajam-static-website-generator/tests/test_preview.py), [private browser sessions](projects/a-guavajam-static-website-generator/tests/test_preview_browser.mjs), [hosted private workspaces](projects/a-guavajam-static-website-generator/tests/test_preview_service.py).
- `a-guavajam-static-website-generator`: [editions and citations](projects/a-guavajam-static-website-generator/tests/test_catalog.py), [atomic updates](projects/a-guavajam-static-website-generator/tests/test_update.py), [index service and independent trust](projects/a-guavajam-static-website-generator/tests/test_service.py), [keyboard, hover and browser journeys](projects/a-guavajam-static-website-generator/tests/test_catalog_browser.mjs).
- Deployment: [configuration](tests/deployment/test_configuration.py), [Compose lifecycle and media](tests/deployment/test_compose.py), [opt-in provider rehearsal and scoped recovery](tests/support/guavajam_test_support/provider_qualification.py).
