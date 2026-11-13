# Working on GuavaJam

Tests define the supported source and presentation behavior.

- Readers and developers: [renderer contract](tests/test_renderer.py) and [presentation qualification](tests/test_presentation.py).
- Source compatibility: [Press](https://github.com/nashspence/press), [source reader](guavajam/source.py), and [reviewed supporting GFM](guavajam/supporting_gfm.py).
- Site operators: [catalog contract and deployment](docs/catalog.md), [catalog tests](tests/test_catalog.py), and [signed fixture browser journey](tests/test_catalog_browser.mjs).
- Use `make help` for commands and arguments. Keep recipes and help together.

Keep README minimal and onboarding short. Put rationale and verification history in issues/PRs. Views are derived; publication authority comes from the independently trusted signed source commit.
