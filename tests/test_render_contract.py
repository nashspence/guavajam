"""Executable, offline assertions for the versioned press.html.v1 output contract."""
from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

from asset_policy import AssetError
from build_edition import build_edition
from render_profile import PROFILE, STYLESHEET_PATH, TEMPLATE_PATH
from test_assets import EditionTests, URL, SVG
from verify_edition import EditionHTML, verify_edition


class RenderContractTests(EditionTests):
    """The reviewed fixture is a real Git commit; no release or network access."""

    def sample_with_structures(self):
        source = self.repo / "works/sample/manuscript.md"
        original = source.read_text(encoding="utf-8")
        # Preserve exact YAML front matter but exercise every selected GFM node.
        front, _ = original.split("---\n\n", 1)
        body = (
            "# On Structure and Identity\n\n"
            "<a id=\"sec-1\"></a>\n## § 1 — Foundations\n\n"
            "Text with **strong**, *emphasis*, and `inline code`.\n\n"
            "<a id=\"def-1\"></a>\n### Definition 1.1 — Identity\n\n"
            "Let $x \\in S$.\n\n"
            "$$\n\\forall x \\in S,\\quad x=x\n$$\n\n"
            "See [Definition 1.1](#def-1) and a note[^note].\n\n"
            "<a id=\"proof-1\"></a>\n### Proof\n\n"
            "- Item one\n- Item two\n\n"
            "> A block quotation.\n\n"
            "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
            "```text\nexample code\n```\n\n"
            f"![Structural diagram]({URL})\n\n"
            "[^note]: A sample footnote.\n"
        )
        source.write_text(front + "---\n\n" + body, encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "Exercise rendering semantics")
        self.commit = self.git("rev-parse", "HEAD")

    def _repack(self, html=None, manifest=None):
        page = self.output / "sample-v1.0.0.html"
        if html is not None:
            page.write_bytes(html)
        p = self.output / "provenance.json"
        current = json.loads(p.read_text(encoding="utf-8"))
        if manifest is not None:
            current = manifest
        page_bytes = page.read_bytes()
        current["html_sha256"] = hashlib.sha256(page_bytes).hexdigest()
        current["html_byte_count"] = len(page_bytes)
        p.write_text(json.dumps(current, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        sums = "".join(hashlib.sha256((self.output / name).read_bytes()).hexdigest()
                       + "  " + name + "\n" for name in [page.name, p.name])
        (self.output / "SHA256SUMS").write_text(sums, encoding="ascii")

    def test_stable_heading_ids_and_formal_semantics(self):
        self.sample_with_structures()
        manifest = self.build()
        html = (self.output / "sample-v1.0.0.html").read_text(encoding="utf-8")
        parser = EditionHTML()
        parser.feed(html)
        parser.close()
        self.assertIn('id="sec-1"', html)
        self.assertIn('id="def-1"', html)
        self.assertIn('id="proof-1"', html)
        self.assertEqual(["definition", "proof"], parser.formal_kinds)
        self.assertTrue({'sec-1', 'def-1', 'proof-1'} <= parser.ids)
        self.assertNotIn('<a id="def-1"></a>', html)
        self.assertIn('data-press-kind="definition"', html)
        self.assertIn('data-press-kind="proof"', html)
        self.assertIn('<blockquote>', html)
        self.assertIn('<table', html)
        self.assertIn('<code', html)
        self.assertIn('<math', html)
        self.assertIn('id="press-edition-css"', html)
        self.assertEqual(PROFILE, manifest['presentation_profile'])
        self.assertEqual(manifest, verify_edition(self.output))

    def test_template_css_and_builder_provenance(self):
        manifest = self.build()
        root = Path(__file__).resolve().parents[1]
        for field, path in [('template_sha256', TEMPLATE_PATH), ('stylesheet_sha256', STYLESHEET_PATH)]:
            self.assertEqual(hashlib.sha256((root / path).read_bytes()).hexdigest(), manifest[field])
        html = (self.output / manifest['canonical_artifact']).read_bytes()
        css = (root / STYLESHEET_PATH).read_bytes()
        self.assertIn(b'<style id="press-edition-css">\n' + css + b'</style>', html)
        self.assertNotIn(b'<link ', html)
        self.assertEqual(manifest, verify_edition(self.output))

    def test_css_tampering_rejected_even_after_rehash(self):
        self.build()
        page = self.output / "sample-v1.0.0.html"
        original = page.read_bytes()
        self.assertIn(b'color: #222;', original)
        self._repack(html=original.replace(b'color: #222;', b'color: #333;'))
        with self.assertRaisesRegex(AssetError, "CSS"):
            verify_edition(self.output)

    def test_profile_tampering_rejected_even_after_rehash(self):
        self.build()
        provenance = json.loads((self.output / "provenance.json").read_text())
        provenance['presentation_profile'] = 'press.html.v0'
        self._repack(manifest=provenance)
        with self.assertRaisesRegex(AssetError, "presentation provenance"):
            verify_edition(self.output)

    def test_duplicate_heading_id_rejected_even_after_rehash(self):
        self.sample_with_structures()
        self.build()
        page = self.output / "sample-v1.0.0.html"
        html = page.read_bytes()
        self.assertIn(b'id="def-1"', html)
        self._repack(html=html.replace(b'id="def-1"', b'id="sec-1"', 1))
        with self.assertRaisesRegex(AssetError, "duplicate HTML id"):
            verify_edition(self.output)

    def test_unresolved_html_fragment_rejected_even_after_rehash(self):
        self.sample_with_structures()
        self.build()
        page = self.output / "sample-v1.0.0.html"
        html = page.read_bytes()
        self.assertIn(b'href="#def-1"', html)
        self._repack(html=html.replace(b'href="#def-1"', b'href="#does-not-exist"', 1))
        with self.assertRaisesRegex(AssetError, "unresolved HTML fragment"):
            verify_edition(self.output)

    def test_repeated_builds_are_identical(self):
        self.sample_with_structures()
        self.build()
        other = self.base / "other"
        build_edition(self.repo, self.commit, self.tag, other,
                      fetcher=lambda *_: (SVG, "image/svg+xml"))
        for path in self.output.iterdir():
            self.assertEqual(path.read_bytes(), (other / path.name).read_bytes())


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name in RenderContractTests.__dict__:
        if name.startswith("test_"):
            suite.addTest(RenderContractTests(name))
    return suite
