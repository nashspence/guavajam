"""Offline presentation checks that do not depend on a browser or Pandoc version."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from asset_policy import AssetError
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

    def test_responder_figure_uses_caption_outside_quote_not_artificial_headings(self):
        from supporting_gfm import parse_gfm_record
        source = ('<a name="document"></a>\n\n# Evidence\n\n'
                  '<a name="author"></a>\n\nAuthorial prose.\n\n'
                  '<a name="response" data-press-speaker="responder"></a>\n\n'
                  '> <a name="body"></a>\n>\n> Quoted response.\n\n— Correspondent\n')
        ast, anchors = parse_gfm_record(source)
        self.assertEqual('Para', ast['blocks'][anchors['author']]['t'])
        start = ast['blocks'][anchors['response']]
        self.assertEqual('Div', start['t'])
        self.assertEqual('responder', dict(start['c'][0][2])['data-press-speaker'])
        self.assertEqual('Correspondent', dict(start['c'][0][2])['data-press-attribution'])
        self.assertEqual('Para', ast['blocks'][anchors['body']]['t'])
        self.assertEqual(1, len([b for b in ast['blocks'] if b['t']=='Header']))

    def test_end_attribution_preserves_source_model_and_effort_without_badges(self):
        from supporting_gfm import append_gfm
        from tempfile import TemporaryDirectory
        import json
        import subprocess
        with TemporaryDirectory() as directory:
            root=Path(directory)
            relative=Path('works/demo/supporting/quote.md')
            path=root/relative
            path.parent.mkdir(parents=True)
            path.write_text('<a name="document"></a>\n\n# Evidence\n\n'
                            '<a name="response" data-press-speaker="assistant"></a>\n\n'
                            '> <a name="quoted"></a>\n>\n> Exact quote.\n\n'
                            '— gpt-6-thinking (max effort)\n')
            blocks, anchors, _, _ = append_gfm(root, relative, 'quote')
            opening=next(b for b in blocks if b['t']=='RawBlock' and '<figcaption' in b['c'][1])
            caption=blocks[blocks.index(opening)+1]
        self.assertEqual('Para',caption['t'])
        spans=[node for node in caption['c'] if node['t']=='Span']
        self.assertEqual([['press-turn-credit']],
                         [node['c'][0][1] for node in spans])
        self.assertEqual('— gpt-6-thinking (max effort)',
                         ''.join(x['c'] if x['t']=='Str' else ' '
                                 for x in spans[0]['c'][1]))
        css=(ROOT/'styles/edition-v1.css').read_text()
        self.assertIn('press-turn-credit { font-weight: 500; overflow-wrap: anywhere;', css)
        self.assertNotIn('press-turn-details', css)

    def test_quoted_responder_uses_the_native_blockquote_rail(self):
        css=(ROOT/'styles/edition-v1.css').read_text()
        for token in ('figure.press-turn > blockquote.press-turn-quotation {','margin: 0 0 0 -.35rem;',
                      '--press-turn-text-inset: .65rem;',
                      'border-inline-start: var(--press-turn-rail-size) solid var(--press-border);',
                      'inset-inline-start: calc(-1 * (var(--press-turn-text-inset) + var(--press-turn-rail-size)));',
                      'inline-size: calc(var(--press-turn-text-inset) + var(--press-turn-rail-size));',
                      'figure.press-turn, figure.press-turn > blockquote.press-turn-quotation' ):
            self.assertIn(token,css)
        self.assertNotIn('blockquote.press-turn::before',css)
        self.assertNotIn('box-shadow: inset 0 .11rem 0 var(--press-link);',css)
        self.assertNotIn('speaker',css.split('/* Supporting evidence:')[0])
        forced=css.split('@media (forced-colors: active)')[1].split('@media print')[0]
        self.assertIn('border-inline-start-color: CanvasText;',forced)

    def test_figure_quote_and_credit_are_bound_by_element_fingerprints(self):
        from element_citations import element_fingerprints
        raw = (b'<figure class="press-turn" id="reply" data-press-speaker="responder">'
               b'<blockquote class="press-turn-quotation"><div id="block">Quoted text</div>'
               b'</blockquote><figcaption>-- Correspondent</figcaption></figure>')
        baseline = element_fingerprints(raw, {"reply", "block"})
        changed_role = raw.replace(b'data-press-speaker="responder"', b'data-press-speaker="assistant"')
        self.assertNotEqual(baseline, element_fingerprints(changed_role, {"reply", "block"}))
        changed_credit = raw.replace(b'-- Correspondent', b'-- Unknown')
        self.assertNotEqual(baseline['reply'], element_fingerprints(changed_credit, {"reply", "block"})['reply'])
        changed_quote = raw.replace(b'class="press-turn-quotation"', b'class="ordinary"')
        self.assertNotEqual(baseline['reply'], element_fingerprints(changed_quote, {"reply", "block"})['reply'])
        shifted = raw.replace(b'</blockquote><figcaption>', b'<figcaption>')
        with self.assertRaises(AssetError):
            element_fingerprints(shifted, {"reply", "block"})

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

    def test_preview_identity_is_visible_with_the_title_and_uses_readable_type(self):
        css = (ROOT / 'styles/edition-v1.css').read_text()
        style = css.split('.press-edition-identity {', 1)[1].split('}', 1)[0]
        self.assertIn('font: .94rem/1.55 system-ui, sans-serif;', style)
        self.assertIn('overflow-wrap: anywhere;', style)
        self.assertIn('break-after: avoid;', style)
        for path in (ROOT / 'tests/fixtures/presentation').glob('*preview.html'):
            with self.subTest(preview=path.name):
                page = path.read_text()
                self.assertRegex(page, r'</h1>\s*<div class="press-edition-identity"')
                self.assertIn('data-press-field="byline">Nash Spence</span>', page)
                self.assertIn('data-press-field="edition-version" lang="en">Edition v1.0.0</span>', page)

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
