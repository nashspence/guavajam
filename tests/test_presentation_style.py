"""Offline presentation checks that do not depend on a browser or Pandoc version."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from public_record import PAGE_STYLE
from render_profile import canonical_template, prepare_presentation_ast


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
        for wrapped, original, cls, label in zip(ast["blocks"][:2], (table, equation),
                                                  ("press-table-scroll", "press-math-scroll"),
                                                  ("Scrollable table", "Scrollable equation")):
            with self.subTest(cls=cls):
                self.assertEqual("Div", wrapped["t"])
                attributes, children = wrapped["c"]
                self.assertEqual([original], children)
                self.assertEqual(["press-scroll", cls], attributes[1])
                self.assertEqual([["role", "region"], ["aria-label", label], ["tabindex", "0"]], attributes[2])
        self.assertIs(inline, ast["blocks"][2])

    def test_supporting_passage_uses_versioned_styles(self):
        self.assertIn('class="press-supporting-passage"',
                      (ROOT / "scripts/supporting_records.py").read_text())
        self.assertIn(".press-supporting-passage { white-space: pre-wrap; overflow-wrap: anywhere; }",
                      (ROOT / "styles/edition-v1.css").read_text())


if __name__ == "__main__":
    unittest.main()
