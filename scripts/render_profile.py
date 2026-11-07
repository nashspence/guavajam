"""Versioned, deterministic HTML presentation profile for published editions.

Plain Markdown remains the source syntax. This module maps explicit source
anchors to heading IDs and adds publisher-owned semantics to formal headings.
"""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import re
from pathlib import Path

from asset_policy import AssetError


PROFILE = "press.html.v1"
# Retain issued profiles here when the builder moves to a new default.
PROFILE_FILES = {
    "press.html.v1": ("templates/edition-v1.html", "styles/edition-v1.css"),
}


def profile_paths(profile: str) -> tuple[str, str]:
    if not isinstance(profile, str) or profile not in PROFILE_FILES:
        raise AssetError(f"unsupported presentation provenance profile: {profile!r}")
    return PROFILE_FILES[profile]


TEMPLATE_PATH, STYLESHEET_PATH = profile_paths(PROFILE)
CSS_TOKEN = "@@PRESS_EDITION_CSS@@"
ANCHOR = re.compile(r'<a id="([a-z][a-z0-9]*(?:-[a-z0-9]+)*)"></a>\Z')
FORMAL_LABEL = re.compile(
    r"^(Definition|Theorem|Proposition|Lemma|Corollary|Proof|Axiom|Remark|Example)"
    r"(?:\s+[0-9]+(?:\.[0-9]+)*)?(?:\s+—\s+.+)?$"
)
FORMAL_KINDS = frozenset({
    "definition", "theorem", "proposition", "lemma", "corollary",
    "proof", "axiom", "remark", "example",
})


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_template(builder: Path) -> tuple[str, str, str]:
    """Load versioned source files and embed the exact CSS bytes into template."""
    template_bytes = (builder / TEMPLATE_PATH).read_bytes()
    css_bytes = (builder / STYLESHEET_PATH).read_bytes()
    try:
        template = template_bytes.decode("utf-8", "strict")
        css = css_bytes.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise AssetError("presentation sources must be UTF-8") from exc
    if template.count(CSS_TOKEN) != 1 or not css or not css.endswith("\n"):
        raise AssetError("versioned HTML template or stylesheet violates profile contract")
    if ("</style" in css.lower() or re.search(r"@import|url\s*\(|expression\s*\(|\\", css, re.I)):
        raise AssetError("edition CSS must be static and self-contained")
    return template.replace(CSS_TOKEN, css), digest(template_bytes), digest(css_bytes)


def walk(value):
    if isinstance(value, dict):
        if isinstance(value.get("t"), str):
            yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def plain_text(value) -> str:
    bits: list[str] = []
    def visit(node):
        if isinstance(node, dict):
            token = node.get("t")
            if token in ("Str", "MetaString"):
                bits.append(node["c"])
            elif token in ("Space", "SoftBreak", "LineBreak"):
                bits.append(" ")
            elif token == "Code":
                bits.append(node["c"][1])
            elif token == "Math":
                bits.append(node["c"][1])
            else:
                visit(node.get("c", []))
        elif isinstance(node, list):
            for sub in node:
                visit(sub)
    visit(value)
    return "".join(bits).strip()


def _anchor_from_block(block: dict) -> str | None:
    """Pandoc parses a standalone `<a id=...></a>` as RawInline tokens in Para.

    Some Pandoc versions may instead use RawBlock. These are the only accepted
    source shapes; anything else is left for the strict source validator.
    """
    kind, contents = block.get("t"), block.get("c")
    if kind == "RawBlock" and isinstance(contents, list) and len(contents) == 2:
        if contents[0] == "html":
            match = ANCHOR.fullmatch(contents[1])
            return match.group(1) if match else None
    if kind != "Para" or not isinstance(contents, list) or len(contents) != 2:
        return None
    if contents[0].get("t") != "RawInline" or contents[1].get("t") != "RawInline":
        return None
    opening, closing = contents[0].get("c"), contents[1].get("c")
    if (not isinstance(opening, list) or not isinstance(closing, list) or
            opening[0] != "html" or closing != ["html", "</a>"]):
        return None
    match = re.fullmatch(r'<a id="([a-z][a-z0-9]*(?:-[a-z0-9]+)*)">', opening[1])
    return match.group(1) if match else None


# The outline is a reading aid, not an alternative authoring or citation scheme.
# Include only major manuscript sections. Supporting documents share one entry,
# regardless of their number of citable passages or internal headings.
CONTENTS_ID = "press-contents"
CONTENTS_LABEL_ID = "press-contents-label"
RESERVED_READER_IDS = frozenset({"press-main", CONTENTS_ID, CONTENTS_LABEL_ID})


def _contents_blocks(blocks: list[dict]) -> list[dict]:
    sections = []
    first_record = None
    record_count = 0
    for block in blocks:
        if block.get("t") != "Header" or block["c"][0] != 2:
            continue
        _level, attrs, inlines = block["c"]
        if "press-record-heading" in attrs[1]:
            record_count += 1
            if first_record is None:
                first_record = attrs[0]
        else:
            # The canonical permalink was appended to the heading earlier;
            # it must not become a stray paragraph mark in the outline.
            title_inlines = inlines[:-2] if (len(inlines) >= 2 and
                inlines[-1].get("t") == "Link" and
                "press-permalink" in inlines[-1]["c"][0][1]) else inlines
            sections.append((attrs[0], plain_text(title_inlines), False))

    # A two-section note needs no navigation. A manuscript with three major
    # sections, two plus evidence, or a large evidence appendix benefits.
    if len(sections) < 3 and not (len(sections) >= 2 and first_record) \
            and not (len(sections) >= 1 and record_count >= 3):
        return []
    if first_record:
        sections.append((first_record, "Supporting records", True))

    items = []
    for anchor, title, english in sections:
        link_attributes = ["", [], [["lang", "en"]] if english else []]
        link = {"t": "Link", "c": [link_attributes, [{"t": "Str", "c": title}], ["#" + anchor, ""]]}
        items.append([{"t": "Plain", "c": [link]}])
    return [
        {"t": "RawBlock", "c": ["html", '<nav id="press-contents" class="press-contents" aria-labelledby="press-contents-label">'
                                     '<p id="press-contents-label" class="press-contents-label" lang="en">Contents</p>']},
        {"t": "BulletList", "c": items},
        {"t": "RawBlock", "c": ["html", "</nav>"]},
    ]


