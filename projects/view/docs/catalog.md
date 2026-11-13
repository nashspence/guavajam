# Catalogs, DOI records and committed-source citations

GuavaJam can generate a complete static presentation: collection index, work
pages, exact-edition landing pages, reading views, supporting documents, source
citations, an Atom feed and an XML sitemap. It is neither a DOI registrar nor a
publication issuer. The signed Git publication remains authoritative. No Press
repository import, credentials, network fetch, runtime JavaScript or GitHub API
is required by the site build.

The executable contracts are `projects/view/tests/test_catalog.py` and
`projects/view/tests/test_catalog_browser.mjs`. The existing single-edition `guavajam --repo …`
invocation is unchanged; `guavajam render …` is an explicit synonym.

For publisher-owned automatic discovery and periodic deployment, use
[`make update`](updates.md). The explicit catalog workflow below remains supported.

## Build

Use the pinned tools from `make setup`. The source repository must contain every
referenced full publication commit, its ancestry and source blobs. Supply signer
trust independently of that repository and the catalog. For SSH:

```sh
mise exec -- uv run --locked guavajam site \
  --repo /path/to/source-repository \
  --catalog /path/to/reviewed-catalog.json \
  --ssh-allowed-signers /path/to/independently-managed-allowed-signers \
  --initial-catalog \
  --output /path/to/new-site
```

For GPG, use `--trusted-gpg-fingerprint FULL_FINGERPRINT` instead of the SSH
option; the caller's keyring must contain the corresponding verified public
key. The option can be repeated for an explicitly authorized key rotation.
`make site` exposes the same inputs; run `make help`.

Only the **first** deployment uses `--initial-catalog`. Every later build must
use the independently retained `CATALOG.json` from the last successful deployment:

```sh
guavajam site --repo /path/to/source-repository \
  --catalog /path/to/reviewed-catalog.json \
  --ssh-allowed-signers /path/to/independently-managed-allowed-signers \
  --previous-catalog /path/to/last-deployment/CATALOG.json \
  --output /path/to/new-site
```

The two baseline options are mutually exclusive. A baseline must come from your
trusted deployment history, not from an untrusted request or an arbitrary current
webpage. Resetting to `--initial-catalog` defeats historical binding checks and
must not be used to make a rejected update pass. This is a caller/deployment
policy, not a cryptographic registration ledger.

The destination must be new or empty, outside the read-only source repository.
The builder stages everything privately, audits generated links and fragments,
and installs the completed directory only after all checks succeed. It never
alters a publication commit or a source checkout. There is no unsigned catalog
mode and no discovery of whichever commits happen to be at `HEAD`.

## Administrative catalog

Start with `examples/catalog.json`. It deliberately contains **no invented
publication commits or DOI assignments**. Replace the imprint and deployment
origin and add reviewed records. This schematic example uses a made-up hash and
DOIs; it illustrates the schema and must not be deployed unchanged:

```json
{
  "schema": "guavajam.catalog.v1",
  "title": "Collected Publications",
  "publisher": "Example Imprint",
  "language": "en",
  "base_url": "https://publisher.example/collection",
  "source_view": "https://code.example/repository/blob/{revision}/{path}",
  "works": [
    {
      "slug": "field-notes",
      "concept_doi": "10.1234/field-notes",
      "metadata_revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "recommended_revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "editions": [
        {
          "revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          "published": "2026-11-13",
          "label": "First edition",
          "doi": "10.1234/field-notes.v1",
          "status": "available"
        }
      ]
    }
  ]
}
```

The catalog is a bounded UTF-8 JSON document. Duplicate and unknown keys are
errors; it is not a template and cannot specify executable code, signer policy,
repository paths or CSS. The catalog may be maintained in a publisher's current
administrative Git tree or another reviewed location; it need not be added to
already-signed publication trees. A later DOI assignment never requires
rewriting the original publication commit.

`base_url` is an HTTPS deployment URL with an optional simple path prefix and no
trailing slash, credentials, query or fragment. Generated navigation is relative,
so a complete tree is portable. Absolute discovery and SEO links use this base.
`source_view` is optional: an HTTPS access-path template with exactly one
`{revision}` and `{path}` in the URL path. It can point to GitHub, another forge or
a publisher's source viewer. It is never a canonical citation identifier and
GuavaJam never fetches it. GitHub's optional form is
`https://github.com/OWNER/REPO/blob/{revision}/{path}`.

