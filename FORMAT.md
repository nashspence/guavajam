# Press source format — v1

This is the **normative executable authoring contract** for manuscripts in `press`. The reference implementation is [`scripts/validate_sources.py`](scripts/validate_sources.py), with identifier checks in [`scripts/identifier_schema.py`](scripts/identifier_schema.py) and positive/negative regression cases in [`tests/test_validate_sources.py`](tests/test_validate_sources.py). [`PUBLICATION.md`](PUBLICATION.md) defines official editions; [`REGISTRATIONS.md`](REGISTRATIONS.md) defines the external-identifier registry.

## Required and permitted manuscript fields

Each work lives at `works/<slug>/manuscript.md`. Its YAML front matter must begin at byte zero with `---`, end with `---`, then have **exactly one blank line** before the Markdown body. There may be at most 128 front-matter lines. The seven required fields below are the only required fields; there are **exactly two optional fields**. All fields other than `identifiers` are nonempty, single-line YAML strings. Field order is free.

| Field | Required | Rule |
| --- | --- | --- |
| `schema` | Yes | Exactly `press.work.v1` |
| `title` | Yes | Exactly equals the manuscript's rendered plain-text H1 |
| `author` | Yes | Nonempty string |
| `slug` | Yes | Must equal the lowercase hyphenated `works/<slug>/` directory |
| `language` | Yes | Two- or three-letter lowercase language code, optionally `-XX` region (syntax only) |
| `status` | Yes | Exactly `draft` (formal publication belongs to releases) |
| `rights` | Yes | One of `all-rights-reserved`, `CC-BY-4.0`, `CC-BY-ND-4.0`, `CC-BY-NC-ND-4.0`, `CC0-1.0` |
| `author_orcid` | No | Canonical bare ORCID, e.g. `0000-0002-1825-0097`; ISO 7064 check digit validated |
| `identifiers` | No | Nonempty two-space-indented mapping; only the four work identifiers below |

The optional `identifiers` mapping permits **only**:

| Identifier key | Purpose and canonical form |
| --- | --- |
| `concept_doi` | Registered DOI for the evolving *work across editions*: lowercase bare DOI such as `10.5281/zenodo.1234567`, not a URL |
| `arxiv` | Base (unversioned) arXiv identifier, e.g. `2501.01234` or `hep-th/9901001`, without `arXiv:` |
| `openalex` | OpenAlex work ID, e.g. `W2741809807` |
| `wikidata` | Wikidata work Q-ID, e.g. `Q123456` |

These are **real external registry identifiers**. Never invent or reserve them by placing arbitrary values in source. Omit fields until actually assigned or indexed. The validator checks formats and relevant checksums **offline**, not whether a registry recognizes the identifier or whether the entity is the intended one.

### Front-matter example

```markdown
---
schema: press.work.v1
title: On Structure and Identity
author: Nash Spence
slug: structure-and-identity
language: en
status: draft
rights: all-rights-reserved
# author_orcid: "<assigned ORCID>"
# identifiers:
#   concept_doi: "<registered DOI>"
#   arxiv: "<assigned arXiv ID>"
#   openalex: "<indexed W-ID>"
#   wikidata: "<indexed Q-ID>"
---

# On Structure and Identity

<a id="sec-1"></a>
## § 1 — Foundations

Let $S$ be a set, and let $x \in S$.
```

Optional identifier lines are **comments in this example**: remove the leading `#` only when the corresponding identifiers exist. Quoted values and comments are allowed. Empty optional mappings are forbidden.

### Strict YAML profile

- Root metadata is a mapping of `key: single-line-string` entries, plus the **one permitted nested mapping** `identifiers:` using exactly two spaces for its entries. No other nesting or fields are permitted.
- Duplicate keys (including nested identifier keys), empty fields, missing required keys, unknown keys, sequences, aliases, anchors, merge keys, directives, custom tags, and multiline/folded scalars fail validation.
- Quote any scalar that YAML might parse as a number, boolean, date, or null. Nested identifier values must be strings, and Pandoc must interpret **every key and value identically** to the YAML parser.
- `author_orcid` uses bare hyphenated form, with a validated check digit. IDs appear as raw registry strings (no DOI/ORCID/OpenAlex URL prefixes). The `identifiers` set is deliberately small and must be extended explicitly in a future schema version.
- A work-level concept DOI is *not* an edition DOI. ISBNs identify publication products and therefore **do not belong** in manuscript metadata. See [`REGISTRATIONS.md`](REGISTRATIONS.md).

