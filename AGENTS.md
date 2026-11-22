# Working on GuavaJam

GuavaJam owns publication authority and shared contracts. Optional implementations consume GuavaJam; GuavaJam must never depend on them.

Use `make setup` for pinned mise/uv tools and `make help` for GuavaJam and workspace checks; the optional generator owns its [commands and deployment](some-implementations/a-guavajam-static-website-generator/Makefile).

`README.md` and `AGENTS.md` are the only prose documentation on `main`; license texts are legal artifacts. Keep onboarding minimal, primarily linking to executable tests. Tests are authoritative; prose is limited to policy that cannot be encoded as executable checks. Put release guides with release documentation and rationale/verification history in issues/PRs.

- Licensing: [REUSE and package contracts](tests/repository/test_licensing.py); `make check-licenses` runs REUSE lint.
- Repository policy: [documentation boundary and onboarding links](tests/repository/test_policy.py), [GuavaJam v1 identifiers and work metadata](tests/repository/test_namespace.py).
- Packages: [GuavaJam independence and single implementation ownership](tools/check_boundaries.py), [boundary counterexamples](tests/repository/test_boundaries.py), [coordinated versions](tools/version.py), [isolated distributions](tools/check_artifacts.py), [release preparation](.github/workflows/prepare-release.yml).
- GuavaJam: [source, signatures, capture, discovery and preservation](tests/guavajam/).
- Storage: [S3 contracts](tests/guavajam/test_s3.py), [identity/retention](tests/guavajam/test_asset_versions.py), [publication checks](tests/guavajam/test_publication_storage.py), [collection](tests/guavajam/test_asset_storage.py).
- Release: [private staging, signed-main authority and retries](tests/guavajam/test_asset_release.py), [session-isolated draft delivery](tests/guavajam/test_private_preview.mjs), [unsigned draft previews](some-implementations/a-guavajam-static-website-generator/tests/test_preview.py), [private browser sessions](some-implementations/a-guavajam-static-website-generator/tests/test_preview_browser.mjs), [hosted private workspaces](some-implementations/a-guavajam-static-website-generator/tests/test_preview_service.py).
- `a-guavajam-static-website-generator`: [local Lucide controls and link types](some-implementations/a-guavajam-static-website-generator/tests/test_icons.py), [editions and citations](some-implementations/a-guavajam-static-website-generator/tests/test_catalog.py), [atomic updates](some-implementations/a-guavajam-static-website-generator/tests/test_update.py), [index service and independent trust](some-implementations/a-guavajam-static-website-generator/tests/test_service.py), [keyboard, hover and browser journeys](some-implementations/a-guavajam-static-website-generator/tests/test_catalog_browser.mjs).
- Search: [visible evidence for every text hit, viewport focus/scroll behavior and highlight contrast](some-implementations/a-guavajam-static-website-generator/tests/test_search_browser.mjs), [Versions/Documents/Passages parity](some-implementations/a-guavajam-static-website-generator/tests/test_work_search_browser.mjs).
- Deployment: [configuration](tests/deployment/test_configuration.py), [Compose lifecycle and media](tests/deployment/test_compose.py), [opt-in provider rehearsal and scoped recovery](tests/support/guavajam_test_support/provider_qualification.py).