Each `slug` selects `works/<slug>/manuscript.md`. The signed-source
conventions accept `Press-Publication: 1` with `Press-Work`, and v2 with
explicit `Press-Published` and optional `Press-Label` trailers, a full
lowercase SHA-1 commit ID, an independently trusted signature, and an empty
single-parent publication commit preserving the reviewed parent tree. A branch,
tag, shortened hash, unsigned draft or wrong-work commit is not accepted.

`metadata_revision` explicitly chooses the signed edition supplying the work
page's title, ordered authors, abstract, language and rights. It is required;
the generator never guesses the latest edition. `recommended_revision` is
optional and must name an available edition. A work with no available editions
can keep its metadata and complete edition history without a reading recommendation.

`concept_doi` and edition `doi` are optional, distinct lowercase bare DOI names.
They must be globally unique within the catalog. A declared source `concept_doi`
must agree with the catalog. Assignments added later are allowed when the source
has no such declaration. Syntax validation is not evidence of registrar ownership
or completed registration: operators must check those facts separately.

Publication dates are explicit `YYYY-MM-DD` catalog facts, not inferred Git
committer timestamps. They are not independent timestamp attestations. English
is currently the interface language; manuscript/supporting source languages and
Unicode names/content are retained. Bibliographic facts cannot be overridden by
catalog HTML or inferred from presentation order.

### Provenance of historical facts

`metadata_revision` selects signed title, authorship, abstract, language and
rights. It does not establish when the work was first published. A catalog may
list only part of a work's history, so neither its earliest listed edition nor
the metadata edition supplies an original publication date for the work. Work
records omit publication date metadata and a dated work-level citation; edition records
and citations retain their explicitly reviewed edition dates.

DOI fields record current bindings. Adding a previously unassigned DOI preserves
the edition's publication date and signed source identity; that date does not
become a DOI assignment date. The current schema has no fields for a work's
original publication date, DOI assignment history or initial editorial notes. Reviewed dated `notices`
remain explicit editorial records with append-only history. Git timestamps,
unstructured commit messages and catalog ordering cannot supply missing administrative facts.
The dated v2 publication declaration described in `updates.md` is an explicit
schema for publisher-declared edition dates, not for historical work or DOI-assignment dates.

Before displaying additional historical facts, retain their independently
reviewed records in the publisher's administrative review tree, identify the
exact work or edition they describe, and approve an explicit schema and
validation contract. When those records are unavailable, omit the facts and
record that migration decision in the publisher's review. Existing DOI-to-item
bindings and notices remain protected by the catalog comparison.

## DOI scope, notices and historical bindings

A work DOI identifies the work across editions; its page enumerates those
editions and says which source supplied the displayed bibliographic facts. An
edition DOI identifies exactly one signed revision of one work. Neither is the
identity of the optional HTML renderer.

A catalog comparison rejects removal of existing work or edition pages, removal
or reassignment of their DOIs, and moving an edition to another work. Adding
new editions and previously unassigned DOIs is supported. Changing an existing
edition's status, publication date or label requires an appended notice. Existing
notices must remain verbatim and in order. This does not prevent an authorized
operator from editing the catalog; it makes normal builds fail closed when an
update would silently rewrite prior bindings.

Statuses are `available`, `superseded`, `withdrawn` and `unavailable`.
`superseded`, `withdrawn` and `unavailable` require a dated, explanatory notice;
any later status or successor change must append a new notice, rather than
rewrite old ones. Notices are escaped plain text; optional `url` is a reviewed
HTTPS link to supporting details. Signed title, manuscript and author bytes are
never changed by catalog notices.

**Withdrawal and supersession never suppress historical reading access.** Their
DOI landing pages, native reading views, supporting-source views, exact GFM
source downloads, passage citation pages and citation JSON remain available.
Editorial status and the dated notices appear prominently on DOI landing pages,
reading pages and citation pages before substantive content. Textual headings
and explanations convey the status without relying on color; warnings remain
visible in print. Later restoration to `available` requires an appended notice.

A `superseded` edition requires `successor_revision`, the full SHA-1 of a
distinct later (or same-day) signed edition of the **same work**. A withdrawn
edition may optionally have a successor. The DOI and reading pages link to the
successor's exact edition record rather than choosing the latest edition
implicitly. References are validated for existence, chronology and cycles.
An existing successor may change only with an appended dated notice; for
example, restoration to `available` can explicitly retire the administrative
successor link without altering either canonical publication.

