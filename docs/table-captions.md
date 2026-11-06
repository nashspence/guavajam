# Table captions

In a work manuscript, put a caption directive immediately after a top-level GFM
pipe table, with **exactly one empty source line** between the last table row and
the directive:

```markdown
| Symbol | Meaning |
|--------|---------|
| S      | Set     |

: Table 1 — Notation
```

The directive is one unindented source line beginning with exactly `: Table `,
followed by nonempty plain text. `Table` belongs to the visible caption. Supply
all wording and numbering yourself; a number and an em dash are optional.
The compiler emits a native HTML `caption` inside the table. It removes the
directive paragraph and adds no label, counter, table number or caption ID.
Ordinary prose labels without the directive remain ordinary paragraphs.
Captionless tables keep their existing GFM semantics.

Caption text may include Unicode punctuation and ordinary spaces. As in normal
HTML prose, runs of spaces collapse. Keep the directive on one source line; a
following paragraph needs its own blank line. Version 1 permits no Markdown
formatting, links (including automatically linked URLs), code, math, HTML,
entities that change the parsed text, backticks or backslash escapes. The
characters `*`, `_`, `~`, `[`, `]`, backtick, `$`, `<`, `>`, backslash and tab are
reserved in caption directives. Use literal Unicode text, such as `©`, rather
than an HTML entity. Additional inline syntax needs a separate source-format
change.

Zero or multiple blank lines, intervening prose/comments/definitions, nested
tables, duplicate directives, indentation and escaping cannot attach a caption.
Caption-looking directives fail validation even when they are orphaned; they
are never silently published as prose. Other colon-prefixed caption variants
in the table's caption position also fail. Fenced code and block quotes stay
literal and never acquire captions. Indented code stays literal too, except
that an apparent indented directive directly after a table is rejected with an
instruction to remove the indentation.

Diagnostics identify the manuscript and original source line. For example:

- `works/essay/manuscript.md:42: table caption: use a standalone line immediately after a top-level GFM pipe table, with exactly one blank line between`
- `works/essay/manuscript.md:42: table caption: plain text only; remove Markdown, math, HTML, backticks and backslash escapes`
- `works/essay/manuscript.md:42: table caption: cannot prove the exact table span; remove intervening definitions, prose or ambiguous blocks`

The existing pinned GFM reader remains authoritative. The shared manuscript
parse boundary checks the directive against its native table/paragraph pair.
Temporary source markers must round-trip the complete AST unchanged, and a
marker at each computed table start must prove the exact table span. Uncertain
correspondence fails validation rather than guessing a table. The builder uses
the same normalized parse before its existing validation, image handling and
accessible scroll wrapping. The caption compiler is included in the committed
builder identity; no raw HTML permission or browser script is needed.
