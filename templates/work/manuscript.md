---
schema: press.work.v1
title: Sample Work
author: Nash Spence
slug: sample-work
language: en
status: draft
rights: all-rights-reserved
---

# Sample Work

<a id="sec-1"></a>
## § 1 — Definitions and mathematics

Let $S$ be a set. Every element equals itself. In symbols, for $x \in S$:

$$
\forall x \in S,\quad x=x
$$

The surrounding prose remains readable independently of mathematical typesetting.
A **strong assertion**, *emphasis*, ~~a discarded claim~~, and `inline code` illustrate
ordinary Markdown. See [the definition](#def-1) and [the proof](#proof-1).

<a id="def-1"></a>
### Definition 1.1 — Reflexivity

A relation $R$ on $S$ is *reflexive* if each element is related to itself.

<a id="theorem-1"></a>
### Theorem 1.1 — Equality is reflexive

Equality satisfies [Definition 1.1](#def-1) because every element equals itself.

<a id="proof-1"></a>
#### Proof

The identity $x=x$ holds for each element of $S$. Thus the relation is reflexive.

<a id="sec-2"></a>
## § 2 — Lists, quotations, and notes

A bulleted list can contain nested items:

- The underlying set
  - Its elements
- The relation defined on the set

An ordered list expresses a sequence:

1. State the property.
2. Check the property.

A task list distinguishes completed and open checks:

- [x] State the definition.
- [ ] Explore an extension.

> A quotation can contain **emphasis** without changing the source format.

This sentence includes a footnote.[^note] It also links to the
[notation](#sec-1) and to the [source repository](https://github.com/nashspence/press).

<a id="sec-3"></a>
## § 3 — Tables, code, and images

| Symbol | Meaning | Example count |
| :--- | :--- | ---: |
| $S$ | Underlying set | 1 |
| $R$ | Relation on the set | 2 |

A fenced code block preserves literal code and indentation:

```python
def identity(value):
    return value
```

The following is a reference-style image with a stable, content-addressed HTTPS URL:

![Press asset preview: white text on a blue rectangle][sample-figure]

[sample-figure]: https://assets.press.nash.spence.0819870.xyz/sha256/035f32814cb199ba20d1c14a8e422facfa46b1166622396a8bb237d1712364bd.svg

[^note]: Footnote content can use *emphasis* and link back to [theorem](#theorem-1).