```json
{
  "revision": "<40-character-commit-SHA>",
  "published": "2026-11-01",
  "status": "withdrawn",
  "notices": [
    {"date": "2026-11-14", "text": "Withdrawn following editorial review. Historical source remains available."}
  ],
  "successor_revision": "<different-40-character-commit-SHA>"
}
```

`unavailable` is deliberately different: the bibliographic page, DOI, signed
Git identity, explanatory notices, and edition citation JSON remain, but the
catalog does not distribute a reading copy or source download. Do **not** use
this status as a synonym for `withdrawn` or `superseded`. Its notice must explain
why this optional access path is restricted. The signed Git objects must still
be present for verification, and a missing object or invalid signature aborts
the **entire build** rather than creating an unverifiable tombstone.

## Citation and page behavior

Every available edition provides an HTML reading view and its exact GFM source.
Each declared source anchor receives a link to a citation entry containing:

- Conventional work bibliography and the edition DOI when assigned.
- The complete Git SHA-1 revision and repository-relative manuscript path.
- The supporting-document path where applicable, and the exact source anchor.

No recommended citation substitutes a view URL or generated heading slug for the
committed source identity. DOI links remain part of the conventional citation;
the hash/path/anchor adds independently verifiable specificity. A source anchor
is preserved even if it happens to resemble an internal GuavaJam navigation ID.
Citation download JSON separates the canonical `press.citation.v1` locator from
administrative bibliography and available anchors. Whole-document citations do
not fabricate a passage reference.

### Navigation and the signed-source reading order

The generated registry, work records, edition DOI records, citation helpers and
reading views share a small **breadcrumb** navigation (`nav` with an ordered
list, `aria-label="Breadcrumb"` and one non-link `aria-current="page"` item).
Ancestors use the concise “Publications” root link, the actual verified
work title and reviewed edition label;
there are no generic "Work and editions" / "Edition record" links and no
self-link for the current page. Supporting-source citations preserve a path
back to their corresponding supporting reading view. The breadcrumbs are
ordinary links and stay responsive without scripts, CSS framework or sidebar.
They are separate from the source document's internal contents outline.

For catalog-created reading views, the source H1 and authored byline precede
the compact canonical-source and citation guidance. The full revision, path,
and passage anchors remain available and unchanged; moving viewer-generated
bibliographic chrome must never rewrite signed source content. Standalone
`guavajam render` continues to have its independent original reading layout.
In print, breadcrumb navigation is omitted, while citation/provenance remains
readable. Source identity is canonical regardless of the breadcrumb labels,
landing URLs or CSS. Do not replace source anchors with generated heading IDs.

The visual reference remains the restrained Press bibliographic-cover idiom:
serif title/text, small sans-serif navigational metadata, paper/ink palette,
underlined blue links, single-column layout and clear provenance. The design
references retained from the original Press work/edition landing-page contract
are Zenodo version distinction, arXiv title-first covers, W3C status notices,
GOV.UK labeled publication facts and DataCite DOI display conventions. This
feature does not claim pixel-for-pixel parity with the retired generator.

The index links work records and work DOI URLs. Work records link edition
records and the explicitly recommended reader. Edition records link the reader,
supporting source documents, citation instructions and source downloads.
A concept DOI is never advertised as an exact edition DOI. A supporting document
is not falsely assigned its parent work's `citation_doi`; the relation is exposed
as `DC.relation` instead. Landing and reader pages use appropriately scoped
Highwire/Dublin Core metadata, while the collection index has no single
`citation_doi`. No JSON-LD `<script>` or client-side clipboard code is needed.

DOIs display as complete clickable `https://doi.org/...` URLs. `rel="canonical"`
is an SEO choice for a generated webpage, not the edition's source identity.
A stylesheet or host change can regenerate the presentations without issuing a
new source publication.

## Output and deployment

