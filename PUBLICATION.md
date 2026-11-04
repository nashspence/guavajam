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
Canonical artifact: example-essay-v1.0.0.pdf
Rights: All rights reserved
```

The canonical artifact must be uploaded as a **release asset** and its exact filename must match the release notes. The artifact may be PDF, EPUB, Markdown, or another suitable published format. If several formats are provided, identify exactly one as the canonical edition and describe the others as alternate formats. The GitHub-generated source ZIP/tarball is **not** canonical; it contains the entire repository at the tagged commit.

A specific edition may grant a different license; give the precise terms and accompanying license file or link. A default rights notice is in [COPYRIGHT.md](COPYRIGHT.md).

## Publication procedure

1. Draft and review a manuscript in `works/<slug>/`. Ensure the versioned source state is committed.
2. Build or export the final canonical artifact; verify its contents, title, and version.
3. In GitHub, create a **draft release** with a new tag `works/<slug>/vMAJOR.MINOR.PATCH` pointing at the reviewed source commit.
4. Fill in the required metadata using the release-notes template, and upload the complete canonical artifact (plus any alternate formats).
5. Review the draft, then explicitly **publish as a full release**, not as a prerelease. Prefer enabling GitHub release immutability **before** the first publication.
6. Add the published edition and its release link to [CATALOG.md](CATALOG.md). Catalog updates document publication; they do not themselves create it.

The [release-check workflow](.github/workflows/check-release.yml) validates metadata and assets **after** a release is published. A failed check is a warning to correct the publication record; it cannot block a GitHub release in advance.

## Corrections and supersession

Do not silently alter an official edition. Publish a new version for corrections or substantive revisions and explain its relationship to earlier editions. Record any withdrawal or supersession clearly in the catalog and release notes.

## Public visibility is not confidentiality

Unreleased material in a public repository can be seen, copied, or archived. Journals, contests, and publishers may regard such material as previously published even if this imprint calls it a draft. Keep sensitive or first-publication-dependent work in a private workspace until you are ready to disclose it.

## Source compliance

Manuscript sources **must** follow [FORMAT.md](FORMAT.md), including the required in-manuscript YAML front matter, and pass the **Validate manuscript sources** check at the reviewed revision before being tagged for publication. This is an editorial requirement in this protocol; the repository still needs an active branch ruleset to enforce merges, and a separate release gate for pre-publication enforcement. The post-publication release checker cannot prevent a release.

## External identifier registration

Manuscripts now use [the strict `press.work.v1` schema](FORMAT.md). Its optional work-level `author_orcid` and `identifiers` fields carry only real assigned identifiers. Do not place edition/format ISBNs or version DOIs in manuscript metadata.

After an official GitHub Release is published, record its canonical artifact filename, SHA-256 digest, release URL, publication timestamp, and any issued version DOI, ISBN, or catalog record number in the separate [registration index](REGISTRATIONS.md). Work IDs discovered later may be entered into a work registration record without changing an immutable issued edition. Author/imprint and serial identifiers have different scopes.

The registry validates ID syntax, checksums, scope and deterministic serialization **offline**; it does not verify registry ownership, availability, release existence, or file digests against GitHub release assets. Registering an identifier is not itself publishing an edition. No identifier is invented or issued by this repository.
