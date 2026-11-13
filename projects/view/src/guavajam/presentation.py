"""Semantic HTML presentation of source headings, contents, overflow and code."""
from __future__ import annotations
from html.parser import HTMLParser
import re
from guavajam_core.asset_policy import AssetError
from guavajam_core.source_ast import walk,plain_text


ANCHOR = re.compile(r'<a id="([a-z][a-z0-9]*(?:-[a-z0-9]+)*)"></a>\Z')

FORMAL_LABEL = re.compile(
    r"^(Definition|Theorem|Proposition|Lemma|Corollary|Proof|Axiom|Remark|Example)"
    r"(?:\s+[0-9]+(?:\.[0-9]+)*)?(?:\s+—\s+.+)?$"
)

FORMAL_KINDS = frozenset({
    "definition", "theorem", "proposition", "lemma", "corollary",
    "proof", "axiom", "remark", "example",
})





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


def _contents_blocks(blocks):
    sections=[];used={n['c'][1][0] for n in blocks if n.get('t')=='Header'}
    for block in blocks:
        if block.get('t')=='Header' and block['c'][0]==2:
            inlines=block['c'][2]
            if len(inlines)>=2 and inlines[-1].get('t')=='Link' and 'press-permalink' in inlines[-1]['c'][0][1]:inlines=inlines[:-2]
            sections.append((block['c'][1][0],plain_text(inlines)))
    if len(sections)<3:return []
    def fresh(base):
        name=base;number=1
        while name in used:number+=1;name=base+'-'+str(number)
        used.add(name);return name
    contents,label=fresh('guavajam-contents'),fresh('guavajam-contents-label')
    items=[[{'t':'Plain','c':[{'t':'Link','c':[['',[],[]],[{'t':'Str','c':title}],['#'+anchor,'']]}]}] for anchor,title in sections]
    return [{'t':'RawBlock','c':['html','<nav id="'+contents+'" class="press-contents" aria-labelledby="'+label+'"><p id="'+label+'" class="press-contents-label" lang="en">Contents</p>']},{'t':'BulletList','c':items},{'t':'RawBlock','c':['html','</nav>']}]


def prepare_presentation_ast(ast: dict) -> dict:
    """Render explicit source anchors, formal headings and native contents navigation."""
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
                    attr[1].append("press-formal-heading")
                    attr[1].append("press-" + kind)
                    attr[2].append(["data-press-kind", kind])
                if anchored:
                    # Author-written #fragment links stay local in downloaded copies.
                    # Keep the permalink separate from any links in the heading text.
                    link_attr = ["", ["press-permalink"], [
                        ["lang", "en"], ["aria-label", "Permalink to section " + anchored],
                    ]]
                    title.extend([{"t": "Space"}, {"t": "Link", "c": [
                        link_attr, [{"t": "Str", "c": "¶"}], ["#" + anchored, ""],
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
            raise AssetError("reader identity and contents require a manuscript title")
        new[title_index + 1:title_index + 1] = contents
    if contents:
        ast.setdefault("meta", {})["press_has_contents"] = {"t": "MetaBool", "c": True}
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
            cls = "press-table-scroll" if kind == "Table" else "press-math-scroll"
            return {"t": "Div", "c": [
                ["", ["press-scroll", cls], [["role", "group"], ["aria-label", label], ["tabindex", "0"]]], [block],
            ]}
        return block
    ast["blocks"] = [wrap(block) for block in ast["blocks"]]
    return ast

