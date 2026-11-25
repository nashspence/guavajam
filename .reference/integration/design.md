# Integration recommendation for GuavaJam #113

This is the controlling design for the final reference handoff. It incorporates the requester's final decision: **Works is bibliographic discovery over the latest accepted edition of each work. It does not search manuscript or supporting-document bodies.** This design supersedes conflicting recommendations in the retained `design.json`, `scope-revision/design.md`, earlier Proffer records and historical issue comments. Those files remain evidence of the investigation, not competing implementation instructions. The owning issue is https://github.com/nashspence/guavajam/issues/113.

Production source was inspected at `54fce07b619628a5e92be946be4b189873ddf1a0`; main still pointed to that commit at the final review. This reference branch changes only `.reference/`. It includes research prototypes and an earlier source-guard patch, not a production implementation or a branch to merge into main.

## 1. Fixed search contracts

| Surface | Searchable corpus and fields | Results |
| --- | --- | --- |
| Collection Works | One record per work from its latest accepted edition: title, authors, abstract, subjects and tags. Index that edition's publication date for date filtering/sorting. | Work |
| Work Versions, ordinary mode | That work's accepted edition names, declared titles and full commit messages. | Edition |
| Edition Documents | Supporting-document titles and abstracts in exactly the linked edition, with generic literal metadata filters. | Document |
| Edition Passages | Manuscript/supporting passage occurrences in exactly the linked edition, with existing eligible passage text fields and filters. | Exact edition/document/anchor occurrence |
| Work Versions, explicit complete-history mode | Passage occurrences across all accepted editions of that work, with the same passage-field contract. | Exact historical edition/document/anchor occurrence |

Latest means verified accepted publication order, not the greatest claimed date. All Works searchable fields, facets and dates come from the same latest edition. Missing/empty latest metadata stays missing/empty; never fill it from a superseded edition. Keep routing identifiers and small useful scalar facts such as `edition_count` for the existing Most editions sort; these do not require historical searchable rows. Unpublished/empty works retain truthful directory behavior and have no invented accepted edition.

Tags map to the existing keyword metadata. Preserve the existing structured subject identity and authority/label disambiguation; the prototype's flat subject strings are not a replacement source contract. Preserve ordered author facts and the existing supported representation. Dates need indexed predicates and deterministic sort behavior, not a new date-expression language. Supporting custom metadata remains exact filtering; do not silently add it to Documents free-text fields.

Works contains no manuscript/supporting body, passage, historical metadata or commit-message FTS. Versions contains no body FTS. A collection-wide passage/full-text mode is not part of this work. Works and ordinary Versions need no passage-evidence or body-window machinery. Show evidence from the matching eligible metadata fields using the existing presentation conventions.

Give each logical corpus its own ranking statistics. A selected-edition result must not inherit all-history BM25 statistics; Works ranks one bibliography record per work. Filters narrow eligible occurrences within a scope without redefining its underlying FTS corpus. Preserve deterministic sorting/ties, exact counts, pagination and occurrence identity. Complete-history relevance is global within that work's complete passage corpus. Identical text in different editions remains separate citable occurrences. An existing Git/edition field may legitimately make a commit-message term match a passage; fixtures must reflect the real passage-field contract.

## 2. Complete history belongs in the existing Versions search component

Extend the existing `directory_pages._sqlite_controls(kind='editions', ...)` component and associated worker/UI. Ordinary Versions mode is the default. Use one compact accessible mode control, for example Versions / Complete history (passages); the exact widget is an implementation choice. Adapt the query label, filters, sorting, count and row presentation. Keep the static edition list, navigation and feeds usable without JavaScript. Do not add another search form or place a history section/action below the edition list.

Selecting history reveals a concise notice before execution, for example: “Search passages across all published versions. This may require larger downloads and take longer.” Opening the work page or search controls, typing, and selecting history alone must not fetch a history index, chunk manifest or chunks. A small routing descriptor containing a history artifact URL/identity is permissible. Do not eagerly enumerate every edition artifact in a large directory manifest.

