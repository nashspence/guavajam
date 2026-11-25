GuavaJam issue 113, scope-revision research supplement (2026-11-25).
Parent immutable handoff: 1f0e02fa8b38f9e355297ad59247e17344b1b7ea.
Production base: 54fce07b619628a5e92be946be4b189873ddf1a0.
Use the retained parent .reference/experiments/corpus/guavajam-documents.jsonl.gz.
Gzip SHA256 87e077b393cf3c482b30f072573fd90395aee7bd9fed8d0c278e43b086343b63;
decompressed SHA256 ecb80dfe215b458c119ca6e0265723cdab5e7210410f864ace5b2c6a059f610a.

Reproduction (separate scratch dir; Python 3.13.5 SQLite 3.46.1, Linux):
python scopes-initial.py CORPUS.gz scratch/scoped
python parity.py scratch/scoped scratch/parity.json
python scopes.py CORPUS.gz scratch/long --history-50
python routing.py scratch/routing.json /usr/bin/chromium
Last command requires Playwright 1.57.0 and Chromium 144.0.7559.96.
Large generated databases are not supplied; reserve at least 3 GiB scratch.
scopes-initial.py is reconstructed exactly from the original script by undoing
only the later long-history branch insertion and range change. scopes.py is
that extension used to produce history-50-results.json. All retained reports
are actual run outputs; reruns on another toolchain may differ in bytes/timing.

Native prototype with modeled edition metadata, synthetic sentinel documents
and a small second work. Per-edition source text from real retained NAV fixture;
5% seeded changes and version field changes; not actual release history. Schema
uses shared passage bodies, simplified occurrence tuples (work, revision, path,
anchor), not full production citation/source-digest/presentation records. Six
fields use modeled edition facts; no signed source validation or site rendering.
Native timings are warmed count + ranked first page (20), not evidence/snippets,
WASM/browser transfer, or end-to-end latency. FTS integrity and relational checks
run at each build. 50-edition test has only seven queries and one pagination
assertion, not the complete five-edition or production query matrix.

Offline Chromium DOM witness, not production GUI or WASM worker. Both localhost
HTTP navigation and Playwright-fulfilled virtual HTTP navigation were blocked
(ERR_BLOCKED_BY_ADMINISTRATOR); pinned npm SQLite WASM retrieval failed with
EAI_AGAIN. The successful witness uses in-page fetch and URL-history doubles.
Tests verify one form, scope controls, no mock history fetch before execution,
stale response suppression, mock error/retry, simulated URL restoration, static
list without JS. No actual network abort, navigation, caching or transport claim.
History-50 raw file exceeds the current 256 MiB loader bound. No chunk/VFS run
at 50 editions; original handoff's 1/5 edition browser transport evidence remains.
No runtime or release changes; integrate and qualify on independently reviewed SHA.

Subsequent maintainer clarification: the Works scope is bibliographic only;
full-text Works layouts in scoped-results.json are rejected comparisons.
Reproduce new bibliography sizing: python works_catalog.py scratch/bibliography
Synthetic 80-word abstracts, three tags, two subjects; no document/passages or
historical rows. Native timings only. See works-catalog-results.json.
