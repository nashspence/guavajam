# Publisher-driven, periodic updates

`make update` is a one-shot operation designed to be invoked periodically. It
reads Press conventions and generates the catalog itself. There is **no second
hand-maintained GuavaJam catalog** in this mode. Manual `make site` and
`make render` remain supported.

## Responsibilities and defaults

The publisher owns one `press-site.json` in the source repository. The deployment
operator owns the trusted signer policy, local source checkout, and web server.
Trust cannot safely be derived from the repository it is supposed to authorize.
No signing key, registrar credential, B2 credential or site template goes in the
source-owned settings or generated site.

Default conventions are:

| Input | Default |
| --- | --- |
| Local source repository | `../press` relative to the GuavaJam checkout |
| Remote / publication branch | `origin`, `refs/heads/main` |
| Publisher settings | `press-site.json` in the selected committed history |
| Managed output root | `.build/site` |
| Public document root | `.build/site/current` |
| SSH trust | `~/.config/guavajam/allowed_signers` |
| Alternative GPG trust | independently provisioned keyring and `TRUSTED_GPG_FINGERPRINT` |

Use an operator-created normal Git clone, not an unreviewed archive with arbitrary
local `.git/config`, hooks or credentials. The updater never executes the source
repository's Makefile, Python modules, templates or committed programs. Git remote
and transport configuration belong to the deployment account. It fetches only
the configured branch to a dedicated private ref, without force, pruning, tags,
submodules, checkout or reset. `NO_FETCH=1` instead uses the named local branch
for an independently refreshed mirror. Shallow histories are rejected.

After one-time publisher and deployment setup, the periodic invocation is simply:

```sh
make update
```

Optional location overrides are `REPO`, `OUTPUT`, `SOURCE_REF`, and `REMOTE`.
`SSH_ALLOWED_SIGNERS` may specify the externally managed file instead of its
conventional path. Use exactly one SSH or GPG trust policy. GPG fingerprints in
the environment can be whitespace-separated for an explicitly approved key
rotation; the corresponding public keys must already be in the operator's
keyring. No key is imported from the source repository.

## Publisher-owned settings

The minimal new-site configuration is:

```json
{
  "schema": "press.site.v1",
  "publisher": "Example Imprint",
  "base_url": "https://publisher.example"
}
```

Use the actual publisher and deployment origin, never deploy these example
values. Optional fields are `title`, `language` (currently `en`), `description`,
`source_view`, `works`, and `editions`. The first five describe the site; the last
two contain only administrative overrides. They are not an index to duplicate.
Press ships `templates/site.json`, a JSON Schema, executable validation tests,
and `make site-export` to emit exactly `guavajam.catalog.v1` for other consumers.

Site settings must be reviewed and committed, then authorized with a separate
**empty signed checkpoint** on the accepted history:

```sh
git add press-site.json
git commit -m 'Review presentation settings'
git commit --allow-empty -S -m 'Authorize presentation settings' -m 'Press-Site: 1'
```

The checkpoint has one parent and the same tree as that parent. Its final marker
means approval of `press-site.json`, **not publication of a new edition**. A
nonempty checkpoint, untrusted/unsigned checkpoint, or changed settings without a
new checkpoint fails closed. Multiple checkpoint branches must be reconciled
into one ancestry chain. Publisher code/README commits need no site checkpoint
when the settings blob is unchanged.

## New publications

The existing v1 signed-publication contract remains valid. New automatically
indexed publications use the explicit dated v2 trailer block:

```text
Publish Field Notes

Press-Publication: 2
Press-Work: works/field-notes/manuscript.md
Press-Published: 2026-11-13
Press-Label: First edition
```

`Press-Label` is optional; the date is not. The declaration must occupy one final
trailer block. This is still an empty, single-parent, signed commit preserving
the reviewed source tree. The date is a publisher-declared edition date, not an
independent timestamp attestation or a DOI-assignment date. No dates are guessed
from Git author/committer times. `make publication-prepare` in Press accepts
`PUBLISHED=YYYY-MM-DD` and optional `LABEL=...`, returning the exact trailers after
its existing source/asset/retention checks. Signing and pushing remain explicit
publisher operations; neither export nor update signs anything.

The updater discovers and verifies every declared publication reachable from the
selected branch. It does not scan all branches or use tag annotations. Signed
commits on an unmerged branch are not listed. Unsupported or malformed Press
markers fail the update rather than disappear from the index. Preserve original
signed publication commits during integration: rebasing/squashing would change
their identity. Retain the full Git history independently.

New v2 publications need **no catalog edit**. All editions default to available;
none is automatically declared superseded. Per work, the metadata basis defaults
to the first accepted publication in topological history, and the recommendation
defaults to the latest available publisher-declared edition date, with accepted
topological order breaking same-day ties. These are explicit opt-in conventions
of `press.site.v1`, not conclusions about original work-publication dates. Work
pages still do not manufacture historical first-publication dates.

## Later DOI, notice and presentation decisions

Administrative decisions stay in the publisher's current settings, followed by a
new signed site checkpoint. Neither the old source nor its signed commit changes.
For example (revision strings below are schematic, not real publications):

```json
{
  "schema": "press.site.v1",
  "publisher": "Example Imprint",
  "base_url": "https://publisher.example",
  "works": {
    "field-notes": {
      "concept_doi": "10.1234/field-notes",
      "recommended_revision": null
    }
  },
  "editions": {
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa": {
      "doi": "10.1234/field-notes.v1",
      "status": "withdrawn",
      "notices": [
        {"date": "2026-11-14", "text": "Withdrawn after review; historical access is retained."}
      ]
    }
  }
}
```

