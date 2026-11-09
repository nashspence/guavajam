"""Offline presentation checks that do not depend on a browser or Pandoc version."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from public_record import PAGE_STYLE
from render_profile import canonical_template, prepare_code_focus, prepare_presentation_ast


COLORS = ("paper", "ink", "muted", "link", "visited", "border", "tint", "focus")


def tokens(source, section):
    if section == "light":
        content = source.split("@media", 1)[0]
    elif section == "dark":
        content = source.split("@media (prefers-color-scheme: dark)", 1)[1].split("@media print", 1)[0]
    else:
        content = source.split("@media print", 1)[1]
    return dict(re.findall(r"--press-([a-z]+):\s*(#[0-9a-f]+);", content))


def contrast_ratio(foreground, background):
    """WCAG relative luminance for the publisher's exact hex color tokens."""
    def luminance(color):
        assert re.fullmatch(r"#[0-9a-f]{3}(?:[0-9a-f]{3})?", color)
        if len(color) == 4:
            color = "#" + "".join(ch * 2 for ch in color[1:])
        channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4
                  for value in channels]
        return sum(weight * value for weight, value in zip((.2126, .7152, .0722), linear))
    lo, hi = sorted((luminance(foreground), luminance(background)))
    return (hi + .05) / (lo + .05)


