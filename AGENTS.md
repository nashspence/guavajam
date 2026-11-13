# Working on GuavaJam

Tests define supported behavior. Use `make help` for commands and keep recipes/help together.

- Core: [source, signature, capture, asset and preservation contracts](projects/core/tests/).
- View: [renderer and catalog contracts](projects/view/tests/), [catalog operations](projects/view/docs/catalog.md), and [periodic updates](projects/view/docs/updates.md).
- Packages: [workspace and release preparation](docs/development.md), [isolated artifact checks](tools/check_artifacts.py), and [CI](.github/workflows/ci.yml).

Keep README minimal and onboarding short. Put rationale and verification history in issues/PRs. Views are derived; publication authority comes from independently trusted signed source commits. Core must not import the view. Neither indexed repositories nor local sibling checkouts supply tool implementations or signer policy.

All publication and data protocol identifiers are v1 while unreleased. Keep one current contract without migration commands or compatibility adapters. Package versions remain coordinated independently of protocol identifiers.
