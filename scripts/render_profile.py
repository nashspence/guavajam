"""Versioned, deterministic HTML presentation profile for published editions.

Plain Markdown remains the source syntax. This module maps explicit source
anchors to heading IDs and adds publisher-owned semantics to formal headings.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from asset_policy import AssetError
from validate_sources import plain_text

PROFILE = "press.html.v1"
TEMPLATE_PATH = "templates/edition-v1.html"
STYLESHEET_PATH = "styles/edition-v1.css"
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


def prepare_presentation_ast(ast: dict) -> dict:
    """Move each explicit author-provided anchor onto its section heading.

    This gives published editions permanent anchor IDs, independently of
    Pandoc's automatic header slug algorithm. Bare Markdown previews retain
    the original explicit anchor markup.
    """
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
        new.append(block)
        i += 1
    ast["blocks"] = new
    return ast