Work overrides may set `concept_doi`, `metadata_revision`, and
`recommended_revision`; null explicitly disables the default recommendation.
Edition overrides may set `published`, `label`, `doi`, `status`, `notices`, and
`successor_revision`. Unknown work/revision overrides fail. Changing an explicitly
signed publication date/label requires an explanatory notice. Existing DOI
bindings and append-only notices are checked against the last deployed catalog.
Withdrawal and supersession retain historical readers, supporting pages,
source downloads and citations. Only explicitly `unavailable` editions omit
reader distribution. No registrar updates happen automatically.

## Migration from a manual catalog or older publications

Use Press's migration helper once:

```sh
make site-import CATALOG=/trusted/previous-site/CATALOG.json
```

Review `.build/press-site.json`, then place it at the source repository root,
commit it, and sign a site checkpoint. It preserves explicit metadata basis,
recommendations (including their intentional absence), DOIs, dates and notices.
Every legacy v1 publication requires a reviewed `published` override because its
signature did not declare that date. Do not resign/rewrite old publications just
to supply it. A missing legacy date stops indexing with the exact revision that
needs review. Discovery is complete for the selected history: formerly unlisted
publications must be reviewed rather than silently excluded.

For the **first managed deployment of an existing site**, provide the independently
retained old catalog as well:

```sh
make update BASELINE=/trusted/previous-site/CATALOG.json
```

Subsequent runs take their baseline from the managed current generation; omit
`BASELINE`. A new empty website can initialize without a baseline. Do not move to
a fresh output directory to bypass rejected DOI/history updates. Existing
legacy HTML-edition DOI bindings still need a same-item migration plan; automatic
indexing is not authority to reinterpret them as signed-source edition DOIs.

## Efficiency, integrity and atomic publication

Each invocation acquires a nonblocking process lock. An overlapping invocation
returns `busy` successfully without starting another build. It fetches and pins
the branch tip, refuses history rollback, and rechecks every declaration signature
against the current independent policy. That verification is intentionally not
replaced by a cached `verified: true` field.

When the derived catalog, renderer/toolchain and trust inputs are unchanged, no
Markdown conversion, site generation, public-file rewriting or media download
occurs. Ordinary unrelated source commits therefore produce no-op updates.
New builds cache immutable undecorated edition views by source revision and by
renderer/toolchain/trust identity. Adding a publication renders only that new
view; DOI or status changes redecorate verified cached views. Cache integrity is
checked before reuse. Trust changes or renderer/toolchain changes invalidate
reuse. `REBUILD=1` requalifies all views into a fresh cache namespace.

Every changed site is built and link-audited in a private staging directory. A
complete generation and its catalog baseline are installed together; a single
atomic symlink replacement switches `OUTPUT/current`. Existing generations are
retained. Errors before the switch leave the prior generation selected; a crash
cannot select a half-built tree. If state persistence fails after a successful
switch, inspect the complete selected generation before retrying. A missing or
replaced `current` link fails closed whenever retained generations indicate an
existing history, including when the first switch failed to persist `.last.json`.
Restore the reviewed complete generation and its baseline before retrying.
No additive upload overlay is used: explicitly unavailable reader paths disappear from the
selected site, while withdrawn/superseded readers remain.

This target publishes a local filesystem tree. Configure the web server once to
serve **only `OUTPUT/current`**, including directory index routes and the existing
MIME/CSP requirements. Do not serve the parent: it contains old generations and a
private cache. Object-store/CDN deploys need a separate atomic switch/complete
replacement adapter, and CDN purges for status changes; that is not inferred by
this target. The publisher-provided `base_url` is descriptive metadata, not a
request to configure a DNS name or deploy to arbitrary infrastructure.

Keep the managed root and local Git config operator-controlled. Do not edit
generated files or reset its retained baseline. Private caches are disposable;
previous catalog/receipt generations are not. No garbage collection is performed
by this feature: review retention/storage before removing old private generations.
Restore the last complete generation and its state after a storage failure;
never synthesize an empty baseline as recovery. Signature verification, source
validation and catalog history checks remain distinct from media/retention audits,
which continue to be Press's responsibilities.

The initial limits remain 256 editions/1 MiB catalog/512 MiB generated site. A
complete accepted history is bounded to 10,000 commits; exceeding it is an error,
not a partial index. Git subprocesses and signatures have explicit time/output
budgets. These limits do not impose a media-file-size ceiling or copy video into
Git/site generations.

## Scheduling

Run the target with the deployment account's pinned mise/uv toolchain on POSIX.
The sample `examples/guavajam-update.service` and `.timer` demonstrate a five-minute
systemd cadence; they are not installed or enabled by this contribution. Supply
that account's executable PATH and independently managed signer policy. A cron
entry can also call `make update`; locking is already implemented by the target.
Monitor nonzero exits and logs instead of hiding failed signature/history checks.

The tests cover real signed commits, local-origin fetches, no-op behavior,
per-edition cache reuse, lock contention, failed builds, rollback, missing state,
metadata/DOI changes, migration, historical access and corruption recovery.
Pinned CI and the existing three-browser presentation suite must still pass
before production rollout. No new browser JavaScript or presentation authority
is introduced.
