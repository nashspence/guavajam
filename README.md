# GuavaJam

One Git repository, one work in `content/`, optional views. Trusted signed
commits that change `content/` on the selected first-parent history define
editions. The commit identifies the edition; its ordinary message describes
its changes. No edition counters, special commit trailers, or authoring commands.

Initialize a new GitHub work repository **once**, using a local `.env` containing
`GUAVAJAM_REPO=owner/work`, `GUAVAJAM_TITLE`, `GUAVAJAM_AUTHOR`,
`GUAVAJAM_ALLOWED_SIGNERS` (path to an external SSH allowed-signers file),
`GUAVAJAM_TOOL_REF` (full reviewed GuavaJam Git commit SHA), and optionally
`GH_TOKEN` for `gh` authentication:

```sh
uvx --from 'git+https://github.com/nashspence/guavajam.git#subdirectory=projects/core' guavajam-core init work --env .env
```

When publishing, Git must be configured for **SSH signed commits** (`git commit -S`).
At initialization, `gh` must have permissions to create public repositories and Actions secrets.
The initializer commits only repository infrastructure, leaving the new
`content/manuscript.md` untracked as a draft. Its first accepted signed content
change publishes the first edition. Actions checks PR sources and verifies the
accepted history on `main`. It does not retain the environment file or private
signing key. PRs can be reviewed normally; integrate changes with a trusted
signed commit on `main`. A GitHub-generated merge or squash commit is not
authorized unless its signer is separately trusted. Draft branches remain
ordinary Git branches. A fast-forward publishes each signed content-changing
commit; a signed merge publishes its integrated result once.

Initialize a GitHub Pages index repository **once**, using a separate `.env` with
`GUAVAJAM_REPO=owner/collection`, `GUAVAJAM_TOOL_REF`, and optionally `GH_TOKEN`:

```sh
uvx --from 'git+https://github.com/nashspence/guavajam.git#subdirectory=projects/core' guavajam-core init index --env .env
```

The index repository contains only `index.yaml`, independent publisher signer
trust in `trust.yaml` and public key files, and an Actions workflow. Edit and PR
these files normally. The workflow verifies each listed repository, produces
static HTML for GitHub Pages, and records a durable verification checkpoint on a
separate `guavajam-state` branch. It never commits derived HTML. Keep that branch
protected from deletion or force-push; do not reset it to bypass continuity
checks. The index is a list of URLs, not a source of publisher signing authority.

Optional author-declared [keywords and subjects](projects/core/tests/test_subject_metadata.py)
live in work front matter. The [aggregate search contracts](projects/view/tests/test_search_index.py)
and [browser journey](projects/view/tests/test_search_browser.mjs) cover the generated
SQLite artifact, all-edition text search, latest-edition filters and static fallback.
Work views also derive [contents and document navigation](projects/view/tests/test_navigation.py)
and an optional [work search index](projects/view/tests/test_work_search.py), with
[edition/document/passage browser journeys](projects/view/tests/test_work_search_browser.mjs).

Both initializers accept `--output` to choose the local directory and `--local`
to generate a local Git repository without creating a remote. Every later edit
uses normal Git, a text editor, PR review, and Actions. Core can validate,
discover and preserve works without the optional view package.

[Developer onboarding](AGENTS.md). Run `make help` for tooling commands.
