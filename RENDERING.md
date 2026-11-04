# Canonical HTML presentation profile — `press.html.v1`

This document specifies **the release artifact**, not how GitHub renders working
Markdown. The authoritative HTML is produced by [`scripts/build_edition.py`](scripts/build_edition.py),
validated by [`scripts/verify_edition.py`](scripts/verify_edition.py), and covered
by executable DOM and tamper-resistance tests in
[`tests/test_render_contract.py`](tests/test_render_contract.py).

## Versioned presentation sources

- [`templates/edition-v1.html`](templates/edition-v1.html): Pandoc standalone
  HTML5 template, including one `<main><article>` reading region, document
  metadata, Content Security Policy, and a publication footer.
- [`styles/edition-v1.css`](styles/edition-v1.css): responsive typography,
  automatic light/dark appearance, print rules, and formal-block styles.
- [`scripts/render_profile.py`](scripts/render_profile.py): controlled
  transformation of explicit source anchors and recognized formal headings.
- Pandoc **3.12.1**, with `--from=json --to=html5 --standalone --mathml
  --fail-if-warnings` and the exact versioned template. Source parsing still
  follows [FORMAT.md](FORMAT.md).

The builder reads the **exact committed bytes** of these files, verifies that
its own renderer/verifier and presentation sources match the recorded builder
commit, embeds the stylesheet directly inside `<style id="press-edition-css">`,
and records SHA-256 of the **original** template and stylesheet in provenance.
CSS cannot load remote resources or contain active script-like expressions.
Every official edition is a standalone UTF-8 HTML file: there is no external
stylesheet, font, image, JavaScript, or runtime CDN dependency.

## Stable semantic contract

Ordinary Markdown uses native HTML5 elements: `h1`–`h6`, `p`, `strong`, `em`,
`blockquote`, `ul`/`ol`/`li`, `pre`/`code`, `table`, `img`, and links. Mathematics
is MathML. Pandoc is pinned because finer markup (particularly tables,
footnotes, figures, code highlighting, and MathML internals) is not a permanent
cross-version byte-level or DOM-nesting guarantee.

All H2–H6 manuscript headings must have explicit preceding stable `<a id="...">`
anchors under [FORMAT.md](FORMAT.md). In the **published HTML only**, the
controlled AST transform removes those standalone anchor paragraphs and moves
their IDs directly onto the corresponding heading: `<h2 id="sec-1">`.
This makes citation targets stable across heading renames and independent of
Pandoc's generated slug algorithm. Working GitHub Markdown continues to render
those author-written anchors normally. The H1 retains its Pandoc-generated ID,
which is not promised as a persistent citation address.

Formal headings beginning with `Definition`, `Theorem`, `Proposition`, `Lemma`,
`Corollary`, `Proof`, `Axiom`, `Remark`, or `Example` (optionally followed by
numerical levels and ` — Caption`) receive, for example:

```html
<h3 id="def-1" class="press-formal-heading press-definition"
    data-press-kind="definition">Definition 1.1 — Identity</h3>
```

These classes and `data-press-kind` are assigned **only by the builder**; authors
continue to write ordinary Markdown headings. The explicit anchor ID is the
citation identifier. Namespaced classes and attributes are publisher-owned
presentation hooks; other incidental Pandoc classes and generated IDs must not
be relied upon in styles, links, or external tooling.

The generated HTML must have exactly one `<h1>`, one `main`, one profile-tagged
`article`, one embedded style block, exactly one restrictive CSP declaration,
a responsive viewport, a nonempty language declaration, unique HTML IDs,
correctly resolved internal `#fragment` links, and verified embedded images.
Unknown formal `data-press-kind` values fail validation.

## Reproducibility and provenance

The official release is a three-file package:

```
<slug>-vMAJOR.MINOR.PATCH.html
provenance.json
SHA256SUMS
```

The manifest contains `presentation_profile: press.html.v1`, the full
`source_commit` and **independent** full `builder_commit` SHAs, the pinned
Pandoc version, the template and stylesheet paths and their SHA-256 values,
the canonical HTML SHA-256 and byte count, and verified image provenance.
The three-file package is checked offline before staging, then re-downloaded
and checked again before official publication. SHA256SUMS covers HTML and
provenance, and the embedded stylesheet must match the manifest digest.

The builder commit matters because publishing tooling can evolve independently
of the manuscript's source commit. To rebuild an edition, use the exact builder
commit, source commit, compiler and asset bytes recorded in provenance.
The contract requires identical bytes across repeated builds with identical
inputs and toolchain; the CI tests check this on a fixed fixture.

Changes that alter these normative tags, classes, attributes, templates or
CSS should intentionally introduce a **new** `press.html.vN` profile rather
than silently changing the interpretation of this one. Existing issued HTML
files never update to follow newer site styles. The GitHub Pages catalog, if
created, can use independent site-wide styling without changing the canonical
release asset.
