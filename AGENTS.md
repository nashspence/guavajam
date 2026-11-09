# Working on Press

The [tests](tests/) are authoritative for enforceable behavior. Change tests and implementation together; test the starter authors actually copy.

- Developers: [required CI](.github/workflows/validate-source.yml) and [tests](tests/).
- Capture developers: load [the extension](tools/chatgpt-capture/) unpacked in Chrome; [browser qualification](tests/test_chatgpt_capture_browser.mjs) exercises its real permissions and downloads.
- Submitters: [manuscript starter](templates/work/manuscript.md), [supporting GFM starter](templates/work/supporting/note.md), [manuscript contract](tests/test_validate_sources.py), and [evidence contract](tests/test_gfm_source_contract.py).
- Publishers: [publication workflow](.github/workflows/prepare-edition.yml), [publication contract](tests/test_publication.py), and [recovery contract](tests/test_backup_package.py).
- Asset operators: [credential template](storage/credentials.env.example), [storage contract](tests/test_asset_storage.py), and [maintenance workflow](.github/workflows/maintain-assets.yml).

Use `make help` for commands and arguments; maintain that help with its [recipes](Makefile).

Keep README.md absolutely minimal and this file a short onboarding map. Link to tested contracts instead of restating them. Keep rationale and verification history in issues/PRs.

After recording rehearsal results in the owning issue/PR, delete disposable repositories and task-created hosting resources. The owner has authorized this cleanup without further confirmation.