An explicit history submission starts loading. A canonical deep link with an executable history query also counts as explicit intent; mode-only links do not. Permit a deliberately submitted filter-only search where supported, but do not turn an empty untouched form into an automatic match-all history download. Subsequent queries in the selected mode need no repeated confirmation. Preserve/restorably encode mode, query, applicable filters, sort and page in URLs; reset invalid controls/page when changing mode. Browser back/forward should restore executed search state consistently.

Provide accessible progress, cancel and retry. Abort superseded network/query work where possible and fence all late replies by request, scope and generation. Release large resources when they are no longer needed. A failed, interrupted, incompatible or incomplete query must not display an apparently complete count or a misleading zero-result state. Retain a useful static navigation fallback.

## 3. Build physically scoped artifacts and select delivery by measured size

Use generator-owned, versioned routing metadata for a small Works bibliography index, a small Versions name/message index per work, Documents and Passages indexes per exact accepted edition, and a passage-only complete-history index per work. Fetch only the chosen artifact. Do not construct one all-history database and filter it after loading, or merge shard-local relevance scores as though they were global.

Key immutable edition artifacts by exact source revision plus relevant renderer/index/schema/tool generation. Appending another edition should not change old edition artifacts when those inputs are unchanged. Bind queries to a coherent manifest generation; test atomic replacement, cache invalidation and recovery. Measure build time, peak memory and total hosted bytes as well as query downloads. Reuse source blocks and rendered outputs rather than duplicating the entire source/site per search scope.

Prefer the existing simple immutable whole-gzip loading approach for Works, Versions, Documents and edition Passages whenever their measured transfer/decoded/transient budgets permit. Their index sizes should depend on their selected metadata/content, not unrelated historical bodies. The synthetic Works data demonstrates why a full-text collection index is unnecessary; it does not establish a universal size ceiling for arbitrary abstracts or collections.

For an oversized logical scope, the recommended next candidate is compressed physical chunks of one logical SQLite database, delivered by immutable ordinary GET objects. This preserves global within-scope ranking and requires no SQL server. The original 1/5-edition VFS experiments support feasibility; the 50-edition transport is still an implementation qualification gate. Do not raise the whole-download guard and treat that as bounded loading. Do not force chunk-VFS complexity onto small indexes.

Bind schema/generation, database size/page size, ordered chunks, encoded/decoded lengths and cryptographic hashes in the delivery manifest. Verify integrity before use. Independently bound concurrent requests/decompression, compressed and decoded caches, SQLite page/temporary/query memory and outstanding queries. Include progress, cancellation, retry and eviction. Measure total process/transient memory separately from WASM and explicit cache sizes. Keep a native full-scope oracle for cases too large for the whole-file browser loader; compare whole gzip in the browser only where it can run safely.

The integration agent can begin ordinary-scope implementation immediately. It should record finite supported workload/device/network profiles and provisional transfer, latency and memory budgets before claiming performance qualification. No numeric production SLA was established by this research. History may take longer after opt-in; it must remain complete, bounded and recoverable. If the global-history candidate cannot meet the declared envelope, report the measurements and request a specific decision before changing result semantics. Progressive edition-grouped relevance is an alternative product contract, not a transparent equivalent.

## 4. Retained generic foundations

### Source/build processing

Remove arbitrary aggregate supporting-document count/byte policy once the affected paths process large valid inputs within explicit operational budgets. Batch Git enumeration/blob reads; stream to bounded storage with framing, path/type, truncation, deadline and subprocess-cleanup checks. Preserve protective per-operation bounds or expose deliberate operator budgets with precise errors. The tested 8-MiB individual-source and 20-MiB snapshot-blob guards are separate from aggregate policy.

Replace whole-corpus AST retention for link checks with a compact on-disk document/anchor/reference inventory; render incrementally. Evolve generator-owned NAV, VIEW, CACHE, SITE and search inventories coherently, with exact source identities and complete file coverage. Fix the separate CACHE inventory ceiling rather than merely lifting source limits. Keep atomic publication, interruption recovery and cache reuse. Change core publication catalogs only if measured necessity and compatibility evidence require it.