```text
index.html
works/<work>/index.html
works/<work>/editions/<full-revision>/index.html
works/<work>/editions/<full-revision>/citation.json
works/<work>/editions/<full-revision>/read/index.html
works/<work>/editions/<full-revision>/read/cite.html
works/<work>/editions/<full-revision>/read/cite.json
works/<work>/editions/<full-revision>/read/source.gfm
works/<work>/editions/<full-revision>/read/supporting/<document>.html
works/<work>/editions/<full-revision>/read/supporting/<document>.cite.html
works/<work>/editions/<full-revision>/read/supporting/<document>.cite.json
works/<work>/editions/<full-revision>/read/supporting/<document>.gfm
works/<work>/editions/<full-revision>/read/VIEW.json
sitemap.xml
feed.xml
CATALOG.json
SITE.json
```

`read/` is retained for `available`, `superseded`, and `withdrawn`; only explicitly `unavailable` editions omit it. The Atom feed is omitted for an
empty catalog. Feed dates are day-precision catalog dates represented at midnight
UTC for Atom; they are not claims about an actual issuance instant. Entry URLs
are presentation identifiers, not new publication identities. Citation helper
pages are noindexed and omitted from the sitemap.

`SITE.json` hashes every other emitted file and records the catalog digest and
source identities. `VIEW.json` describes the actual decorated reading files, not
the intermediate standalone views. These are reproducibility/integrity aids,
not signed publication receipts. Source signatures are checked; registrar state,
media byte integrity, media retention and external viewer availability are **not**
checked by GuavaJam. Those remain publisher/storage responsibilities. Media still
uses the existing native-player and CSP behavior; media files are not copied into
the site.

Publish the **entire completed tree atomically**, including deletions. Never
upload it as an additive overlay: otherwise an explicitly unavailable edition's former reader
could remain public. Do not remove withdrawn or superseded readers on status change. Retain the prior catalog independently for the next build.
Keep a publisher-owned stable URL for DOI landing pages, serve each page without
requiring login, and maintain routes across future hosting changes. The host
should serve `.html` as HTML, `.gfm` as `text/plain; charset=utf-8`, JSON/XML with
their proper types, and `X-Content-Type-Options: nosniff`. Directory index routes
must work for sitemap/DOI target URLs, including any configured base path.

GuavaJam neither modifies registrar records nor signs, merges or publishes source.
Updating DOI resolver targets and notices requires a separate authorized operator
step. Do not aim a DOI at the registry home page or directly at a raw download.
Render each DOI's specific work/edition landing page, then update its registrar
record only after deployment and end-to-end checks succeed.

When migrating an existing DOI site, inventory its assigned identifiers and
current targets before the first catalog build. `--initial-catalog` cannot check
bindings stored in a legacy registry. Preserve existing DOI/page routes or
configure reviewed redirects to the corresponding same-item landing pages.
Do not reinterpret a DOI for an older canonical HTML/release artifact as a DOI
for a different signed source commit. Keep its legacy record accessible until
an explicitly reviewed compatibility/migration plan exists; this generator
accepts signed-source editions, not legacy release receipts. Host migration and
changes to presentation do not authorize rebinding the identified work.

The first profile bounds catalog input to 1 MiB/256 editions and generated output
to 512 MiB/20,000 files. These are build resource bounds, **not media size limits**.
Larger collections can be split into catalogs; supporting pages can make output
large even when media is streamed.

## Qualification

`make test` checks the schema, real publication signatures, DOI scope, persistent
bindings, notices, literal text escaping, source downloads, exact passage targets,
determinism, site limits and failure behavior. `make test-browser` also runs the
catalog journey in Chromium, Firefox and WebKit with page JavaScript disabled,
then separate axe instrumentation with the original CSP intact. It exercises
registry-to-work-to-edition-to-passage navigation, downloads, keyboard use, 320px
reflow, enlarged text, light/dark modes, print styles and tombstones, retained historical readers, withdrawal/supersession notices,
source downloads and status visibility in print.

The synthetic fixture can be generated with
`python projects/view/tests/run_catalog_browser.py --prepare /tmp/catalog-fixture`. It uses
throwaway signing keys outside the rendered site and does not register its example
DOIs. Never deploy that fixture as a real publication catalog.

Relevant primary guidance (reviewed November 2026):

- Crossref landing pages: https://www.crossref.org/documentation/member-setup/creating-a-landing-page/
- Crossref DOI display and citation links: https://www.crossref.org/display-guidelines/
- DataCite landing pages: https://support.datacite.org/docs/landing-pages
- DataCite tombstones: https://support.datacite.org/docs/tombstone-pages
