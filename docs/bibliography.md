# Reviewed attribution and bibliographic metadata

The starter's `author` and optional `author_orcid` remain valid. For ordered
coauthors, replace both with an `authors` list. Use each author's own ORCID when
known; names are kept whole, without inferring given names, surnames or roles.
For example, this replaces the single-author field in the starter:

```yaml
authors:
  - name: First Author
    orcid: 0000-0002-1825-0097
  - name: Second Author
```

The identifier above is a syntax example, not an attribution to either example
name. An ordered list has one to 32 name/optional-ORCID entries. Names are plain
text of at most 512 UTF-8 bytes each; an ORCID cannot be assigned to two entries.
Use the two-space list and four-space child indentation shown above. Other
nested structures, aliases, flow collections and extra author fields remain
outside the source format. Single-author metadata keeps its existing strict
YAML/Pandoc text checks. The new author-list values are literal text.

Two optional manuscript fields describe the edition:

```yaml
abstract: 'An author-written, plain-text account of this edition.'
schema_type: CreativeWork
```

The abstract is one nonempty line, at most 4 KiB of UTF-8, without leading or
trailing whitespace. Quote it as needed for YAML. Markdown and HTML-looking text
inside this field stays literal; folded and multiline YAML scalars are not
supported. Its exact reviewed content travels in provenance, the canonical
HTML's description metadata, and the edition record's visible abstract and
descriptive metadata. Omission stays omission. A later edition can carry a
different abstract and authorship.

`schema_type` may be `CreativeWork` or `ScholarlyArticle`. Omit it to emit no
schema.org classification. Opting in annotates the visible edition citation
with static HTML microdata, including each author's associated ORCID when
supplied. This descriptive choice makes no assertion of peer review. It does
not classify the work across editions.

Permanent edition records emit Dublin Core title, ordered creators, publisher,
language, permanent edition identifier and `DCTERMS.issued` from the actual
publication receipt. The assigned edition DOI supplies an additional identifier;
the abstract supplies a description when present. Building or staging an
edition supplies no issuance date. The local preview explicitly simulates it.
Historical provenance without attribution or language contributes only the
facts it captured; regenerating a record does not rewrite the canonical HTML.

The initial work citation derives its title, ordered authors and language from
its chronologically first issued edition. Equal receipt timestamps use the lower
numeric edition version. Subsequent editions do not silently overwrite these
work-level facts. This is an initial derivation, not a permanent first-edition
attribution policy or an automatic latest-edition policy.

For an explicit, reviewed change to the work's bibliographic identity, set
`citation_edition` to an already receipted version in the existing
`registrations/works/<slug>.json` record, and regenerate the work page. This
single reference selects the title, ordered authors and language from that
edition's immutable provenance; it does not introduce independent mutable
bibliographic fields. The work registration can have an empty `identifiers`
object when this selector is its only optional field. Changing or removing the
selector is itself a reviewed bibliographic decision; omission uses the initial
derivation. The original publication date and year always come from the first
issued receipt, including when that edition is withdrawn. Every edition keeps
its own exact reviewed attribution and independently accurate citation.

When a concept DOI exists, review the selected work title and authors against
that DOI's registry metadata and coordinate any registry update with the same
bibliographic decision before merging the regenerated page. Press does not query
or modify DOI registry metadata. This narrow selector supports identities already
represented by an issued edition; an independently composed work identity needs
separate design review rather than inferred fields.

The work citation and machine-readable metadata use Press as publisher and the
current concept DOI when assigned, otherwise the permanent work URL. DOI
assignment, correction and retirement use the existing history mechanism. An
edition DOI identifies its edition and never supplies the work identifier.

The markup follows [DCMI's HTML metadata vocabulary](https://www.dublincore.org/specifications/dublin-core/dc-html/)
and the optional [schema.org CreativeWork](https://schema.org/CreativeWork) /
[ScholarlyArticle](https://schema.org/ScholarlyArticle) descriptions. It requires
no browser script or metadata service. Consult `make help` for the existing
validation, build, preview and record commands.
