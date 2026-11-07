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

    def test_footnote_links_decorate_the_raised_numeral(self):
        css = (ROOT / "styles/edition-v1.css").read_text()
        self.assertIn(".footnote-ref { text-decoration: none; white-space: nowrap; }", css)
        self.assertIn(".footnote-ref > sup {", css)
        self.assertIn("text-decoration-line: underline;", css)
        self.assertIn(".footnote-back { margin-left: .3em; text-decoration: none; }", css)

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

    def test_supporting_preview_uses_exact_css_and_literal_passages(self):
        css = (ROOT / "styles/edition-v1.css").read_text()
        preview = (ROOT / "tests/fixtures/presentation/supporting-record-preview.html").read_text()
        embedded = preview.split('<style id="press-edition-css">\n', 1)[1].split("</style>", 1)[0]
        self.assertEqual(css, embedded)
        self.assertIn('class="press-record-provenance"', preview)
        self.assertIn('class="press-passage-heading"', preview)
        self.assertIn('datetime="2026-11-05T14:37:09.125Z"', preview)
        self.assertIn('datetime="2026-11-04"', preview)
        self.assertNotIn('datetime=""', preview)
        self.assertIn('**For all x in [0,1]', preview)
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
        self.assertIn('class="press-supporting-passage"',
                      (ROOT / "scripts/supporting_records.py").read_text())
        stylesheet = (ROOT / "styles/edition-v1.css").read_text()
        self.assertIn(".press-supporting-passage {", stylesheet)
        self.assertIn("white-space: pre-wrap;", stylesheet)
        self.assertIn(".press-record-provenance {", stylesheet)
        self.assertIn(".press-passage-heading {", stylesheet)
        self.assertIn(".press-passage-datetime {", stylesheet)


if __name__ == "__main__":
    unittest.main()