### Generic metadata facets

Carry verified supporting metadata into navigation and normalized indexed memberships. A document occurrence owns its memberships; passages inherit through that occurrence. Use OR within selected values of one key and AND across keys. Preserve Unicode, case, numeric-looking strings, display order and missing/empty distinctions; an empty list has no value membership. Bind keys/values as parameters. Bound, search and deterministically page facet options; do not ship every distinct value in a dropdown. Text eligibility, facet totals, count and visible page must refer to the same scope. Preserve established structured subject identity separately from generic literal document facets.

### Stored content and citations

Normalize repeated text, edition facts and citation presentation while retaining full occurrence identities: repository/work, revision, source digest, path, anchor, order and citation context. Share stable ordered source-text segments independently of changing metadata. A content hash is not a citation identity.

FTS5 external-content storage must reconstruct each scope's exact searchable fields, tokenizer behavior, token positions and document sizes. Retain occurrence-level postings. Check FTS external-content consistency plus relational integrity. Capture ordered source blocks during extraction; do not rebuild arbitrary document text by concatenating overlapping passage/section records. The flat research adapter's concatenation invariant is not valid for general headings, tables, lists, quotations or interstitial text.

### Query and visible evidence

Count membership without snippets, select winners/page IDs with deterministic ordering, then produce visible evidence/citations for those occurrences. Use indexed constraints and choose query plans for text/facet selectivity. For large passage bodies, use bounded source evidence windows or an equivalent position-aware excerpt structure. Windows must not redefine full-text membership, ranking or citation identity. Preserve cross-window matches, exact offsets/separators, safe token boundaries and a correct fallback for oversized unbroken tokens. The preferred excerpt may change; the highlighted evidence must remain genuine and from the winning occurrence. Metadata-only scopes need only their own eligible-field highlighting.

## 5. Evidence and its limits

### Bibliographic Works sizing — final recommended scope

`works_catalog.py` and `works-catalog-results.json` preserve the original native experiment: Python 3.13.5 / SQLite 3.46.1, synthetic 80-word abstracts, three tags and two flat subject labels per work, one FTS record per work, indexed date and exact facet relations.

| Works | SQLite bytes | gzip9 bytes | Warm native count + first ranked page, ms |
| ---: | ---: | ---: | ---: |
| 2 | 36,864 | 2,778 | 0.021 |
| 1,000 | 1,605,632 | 404,153 | 0.657 |
| 10,000 | 15,650,816 | 3,874,720 | 8.043 |

There were 27 term-query executions: 24 expected-count assertions and three recorded subject-count observations. All explicit assertions and FTS/relational integrity checks passed. Additional assertions cover row counts, exact case-sensitive facets and a date predicate. Latest title/author/abstract/tag tokens match; discarded old title/abstract tokens do not. Dates are deliberately reversed relative to accepted order. The schema has no body/document/passage/history rows. However, the body-only sentinel is not present in an ingested supporting document, and the old-body token is part of a discarded old title. This is not end-to-end source-routing proof. Stale author/subject/tag, missing-latest fallback and structured-subject identity remain production acceptance tests. Vocabulary and abstract lengths are controlled, so these are sizing observations, not production or browser guarantees. A final-review rerun with the unchanged script on Python 3.12.14 / SQLite 3.53.1 passed with identical recorded term counts and decoded sizes; database hashes and gzip sizes differed. Both receipts are retained separately.

### Scoped content/history sizing

The retained real NAV corpus has 5,947 Riverhog contract documents and 36,063 passages. Its gzip SHA256 is `87e077b393cf3c482b30f072573fd90395aee7bd9fed8d0c278e43b086343b63`; decompressed SHA256 is `ecb80dfe215b458c119ca6e0265723cdab5e7210410f864ace5b2c6a059f610a`. The supplemental scripts add three synthetic scope witnesses and model 5% document churn plus changed edition metadata. They do not represent observed releases or signed full-site builds. Citation storage uses simplified tuples rather than full production source/citation records.

