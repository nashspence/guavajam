"""Semantic HTML presentation of source headings, contents, overflow and code."""
from __future__ import annotations
from html import escape
from html.parser import HTMLParser
import re
from guavajam_core.asset_policy import AssetError
from guavajam_core.source_ast import walk,plain_text
from .navigation import manuscript_anchor as _anchor_from_block, contents_blocks, extract

FORMAL_LABEL = re.compile(
    r"^(Definition|Theorem|Proposition|Lemma|Corollary|Proof|Axiom|Remark|Example)"
    r"(?:\s+[0-9]+(?:\.[0-9]+)*)?(?:\s+—\s+.+)?$"
)

FORMAL_KINDS = frozenset({
    "definition", "theorem", "proposition", "lemma", "corollary",
    "proof", "axiom", "remark", "example",
})





def abstract_html(text):
    """One literal metadata abstract for work, signed reader and draft views."""
    if not text:
        return ''
    return ('<div class="guavajam-abstract" role="group" aria-label="Abstract">'
            '<p><strong>Abstract</strong></p><p>' + escape(text) + '</p></div>')


def prepare_presentation_ast(ast: dict, navigation=None) -> dict:
    """Render explicit source anchors, formal headings and native contents navigation."""
    navigation = navigation if navigation is not None else extract(ast)
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
            if level==1:attr[0]=""
            if attr[0] and attr[0] in ids:
                raise AssetError("duplicate published heading IDs")
            ids.add(attr[0])
            if level > 1:
                label = FORMAL_LABEL.fullmatch(plain_text(title))
                if label:
                    kind = label.group(1).lower()
                    if kind not in FORMAL_KINDS:
                        raise AssetError("unsupported formal block")
                    attr[1].append("guavajam-formal-heading")
                    attr[1].append("guavajam-" + kind)
                    attr[2].append(["data-guavajam-kind", kind])
                if anchored:
                    # Author-written #fragment links stay local in downloaded copies.
                    # Keep the permalink separate from any links in the heading text.
                    link_attr = ["", ["guavajam-permalink"], [
                        ["lang", "en"], ["aria-label", "Permalink to section " + anchored],
                    ]]
                    title.extend([{"t": "Space"}, {"t": "Link", "c": [
                        link_attr, [{"t": "Str", "c": "¶"}], ["#" + anchored, ""],
                    ]}])
        new.append(block)
        i += 1

    contents = contents_blocks(navigation['anchors'], ids)
    if contents:
        # Supporting documents have source titles; manuscripts use metadata citations.
        title_index = next((j for j, block in enumerate(new) if block.get("t") == "Header"
                            and block["c"][0] == 1), None)
        contents_index = 0 if title_index is None else title_index + 1
        new[contents_index:contents_index] = contents
    if contents:
        ast.setdefault("meta", {})["guavajam_has_contents"] = {"t": "MetaBool", "c": True}
    ast["blocks"]=new
    return wrap_reading_blocks(ast)


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


def wrap_reading_blocks(ast):
    # Give wide tables and display mathematics an actual scroll container.
    # Pandoc Div blocks preserve table semantics, and the explicit tab stop lets
    # keyboard users pan overflow without relying on browser-specific defaults.
    def wrap(block):
        kind = block.get("t")
        if kind == 'Div':
            block['c'][1] = [wrap(child) for child in block['c'][1]]
        elif kind == 'BlockQuote':
            block['c'] = [wrap(child) for child in block['c']]
        elif kind == 'BulletList':
            block['c'] = [[wrap(child) for child in item] for item in block['c']]
        elif kind == 'OrderedList':
            block['c'][1] = [[wrap(child) for child in item] for item in block['c'][1]]
        display_math = (kind in {"Para", "Plain"} and any(
            node.get("t") == "Math" and isinstance(node.get("c"), list)
            and node["c"][:1] == [{"t": "DisplayMath"}]
            for node in walk(block)
        ))
        if kind == "Table" or display_math:
            label = "Table" if kind == "Table" else "Displayed mathematics"
            cls = "guavajam-table-scroll" if kind == "Table" else "guavajam-math-scroll"
            return {"t": "Div", "c": [
                ["", ["guavajam-scroll", cls], [["role", "group"], ["aria-label", label], ["tabindex", "0"]]], [block],
            ]}
        return block
    ast["blocks"] = [wrap(block) for block in ast["blocks"]]
    return ast

