"""Narrow source directives normalized into Pandoc's native Table Caption.

Pandoc remains the only Markdown parser. Temporary paragraph markers establish
source correspondence; a second marker at each table boundary proves its span.
Both parses must round-trip the complete document AST before anything is moved.
"""
from __future__ import annotations

import copy
import hashlib
import re

from render_profile import walk

CANDIDATE = re.compile(r"^[ \t]*\\*:")
TABLE_LIKE = re.compile(r"^[ \t]*\\*:+[ \t*_`\\]*table\b", re.I)
MARKUP = re.compile(r"[*_~\[\]`$<>\\\t]")


def _contains(value, token):
    if isinstance(value, str):
        return token in value
    if isinstance(value, dict):
        return any(_contains(v, token) for v in value.values())
    if isinstance(value, list):
        return any(_contains(v, token) for v in value)
    return False


def _paragraph(token):
    return {"t": "Para", "c": [{"t": "Str", "c": token}]}


def _index(ast, token):
    matches = [i for i, block in enumerate(ast["blocks"]) if block == _paragraph(token)]
    return matches[0] if len(matches) == 1 else None


def _row_count(table):
    """The exact GFM Table shape emitted by pinned Pandoc 3.12.1."""
    try:
        _, caption, _, head, bodies, foot = table["c"]
        body, = bodies
        if (table["t"] != "Table" or caption != [None, []] or len(head[1]) != 1
                or len(body) != 4 or body[2] or foot[1]):
            return None
        return len(body[3])
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def normalize_table_captions(ast, source, relative, parse):
    """Return (AST, errors); never partially normalize an uncertain document.

    `parse` uses the same pinned reader as the original parse_document call.
    Marker parses are transient evidence, never author markup or emitted HTML.
    """
    lines = source.splitlines(keepends=True)
    seed = "PRESSCAPTION" + hashlib.sha256(source.encode()).hexdigest()
    candidates = {i: seed + "C" + str(i) for i, line in enumerate(lines) if CANDIDATE.match(line)}
    if not candidates:
        return ast, []
    assert seed not in source

    def error(i, message):
        return f"{relative}:{i + 1}: table caption: {message}"

    def tagged(selected, boundaries=None):
        out = []
        for i, line in enumerate(lines):
            if boundaries and i in boundaries:
                out.append(boundaries[i] + "\n\n")
            if i in selected:
                indent = re.match(r"^[ \t]*", line)[0]
                out.append(indent + selected[i] + ("\n" if line.endswith("\n") else ""))
            else:
                out.append(line)
        return "".join(out)

    def one_blank(i):
        return i >= 2 and lines[i - 1].rstrip("\n") == "" and bool(lines[i - 2].strip())

    first = min(candidates)
    try:
        marked = parse(tagged(candidates))
        selected = {}
        for i, token in candidates.items():
            if _contains(marked.get("meta", {}), token):
                continue
            literal = [node for node in walk(marked["blocks"])
                       if node["t"] in {"CodeBlock", "BlockQuote", "Math"} and _contains(node, token)]
            if literal:
                # Four-space code is literal, but an apparent indented caption
                # immediately after a top-level table must not silently publish.
                if TABLE_LIKE.match(lines[i]) and one_blank(i):
                    for j, block in enumerate(marked["blocks"]):
                        if (j and block.get("t") == "CodeBlock" and block["c"][1].splitlines()[:1] == [token]
                                and marked["blocks"][j - 1].get("t") == "Table"):
                            return None, [error(i, "remove indentation; use an unindented ': Table …' line")]
                continue
            j = _index(marked, token)
            after_table = j is not None and j > 0 and marked["blocks"][j - 1].get("t") == "Table"
            if TABLE_LIKE.match(lines[i]) or after_table:
                selected[i] = token
        if not selected:
            return ast, []
        if selected != candidates:
            marked = parse(tagged(selected))
        first = min(selected)
        restored = copy.deepcopy(marked)
        pairs, boundaries, errors = [], {}, []
        for i, token in selected.items():
            line = lines[i].removesuffix("\n")
            if not line.startswith(": Table ") or not line[len(": Table "):].strip():
                errors.append(error(i, "use exactly ': Table ' followed by nonempty plain text, without indentation or escaping"))
                continue
            if MARKUP.search(line):
                errors.append(error(i, "plain text only; remove Markdown, math, HTML, backticks and backslash escapes"))
                continue
            j = _index(marked, token)
            if not one_blank(i) or j is None or j == 0 or marked["blocks"][j - 1].get("t") != "Table":
                errors.append(error(i, "use a standalone line immediately after a top-level GFM pipe table, with exactly one blank line between"))
                continue
            if len(ast["blocks"]) != len(marked["blocks"]):
                errors.append(error(i, "cannot prove source/AST correspondence; separate the table and caption from other blocks"))
                continue
            original = ast["blocks"][j]
            inlines = original.get("c", [])
            if (original.get("t") != "Para" or any(n.get("t") not in {"Str", "Space"} for n in inlines)
                    or "".join(n.get("c", " ") for n in inlines) != " ".join(line.split())
                    or inlines[:2] != [{"t": "Str", "c": ":"}, {"t": "Space"}]):
                errors.append(error(i, "caption must parse as exactly one plain-text paragraph; remove formatting, entities or additional lines"))
                continue
            table = marked["blocks"][j - 1]
            rows = _row_count(table)
            start = i - 1 - (rows + 2) if rows is not None else -1
            if start < 0 or start in boundaries or any(not s.strip() for s in lines[start:i - 1]):
                errors.append(error(i, "cannot prove the GFM table boundary; remove intervening or ambiguous source material"))
                continue
            boundaries[start] = token + "BOUNDARY"
            restored["blocks"][j] = original
            pairs.append((i, j, start, inlines[2:]))
        if errors:
            return None, errors
        if restored != ast:
            return None, [error(first, "source/AST correspondence changed outside the caption; simplify ambiguous surrounding material")]
        # Inserting a paragraph at each computed table start must add exactly
        # that block before the same Table, leaving every other AST byte intact.
        expected = copy.deepcopy(marked)
        for _, j, start, _ in reversed(pairs):
            expected["blocks"].insert(j - 1, _paragraph(boundaries[start]))
        if parse(tagged(selected, boundaries)) != expected:
            return None, [error(first, "cannot prove the exact table span; remove intervening definitions, prose or ambiguous blocks")]
        result = copy.deepcopy(ast)
        for _, j, _, caption in reversed(pairs):
            result["blocks"][j - 1]["c"][1] = [None, [{"t": "Plain", "c": caption}]]
            del result["blocks"][j]
        return result, []
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        return None, [error(first, "cannot prove source/AST correspondence; simplify the table and caption (" + str(exc) + ")")]