class PresentationStyleTests(unittest.TestCase):
    def test_both_document_families_share_the_palette(self):
        edition = (ROOT / "styles/edition-v1.css").read_text()
        for theme in ("light", "dark", "print"):
            with self.subTest(theme=theme):
                reader = tokens(edition, theme)
                records = tokens(PAGE_STYLE, theme)
                for name in COLORS if theme != "print" else ("paper", "ink", "muted", "link", "visited"):
                    self.assertEqual(reader[name], records[name])
        # Printing with a dark OS preference must still use a dark link on paper.
        self.assertEqual(tokens(edition, "print")["link"], "#164a7c")
        self.assertEqual(tokens(edition, "print")["visited"], "#164a7c")

    def test_reading_palette_has_text_and_boundary_contrast(self):
        edition = (ROOT / "styles/edition-v1.css").read_text()
        for theme in ("light", "dark"):
            palette = tokens(edition, theme)
            for surface in ("paper", "tint"):
                for role in ("ink", "muted", "link", "visited"):
                    with self.subTest(theme=theme, surface=surface, role=role):
                        self.assertGreaterEqual(contrast_ratio(palette[role], palette[surface]), 4.5)
                for role in ("border", "focus"):
                    with self.subTest(theme=theme, surface=surface, role=role):
                        self.assertGreaterEqual(contrast_ratio(palette[role], palette[surface]), 3)

    def test_fragment_target_forced_colors_and_print_pagination_have_fallbacks(self):
        css = (ROOT / "styles/edition-v1.css").read_text()
        self.assertIn("[id]:target {", css)
        target_rule = ".press-edition :is(h2, h3, h4, h5, h6, div, li)[id]:target"
        self.assertIn(target_rule + "::before {", css)
        decoration = css.split(target_rule + "::before {", 1)[1].split("}", 1)[0]
        for rule in ('content: ""', "position: absolute", "inset-inline-start: -.6rem",
                     "inline-size: .6rem", "border-inline-start: .2rem solid var(--press-link)",
                     "background: var(--press-tint)", "pointer-events: none"):
            self.assertIn(rule, decoration)
        self.assertNotIn("box-shadow", css.split(target_rule + " {", 1)[1].split("}", 1)[0])
        forced = css.split("@media (forced-colors: active)", 1)[1].split("@media print", 1)[0]
        self.assertIn("outline: 2px solid Highlight", forced)
        self.assertIn(".press-permalink:focus-visible { outline: 3px solid Highlight; }", forced)
        self.assertIn(target_rule + "::before { content: none; }", forced)
        self.assertNotIn("forced-color-adjust: none", forced)
        printed = css.split("@media print", 1)[1]
        for rule in ("orphans: 3", "widows: 3", "break-after: avoid-page", "tr { break-inside: avoid; }"):
            self.assertIn(rule, printed)
        self.assertIn(target_rule + "::before { content: none; }", printed)
        self.assertIn("background: transparent", printed)

    def test_supporting_document_field_readability_does_not_change_provenance_typography(self):
        css = (ROOT / 'styles/edition-v1.css').read_text()
        # The original, separately verified GFM/chat provenance treatment is
        # unchanged. Only document-owned labels and values become larger.
        source = css.split('.press-record-provenance {', 1)[1].split('}', 1)[0]
        self.assertIn('font: .76rem/1.5 system-ui, sans-serif;', source)
        metadata = css.split('.press-document-metadata {', 1)[1].split('}', 1)[0]
        self.assertIn('font: .76rem/1.5 system-ui, sans-serif;', metadata)
        self.assertIn('font-size: 1.12em;', css)
        self.assertIn('.press-document-metadata dt:not(:first-child) + dd {', css)
        mobile = css.split('@media (max-width: 36rem)', 1)[1].split('@media (prefers-color-scheme: dark)', 1)[0]
        self.assertIn('border-block-start: 0;', mobile)
        self.assertNotIn('press-metadata-dense', css)
        printed = css.split('@media print', 1)[1]
        self.assertIn('.press-document-metadata dt, .press-document-metadata dd { break-inside: avoid; }', printed)

    def test_source_attributed_speaker_headings_are_subtle_and_inert(self):
        stylesheet = (ROOT / 'styles/edition-v1.css').read_text()
        selector = '.press-passage-heading:is([data-press-speaker="assistant"], [data-press-speaker="responder"])'
        self.assertIn(selector + '::after', stylesheet)
        self.assertIn('border-inline-start: .18rem solid var(--press-border);', stylesheet)
        self.assertIn('background: var(--press-tint);', stylesheet)
        self.assertIn('pointer-events: none;', stylesheet)
        self.assertNotIn('speaker', stylesheet.split('/* Supporting evidence:')[0])
        self.assertIn('background: Canvas;', stylesheet.split('@media (forced-colors: active)')[1])
        self.assertIn('background: transparent;', stylesheet.split('@media print')[1])

    def test_footnote_links_decorate_the_raised_numeral(self):
        css = (ROOT / "styles/edition-v1.css").read_text()
        self.assertIn(".footnote-ref { text-decoration: none; white-space: nowrap; }", css)
        self.assertIn(".footnote-ref > sup {", css)
        self.assertIn("text-decoration-line: underline;", css)
        self.assertIn(".footnote-back { margin-left: .3em; text-decoration: underline; }", css)

    def test_reader_preview_uses_exact_css_and_pandoc_note_structure(self):
        css = (ROOT / "styles/edition-v1.css").read_text()
        preview = (ROOT / "tests/fixtures/presentation/reader-preview.html").read_text()
        embedded = preview.split('<style id="press-edition-css">\n', 1)[1].split("</style>", 1)[0]
        self.assertEqual(css, embedded)
        self.assertIn('class="footnote-ref"', preview)
        self.assertIn('role="doc-noteref"><sup>1</sup></a>', preview)
        self.assertIn('class="footnote-back" role="doc-backlink"', preview)

    def test_navigation_preview_uses_exact_css_and_semantic_anchors(self):
        css = (ROOT / "styles/edition-v1.css").read_text()
        preview = (ROOT / "tests/fixtures/presentation/navigation-preview.html").read_text()
        embedded = preview.split('<style id="press-edition-css">\n', 1)[1].split('</style>', 1)[0]
        self.assertEqual(css, embedded)
        self.assertIn('<nav id="press-contents"', preview)
        self.assertIn('aria-labelledby="press-contents-label"', preview)
        self.assertIn('class="press-skip" href="#press-main"', preview)
        self.assertIn('class="press-contents-link" href="#press-contents"', preview)

    def test_supporting_preview_uses_exact_css_and_semantic_gfm_passages(self):
        css = (ROOT / "styles/edition-v1.css").read_text()
        preview = (ROOT / "tests/fixtures/presentation/supporting-record-preview.html").read_text()
        embedded = preview.split('<style id="press-edition-css">\n', 1)[1].split("</style>", 1)[0]
        self.assertEqual(css, embedded)
        self.assertIn('class="press-record-provenance"', preview)
        self.assertIn('class="press-passage-heading"', preview)
        self.assertIn('datetime="2026-11-05T14:37:09.125Z"', preview)
        self.assertIn('datetime="2026-11-04"', preview)
        self.assertNotIn('datetime=""', preview)
        self.assertIn('<strong>For all x in [0,1]', preview)
        self.assertIn('role="doc-', (ROOT / 'tests/fixtures/presentation/reader-preview.html').read_text())

    def test_mathematical_sections_and_tables_use_scholarly_rhythm(self):
        css = (ROOT / "styles/edition-v1.css").read_text()
        self.assertIn("h3.press-formal-heading { font-size: 1.08rem; }", css)
        self.assertIn(".press-formal-heading[data-press-kind='proof'] {", css)
        self.assertIn("margin-top: .9rem;", css)
        self.assertIn("border-top: 2px solid var(--press-border);", css)
        self.assertIn("border-bottom: 2px solid var(--press-border);", css)
        self.assertIn("thead th { border-bottom-width: 2px; }", css)
        header = re.search(r"(?m)^th \{([^}]+)\}", css)
        self.assertIsNotNone(header)
        self.assertNotIn("background", header.group(1))
        self.assertIn("<caption>Identity and fixed-point properties</caption>",
                      (ROOT / "tests/fixtures/presentation/reader-preview.html").read_text())

    def test_embedded_stylesheet_keeps_its_no_network_contract(self):
        template, template_sha, css_sha = canonical_template(ROOT)
        self.assertIn("<style id=\"press-edition-css\">", template)
        self.assertNotIn("@font-face", template)
        self.assertNotIn("@import", template)
        self.assertNotIn("url(", template)
        self.assertEqual(64, len(template_sha))
        self.assertEqual(64, len(css_sha))

    def test_scroll_containers_retain_tables_and_math_nodes(self):
        table = {"t": "Table", "c": ["example"]}
        equation = {"t": "Para", "c": [
            {"t": "Math", "c": [{"t": "DisplayMath"}, "x=y"]},
        ]}
        inline = {"t": "Para", "c": [
            {"t": "Math", "c": [{"t": "InlineMath"}, "z"]},
        ]}
        ast = {"blocks": [table, equation, inline]}
        prepare_presentation_ast(ast)
        self.assertEqual(3, len(ast["blocks"]))
        for wrapped, original, cls in zip(ast["blocks"][:2], (table, equation),
                                            ("press-table-scroll", "press-math-scroll")):
            with self.subTest(cls=cls):
                self.assertEqual("Div", wrapped["t"])
                attributes, children = wrapped["c"]
                self.assertEqual([original], children)
                self.assertEqual(["press-scroll", cls], attributes[1])
                self.assertEqual([["role", "group"], ["aria-label", "Table" if cls == "press-table-scroll" else "Displayed mathematics"], ["tabindex", "0"]], attributes[2])
        self.assertIs(inline, ast["blocks"][2])

    def test_citable_composites_retain_nested_math_and_table_keyboard_scrollers(self):
        table={'t':'Table','c':['example']}
        equation={'t':'Para','c':[{'t':'Math','c':[{'t':'DisplayMath'},'x=y']}]}
        evidence={'t':'Div','c':[['source-claim',['press-supporting-element'],[]],[table,
            {'t':'BlockQuote','c':[equation]}]]}
        ast={'blocks':[evidence]}
        prepare_presentation_ast(ast)
        self.assertEqual('source-claim',ast['blocks'][0]['c'][0][0])
        children=ast['blocks'][0]['c'][1]
        self.assertEqual(['press-scroll','press-table-scroll'],children[0]['c'][0][1])
        self.assertEqual(['press-scroll','press-math-scroll'],children[1]['c'][0]['c'][0][1])
        self.assertEqual([table],children[0]['c'][1])

    def test_native_code_focus_preserves_markup_and_escaped_source(self):
        from asset_policy import AssetError
        source = '<!-- literal <pre> -->\n<pre><code>&lt;pre&gt;literal</code></pre><div class="sourceCode"><pre class="python"><code>x</code></pre></div>'
        focused = prepare_code_focus(source)
        self.assertEqual(focused.replace(' tabindex="0" role="group" aria-label="Code block"', ''), source)
        self.assertEqual(focused.count(' tabindex="0"'), 2)
        unusual = '<pre class="tabindex=0" data-example="a > b"><code>literal</code></pre>'
        self.assertIn('class="tabindex=0" data-example="a > b" tabindex="0"', prepare_code_focus(unusual))
        self.assertIn('<div class="sourceCode"><pre class="python" tabindex="0" role="group" aria-label="Code block">', focused)
        with self.assertRaisesRegex(AssetError, "unexpected native pre focus"):
            prepare_code_focus('<pre tabindex="-1"><code>x</code></pre>')

    def test_supporting_passage_uses_versioned_styles(self):
        self.assertIn("'press-supporting-element'",
                      (ROOT / "scripts/supporting_gfm.py").read_text())
        stylesheet = (ROOT / "styles/edition-v1.css").read_text()
        self.assertIn(".press-supporting-element {", stylesheet)
        self.assertIn(".press-record-provenance {", stylesheet)
        self.assertIn(".press-passage-heading {", stylesheet)
        self.assertIn(".press-passage-datetime {", stylesheet)


if __name__ == "__main__":
    unittest.main()
