# Publication protocol

This document defines which revisions the **press** imprint designates as official editions. It is an editorial and technical protocol, not a guarantee that public drafts are legally unpublished or eligible for first-publication submissions elsewhere.

## Status model

1. **Working material:** Branches, commits, manuscripts, and bare Git tags are publicly accessible development history, **not official editions**.
2. **Release candidate:** Draft GitHub Releases and prereleases are not official editions.
3. **Official edition:** A GitHub Release published as **non-draft and non-prerelease**, using the naming and metadata rules below, with a designated canonical attached artifact.

Publishing a release is the deliberate editorial act. A tag by itself is only a version reference.

## Version and tag convention

Use `works/<slug>/vMAJOR.MINOR.PATCH`, for example `works/example-essay/v1.0.0`.

- The slug is lowercase letters, digits, and hyphens, matching `works/<slug>/`.
- The first official edition normally starts at `v1.0.0`.
- MAJOR denotes a substantially new edition, MINOR a meaningful revision, and PATCH corrections such as typos or citations. These are editorial conventions rather than a claim of software compatibility.
- Never move or reuse a tag associated with an official edition.

## Required release metadata

Use [RELEASE_NOTES_TEMPLATE.md](RELEASE_NOTES_TEMPLATE.md) as a starting point. Each official release must specify the following lines in its release notes:

```text
Work: Title of work
Edition: v1.0.0
Canonical artifact: example-essay-v1.0.0.html
Rights: All rights reserved
```

The canonical artifact must be uploaded as a **release asset** and its exact filename must match the release notes. The canonical artifact is a self-contained responsive HTML edition with embedded
verified images. Attach generated provenance.json and SHA256SUMS alongside it.
The [versioned HTML presentation profile](RENDERING.md) uses separate committed
CSS and template sources but embeds the stylesheet into each immutable HTML
edition. The provenance manifest binds the source commit and independently
records the publishing-tool commit, profile, template digest and CSS digest.
PDF, EPUB and other formats may be provided as alternates. If several formats are provided, identify exactly one as the canonical edition and describe the others as alternate formats. The GitHub-generated source ZIP/tarball is **not** canonical; it contains the entire repository at the tagged commit.

A specific edition may grant a different license; give the precise terms and accompanying license file or link. A default rights notice is in [COPYRIGHT.md](COPYRIGHT.md).

## Publication procedure

1. Commit and review a manuscript in works/<slug>/. Both **Validate manuscript
   sources** and **Verify manuscript assets** must succeed at the exact full
   commit SHA. They run on every push and pull request and are required on main.
2. From main, dispatch [Prepare verified edition](.github/workflows/prepare-edition.yml)
   with that full SHA and the intended works/<slug>/vMAJOR.MINOR.PATCH tag.
   Leave **publish** false to prepare a draft. The commit must belong to main's
   history; an existing tag must already resolve to that exact commit.
3. The read-only build validates the committed snapshot, downloads and verifies
   each asset, embeds its bytes in canonical HTML, and generates provenance and
   checksums. A failed build cannot reach the release job. The workflow artifact
   is available for inspection before approving the release job.
4. Approve the **press-release** environment deployment. It is restricted to main
   and requires the imprint owner's approval. Its contents-write job verifies
   the package, stages a draft, and downloads all three attachments for another
   byte-for-byte/offline verification. Storage credentials are never needed.
5. For official publication, dispatch with **publish** true and approve the
   environment after reviewing the built edition. The gate refuses changed tags,
   failed source/asset checks, differing attachments, or an already issued
   edition. This explicit operation publishes the verified draft as a full release.
   Do not bypass this gate by publishing through the GitHub UI.
6. Record the edition in [CATALOG.md](CATALOG.md) and any assigned identifiers in
   [REGISTRATIONS.md](REGISTRATIONS.md). Prefer GitHub release immutability before
   the first official publication. Never overwrite an issued artifact.

For local inspection without creating tags or releases:

~~~bash
mise x -- uv sync --locked
mise x -- uv run --locked python scripts/build_edition.py --commit FULL_SHA --tag works/example-essay/v1.0.0 --output dist/edition
mise x -- uv run --locked python scripts/verify_edition.py dist/edition --commit FULL_SHA --tag works/example-essay/v1.0.0
~~~

Pandoc 3.12.1 must be on PATH. The output directory must not already exist.
Only committed sources enter the build; dirty or untracked files do not.

The [release checker](.github/workflows/check-release.yml) is advisory and runs
after publication when GitHub emits the event. It is **not a pre-publication
gate**. Releases created using GITHUB_TOKEN may not trigger another workflow;
verification is already required by the publishing job. Manually created
releases can still bypass repository procedures, so they are not an approved
publication path.

See [ASSETS.md](ASSETS.md) for URL syntax, storage, verification, independent
copies, the monthly audit, and provider migration.

## Corrections and supersession

Do not silently alter an official edition. Publish a new version for corrections or substantive revisions and explain its relationship to earlier editions. Record any withdrawal or supersession clearly in the catalog and release notes.

## Public visibility is not confidentiality

Unreleased material in a public repository can be seen, copied, or archived. Journals, contests, and publishers may regard such material as previously published even if this imprint calls it a draft. Keep sensitive or first-publication-dependent work in a private workspace until you are ready to disclose it.

## Source compliance

Manuscript sources must follow [FORMAT.md](FORMAT.md), including the unchanged
press.work.v1 bibliographic schema. Both required Actions checks must succeed at
the reviewed commit. The trusted workflow verifies image bytes again immediately
before packaging and publication; an older successful push check never
substitutes for that verification.

## External identifier registration

Manuscripts now use [the strict `press.work.v1` schema](FORMAT.md). Its optional work-level `author_orcid` and `identifiers` fields carry only real assigned identifiers. Do not place edition/format ISBNs or version DOIs in manuscript metadata.

After an official GitHub Release is published, record its canonical artifact filename, SHA-256 digest, release URL, publication timestamp, and any issued version DOI, ISBN, or catalog record number in the separate [registration index](REGISTRATIONS.md). Work IDs discovered later may be entered into a work registration record without changing an immutable issued edition. Author/imprint and serial identifiers have different scopes.

The registry validates ID syntax, checksums, scope and deterministic serialization **offline**; it does not verify registry ownership, availability, release existence, or file digests against GitHub release assets. Registering an identifier is not itself publishing an edition. No identifier is invented or issued by this repository.