| Prototype | SQLite bytes | gzip9 bytes |
| --- | ---: | ---: |
| Versions, 5 editions | 57,344 | 1,828 |
| Versions, 50 editions | 65,536 | 4,324 |
| Versions, 10,000 modeled records | 1,695,744 | 478,468 |
| Documents, edition 1 | 4,259,840 | 921,939 |
| Documents, edition 5 | 4,259,840 | 921,956 |
| Passages, edition 1 | 34,459,648 | 6,674,475 |
| Passages, edition 5 | 34,951,168 | 6,715,633 |
| Complete history, 5 editions | 127,717,376 | 19,960,086 |
| Complete history, 50 editions | 1,187,102,720 | 167,779,751 |

The initial receipt records 51 query executions (41 expected-count assertions and 10 observations); all explicit assertions passed. Six executions concern the rejected full-text Works scope and are not acceptance evidence for bibliographic Works. Separate parity checks compare 36,066 passage fields/identities, 35,690 facet memberships per comparison and 5,949 document title/abstract identities. Edition 1 rebuilt byte-identically in that environment (`a119af76e3ceb3fc91b461ec777f67c217c1b889c5bc66d0da699e22083acca3`). Scope-specific BM25 differences are intentional. The 10,000 Versions rows are a metadata scaling fixture, not authority to change core history eligibility.

The 50-edition database contains 297,500 document and 1,803,300 passage occurrences. It passed FTS/relational integrity, five expected-count assertions among seven query executions, and pagination of 50 distinct same-body occurrences. A broad native warm count plus first ranked page took about 2.1 seconds, excluding excerpts, browser startup and transfer. The decoded database exceeds the current 256-MiB whole-file guard. Fifty-edition browser/chunk transport is unqualified. The older roughly 8.74-MiB gzip full-text Works prototype and its collection-body parity checks are retained only as a rejected comparison; do not implement that scope.

The offline Chromium witness passed 12 DOM/state checks using in-page fetch and URL-history doubles. It demonstrates component behavior only. HTTP navigation was blocked and fresh pinned WASM retrieval failed in that run. It does not establish production UI, real network cancellation, navigation, caching or WASM performance. The earlier 1/5-edition actual-browser transport receipts remain separate evidence.

### Earlier foundational evidence

Original full-schema experiments, scripts and receipts remain at their original paths. They establish source/navigation fidelity, exact-facet feasibility, normalized-storage parity and late-evidence savings for those tested layouts. They are not directly comparable to the simplified supplemental schema. The actual generated corpus is text-faithful but uses a flat paragraph adapter; final rich GFM and full signed source/render/cache/atomic-publication behavior still require qualification.

In the original controlled browser profile, a one-edition directory loaded in 1.70 seconds cold / 0.112 seconds warm; a five-edition broad chunk query took 17.64 seconds and 11.25 MiB of SQLite transfers. The chunk prototype used 1-MiB decoded chunks, 32-MiB decoded and 8-MiB compressed caches plus an 8-MiB SQLite page-cache setting; these are experimental settings, not accepted production budgets. The profile applied 40-ms latency and 10-Mbit/s bandwidth per response, not a shared mobile connection. Its worker checked lengths but lacked production hash verification, cancellation and corruption recovery. These receipts support further implementation, not closure of performance gates.

## 6. Implementation order and acceptance