## Manuscript body, assets and symbols

The following strict manuscript source restrictions apply:

- UTF-8, NFC Unicode, LF line endings, no BOM, no trailing whitespace, exactly one terminal newline; selected bidi override/control characters are prohibited.
- Dialect: Pandoc **3.12.1** with `gfm+tex_math_dollars+raw_html+yaml_metadata_block`. The parser must emit no warnings. GitHub's Markdown renderer is not promised to be byte-identical.
- One ATX H1 as the first heading, no skipped heading levels. Every H2–H6 must have a unique standalone `<a id="stable-id"></a>` directly before it or separated by one blank line; other raw manuscript HTML is prohibited.
- Inline math `$...$`; display math uses paired `$$` on separate lines. Rendered MathML is checked. Use literal Unicode for prose symbols and mathematical TeX for mathematics.
- Footnote definitions and references must match. Local links point to tracked files; fragments resolve. External text links allow `https:` or `mailto:`. Remote images must use the explicitly approved HTTPS origin and exact content-addressed grammar below.
- Existing local images under works/<slug>/assets/ remain supported for historical manuscripts, with descriptive alt text and the existing filename/type/10 MiB rules. They are deprecated for new authoring; use approved HTTPS references. Edition builds verify and embed historical local images too.
- Each work directory contains `manuscript.md`, optionally `README.md` and assets only. There is no `metadata.json`.

## Content-addressed image references

The approved origin is the origin value in [asset-policy.json](asset-policy.json);
see [ASSETS.md](ASSETS.md) for the public hostname, upload and storage policy.
Each image destination must be exactly:

~~~text
<approved-https-origin>/sha256/<64-lowercase-hex>.(png|jpg|jpeg|webp|svg)
~~~

The digest is SHA-256 of the **exact object bytes**. Extensions are media hints,
not evidence of content. Ordinary inline, full-reference, collapsed-reference and
shortcut-reference Markdown images use the same parsed-destination checks.
Images require descriptive alt text. No per-work asset YAML is used.

Every other remote destination is rejected, including alternate origins,
non-HTTPS schemes, userinfo, ports (including explicit 443), queries, fragments,
percent escapes, ambiguous authority/path spellings, extra path components,
unsupported extensions and uppercase or malformed hashes. Raw manuscript HTML
remains prohibited except for the existing stable section anchors.

Source syntax validation is entirely offline. The separate required network
check on every push and pull request downloads and verifies these objects without
storage credentials. A release build downloads and verifies again before issuing
the edition; missing, unavailable, oversized, tampered, mismatched or unsafe
images fail closed. See [ASSETS.md](ASSETS.md) for exact media and download limits.

To migrate an existing local image, verify/hash its bytes, upload them under the
digest-derived key, and replace the manuscript reference after checking the
public URL. Remove an unreferenced local file only from a new source revision;
never rewrite historical commits or issued editions. Local references remain
valid until an explicit future format change; this policy has no silent cutoff.

## Executable validation

CI runs on pushes, pull requests and manual dispatch, validating the **entire tracked tree**, not just changed files:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/validate_sources.py
python3 scripts/verify_source_assets.py  # networked cryptographic check
```

The validator checks source, metadata, Pandoc interpretation, HTML5/MathML, assets and external-identifier **syntax**. It additionally validates any tracked external registry records as specified in [`REGISTRATIONS.md`](REGISTRATIONS.md). CI pins Pandoc 3.12.1, PyYAML 6.0.3 and Pillow 13.0.0. The source
validator and its tests stay offline; the separate asset job deliberately depends
on the approved origin being available.

The active main-branch ruleset requires **Validate manuscript sources** and
**Verify manuscript assets**. Passing these checks does not prove authorship,
registry ownership/existence, mathematical truth or accessibility. Publication
also requires the protected [edition workflow](PUBLICATION.md), which verifies
the exact source commit, tag and packaged artifact before publication.
