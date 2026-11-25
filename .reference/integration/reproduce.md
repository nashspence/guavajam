# Verify and reproduce the final #113 handoff

Run from the repository root of the final reference commit. `.reference/proffer.json` and `integration/design.md` are the current entry points. Root `design.json` and `scope-revision/` preserve earlier research, including superseded full-text Works recommendations. The original runner `.reference/reproduce.py` reproduces original layouts only; it is not the final scoped implementation.

## Quick verification

Use Python with FTS5 and `jsonschema` (`>=4.22,<5`, as required by the retained official Proffer checker). Install dependencies in an isolated environment if necessary.

```sh
python3 .reference/tooling/proffer/check.py .reference/proffer.json
python3 .reference/verify.py
```

The inventory covers the complete reference payload, including old and final supplements. Its own record and the root Proffer record are bound by the published Git tree/commit instead of recursive self-hashes. Verification checks exact file hashes, original corpus identity and recorded-receipt consistency; it does not rerun benchmarks or qualify production. The original immutable handoff remains at `1f0e02fa8b38f9e355297ad59247e17344b1b7ea`; the preceding scope revision is `b3e667a9f2d4ff85bfe90c0af43d00497304aa3c`.

The final supplement's original `SHA256SUMS` intentionally lists only the recovered files; newer final design/reproduction/review files are covered by the root inventory. To verify the recovered subset:

```sh
(cd .reference/integration && sha256sum -c SHA256SUMS)
```

## Selected reproductions

Keep outputs outside `.reference/` so generated databases do not invalidate its exact inventory. Reserve at least 3 GiB for the full scoped/history run. Choose a fresh task-specific temporary directory:

```sh
scratch_dir=$(mktemp -d)
python3 .reference/integration/works_catalog.py "$scratch_dir/bibliography"
python3 .reference/integration/scopes-initial.py \
  .reference/experiments/corpus/guavajam-documents.jsonl.gz "$scratch_dir/scoped"
python3 .reference/integration/parity.py "$scratch_dir/scoped" "$scratch_dir/parity.json"
python3 .reference/integration/scopes.py \
  .reference/experiments/corpus/guavajam-documents.jsonl.gz "$scratch_dir/history-50" --history-50
```

The retained original receipts were produced with Linux, Python 3.13.5 and native SQLite 3.46.1. Sizes, hashes and timings can change with SQLite/toolchain versions. `scopes-initial.py` was reconstructed by undoing only the later history-50 branch and range insertion; `scopes.py` is the extended source used for the 50-edition run. This reconstruction is disclosed, not presented as a contemporaneously frozen initial file. The retained corpus is exact; its gzip and decoded hashes are in the design and original corpus manifest.

`works_catalog.py` requires no external corpus. It is a synthetic sizing fixture and does not exercise production source selection, structured subjects, citation rendering or browser routing. The final review rerun receipt is `works-catalog-review-results.json`; the original timing table remains attributed to `works-catalog-results.json`.

`scopes-initial.py` also builds now-rejected full-text Works prototypes. They and the collection-body checks in `parity.py` remain historical controls, not implementation requirements. The Passages/history tests use simplified citation tuples and modeled metadata. Query receipt counts include observations with no expected-count assertion; see the design's exact breakdown.

## Offline UI witness

With Playwright 1.57.0 and the recorded Chromium 144.0.7559.96 executable available:

```sh
python3 .reference/integration/routing.py "$scratch_dir/routing.json" /usr/bin/chromium
```

The script requires `playwright` in the selected Python environment and a compatible Chromium executable. It uses in-page fetch and URL-history doubles with `set_content`. Do not mistake successful execution for an HTTP, navigation, cancellation, WASM or installed-generator browser test. Actual HTTP navigation and fresh pinned WASM retrieval failed in the recorded environment. Do not bypass browser administration restrictions to reproduce it; qualify production in an appropriately configured normal test environment.

## Integration qualification

Use the acceptance matrix in `design.md` and current production-owned test commands (`make help`, generator Makefile, AGENTS.md). Ordinary scope changes and the larger history transport have separate gates. Preserve original source/normalization/transport receipts at their own source identities. Update intentionally obsolete collection-body/Versions-body search expectations; preserve the remaining behavioral contracts. New metadata-only and selected-edition artifacts must be tested in the installed production worker and actual browser/network environment before reporting performance completion.