def prepare_presentation_ast(ast: dict, record_url: str | None = None) -> dict:
    """Move each explicit author-provided anchor onto its section heading.

    This gives published editions permanent anchor IDs, independently of
    Pandoc's automatic header slug algorithm. Bare Markdown previews retain
    the original explicit anchor markup.
    """
    for node in walk(ast.get('blocks', [])):
        if node.get('t') == 'Link' and node['c'][2][0].startswith('press-passage:'):
            anchor = node['c'][2][0].removeprefix('press-passage:')
            node['c'][2][0] = (record_url + '/read' if record_url else '') + '#' + anchor
    old = ast.get("blocks", [])
    new: list[dict] = []
    ids: set[str] = set()
    i = 0
    while i < len(old):
        block = old[i]
        anchored = _anchor_from_block(block)
        if anchored is not None:
            if i + 1 >= len(old) or old[i + 1].get("t") != "Header":
                raise AssetError("stable anchor is not attached to a heading")
            heading = old[i + 1]
            if heading["c"][0] == 1:
                raise AssetError("H1 must not have a section anchor")
            if anchored in ids:
                raise AssetError("duplicate stable section IDs")
            heading["c"][1][0] = anchored
            i += 1
            block = heading
        if block.get("t") == "Header":
            level, attr, title = block["c"]
            if level > 1 and not anchored:
                raise AssetError("published section requires an explicit stable anchor")
            if attr[0] in ids:
                raise AssetError("duplicate published heading IDs")
            if attr[0] in RESERVED_READER_IDS:
                raise AssetError("heading ID is reserved for reader navigation: " + attr[0])
            ids.add(attr[0])
            if level > 1:
                label = FORMAL_LABEL.fullmatch(plain_text(title))
                if label:
                    kind = label.group(1).lower()
                    if kind not in FORMAL_KINDS:
                        raise AssetError("unsupported formal block")
                    attr[1].append("press-formal-heading")
                    attr[1].append("press-" + kind)
                    attr[2].append(["data-press-kind", kind])
                if record_url:
                    # Author-written #fragment links stay local in downloaded copies.
                    # Keep the permalink separate from any links in the heading text.
                    link_attr = ["", ["press-permalink"], [
                        ["lang", "en"], ["aria-label", "Permalink to section " + anchored],
                    ]]
                    title.extend([{"t": "Space"}, {"t": "Link", "c": [
                        link_attr, [{"t": "Str", "c": "¶"}], [record_url + "/read#" + anchored, ""],
                    ]}])
        new.append(block)
        i += 1

    contents = _contents_blocks(new)
    if contents:
        # Keep the title first. H2-only navigation never duplicates theorem,
        # footnote, MathML or supporting-passage output.
        title_index = next((j for j, block in enumerate(new) if block.get("t") == "Header"
                            and block["c"][0] == 1), None)
        if title_index is None:
            raise AssetError("reader contents require a manuscript title")
        new[title_index + 1:title_index + 1] = contents
        ast.setdefault("meta", {})["press_has_contents"] = {"t": "MetaBool", "c": True}
    # Give wide tables and display mathematics an actual scroll container.
    # Pandoc Div blocks preserve table semantics, and the explicit tab stop lets
    # keyboard users pan overflow without relying on browser-specific defaults.
    wrapped = []
    for block in new:
        kind = block.get("t")
        display_math = (kind in {"Para", "Plain"} and any(
            node.get("t") == "Math" and isinstance(node.get("c"), list)
            and node["c"][:1] == [{"t": "DisplayMath"}]
            for node in walk(block)
        ))
        if kind == "Table" or display_math:
            label = "Table" if kind == "Table" else "Displayed mathematics"
            cls = "press-table-scroll" if kind == "Table" else "press-math-scroll"
            wrapped.append({"t": "Div", "c": [
                ["", ["press-scroll", cls], [["role", "group"], ["aria-label", label], ["tabindex", "0"]]], [block],
            ]})
        else:
            wrapped.append(block)
    ast["blocks"] = wrapped
    return ast


def prepare_code_focus(rendered: str) -> str:
    """Attach focus to native pre tags after pinned Pandoc syntax highlighting.

    Pandoc puts CodeBlock attributes on a surrounding div when highlighting,
    so an AST tabindex would focus the wrong box. Source HTML remains forbidden.
    """
    offsets = [0] + [match.end() for match in re.finditer("\n", rendered)]
    edits = []
    class CodeFocus(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag != "pre":
                return
            if any(key in {"tabindex", "role", "aria-label"} for key, _ in attrs):
                raise AssetError("unexpected native pre focus attribute from the pinned writer")
            raw = self.get_starttag_text()
            line, column = self.getpos()
            edits.append((offsets[line - 1] + column, len(raw), raw[:-1] +
                          ' tabindex="0" role="group" aria-label="Code block">'))
    parser = CodeFocus()
    parser.feed(rendered)
    parser.close()
    for start, size, replacement in reversed(edits):
        rendered = rendered[:start] + replacement + rendered[start + size:]
    return rendered