1. **Contracts and ordinary artifacts.** Implement the fixed scopes, latest selection and versioned routing/cache identity. Start with bibliography Works, name/message Versions, and edition Documents/Passages. Update incompatible collection-body and Versions-body expectations in `test_search_browser.mjs` / `test_work_search_browser.mjs`; do not require old full-history BM25 or rejected collection full-text parity. Preserve literal-query safety, structured subjects, focus/highlighting, pagination and static fallback.
2. **Generic foundations.** Integrate bounded source/build processing, inventory evolution, exact facets, occurrence-preserving storage and late evidence. Reuse original research selectively; qualify against the actual integration SHA and source shape. Coordinate source/authority/compatibility work with #112 without treating this research as a new unconditional RC blocker.
3. **Integrated history UI and ordinary delivery.** Implement the single Versions search component, explicit history execution and recovery. Qualify ordinary artifacts with installed production code, actual browser requests and finite declared profiles. Ordinary work may land independently; keep the remaining gates tracked.
4. **Large history and release qualification.** Implement and qualify bounded global-history transport against native full-scope oracles, 1/5/50 modeled histories, sparse/high-churn/skewed changes and representative large passages. Compare viable delivery alternatives without changing semantics silently. Run relevant repository, installed-artifact and supported browser/mobile gates on the actual integration SHA. Leave #113 open until each requirement has implemented evidence or an explicit accepted disposition and owner/next trigger.

Required executable acceptance matrix:

| Area | Required proof |
| --- | --- |
| Latest bibliography | Reversed claimed dates; stale title/author/abstract/subject/tag absent; latest missing fields do not fall back; manuscript/supporting-body-only and commit-message-only terms absent; structured same-label/different-authority subjects remain distinct. |
| Scope boundaries | Manuscript-linked Documents/Passages stay in that exact edition; Versions matches name/title/message only; history covers one work only; identical bodies retain each edition citation; filters/counts/sorting/ranking/pagination agree with a scope-specific oracle. |
| Generic facets | OR within key / AND across keys; Unicode/case/numeric-looking values; empty/missing values; bounded high-cardinality option pagination; totals and results agree. |
| Fetch/cache boundaries | No history index/chunk-manifest/chunk request during ordinary pages/queries or selection-only history; small routing descriptor allowed; stable old-edition artifacts after append; no eager all-edition artifact manifest; stale generation rejected. |
| UI state | Notice before explicit execution; query and supported filter-only execution; mode-only vs query deep links; URL back/forward; invalid filter/page reset; rapid switches; actual cancellation, late-reply suppression, retry; keyboard/mobile and no-JS static directory. |
| Correct source evidence | Full source digest/citation parity, mixed-field matches, overlapping sections/blocks and cross-window terms, large/unbroken tokens; count/page avoids eager excerpts; eligible metadata highlights for Works/Versions. |
| Build/recovery | Complete large valid sources; malformed/truncated/oversized individual inputs rejected precisely; bounded inventory/link/render pipeline; atomic failure/retry/cache recovery; hosted bytes and peak memory measured. |
| Large-history delivery | Complete counts/pages and global within-work ranking; exact historical citations; manifest/hash/length verification; missing/corrupt/truncated/wrong-generation data; network interruption, cancellation and cache eviction; explicit memory/concurrency bounds and truthful incomplete status. |

## 7. Producer and integration boundaries

GuavaJam retains source validity/publication authority. The optional generator owns indexes, rendering, delivery and search UI; search derivatives are not authority. No software release parsing, contract equivalence, inferred applicability, server dependency or Riverhog-specific search feature is introduced.

Riverhog should keep stable element paths, complete immutable documentation-edition snapshots and a hand-written manuscript. It owns software-release-to-documentation mapping, or explicit literal applicability memberships if it chooses change-only pages. “Changed in X” alone cannot tell a generic equality filter what applies to Y or what was removed. Preserve/redesign its separate documentation-authoring history, frozen release docs and native CLI/OpenAPI/Python/distribution/OCI contracts in Riverhog. The inspected release-documentation revision `9fb4a7d52b12a61e582be5656471979e8ee5e1e4` had no populated authored manual to benchmark.

Start from `.reference/proffer.json`, verify `.reference/inventory.json` with `.reference/verify.py`, then follow `integration/reproduce.md`. Treat this document as the implementation recommendation; use retained reports only within their recorded provenance. Integrate in a separate production worktree and record a Proffer consumer disposition in #113 with accepted/adapted/deferred/rejected work, exact integration/check source and remaining triggers. Validation of this reference is not consumer acceptance.
