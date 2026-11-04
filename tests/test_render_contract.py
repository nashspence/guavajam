"""Executable, offline assertions for the versioned press.html.v1 output contract."""
from __future__ import annotations

import hashlib
import json
import re
import runpy
import unittest
from unittest.mock import patch
from pathlib import Path

from asset_policy import AssetError
from build_edition import build_edition
from render_profile import PROFILE, STYLESHEET_PATH, TEMPLATE_PATH
from test_assets import EditionTests
from verify_edition import EditionHTML, verify_edition


class ArchivedEditionTests(unittest.TestCase):
    # These bytes are a synthetic v1 package, independent of the current builder.
    def test_offline_verification_of_archived_profile(self):
        directory = Path(__file__).parent / "fixtures/archived-v1"
        # Import the verifier after switching the default to catch import-time coupling.
        verifier_path = Path(__file__).resolve().parents[1] / "scripts/verify_edition.py"
        with patch("render_profile.PROFILE", "press.html.v2"):
            verify_archived = runpy.run_path(str(verifier_path))["verify_edition"]
            manifest = verify_archived(directory)
        self.assertEqual(manifest["presentation_profile"], "press.html.v1")

    def test_edition_link_targets(self):
        for href in ("#section", "https://example.org/work", "mailto:press@example.org"):
            with self.subTest(href=href):
                parser = EditionHTML("press.html.v1")
                parser.feed(f'<a href="{href}">Reference</a>')
                self.assertEqual(parser.errors, [])
        for href in ("../source.md", "figure.pdf#page=2", "/manual", "?draft=1",
                     "//example.org/doc", "javascript:alert(1)", "https:local"):
            with self.subTest(href=href):
                parser = EditionHTML("press.html.v1")
                parser.feed(f'<a href="{href}">Reference</a>')
                self.assertTrue(parser.errors)


class RenderContractTests(EditionTests):
    """The reviewed fixture is a real Git commit; no release or network access."""

    def sample_with_structures(self):
        # Build the real author-facing template as an edition. No parallel sample prose.
        root = Path(__file__).resolve().parents[1]
        original = self.repo / "works/sample/manuscript.md"
        original.unlink()
        original.parent.rmdir()
        manuscript = self.repo / "works/sample-work/manuscript.md"
        manuscript.parent.mkdir(parents=True)
        manuscript.write_bytes((root / "templates/work/manuscript.md").read_bytes())
        (self.repo / "asset-policy.json").write_bytes((root / "asset-policy.json").read_bytes())
        self.preview_asset = (root / "storage/preview.svg").read_bytes()
        self.tag = "works/sample-work/v1.0.0"
        self.git("add", "-A")
        self.git("commit", "-qm", "Publish the starter manuscript as an offline fixture")
        self.commit = self.git("rev-parse", "HEAD")

    def build(self):
        if hasattr(self, "preview_asset"):
            return build_edition(self.repo, self.commit, self.tag, self.output,
                                 fetcher=lambda *_: (self.preview_asset, "image/svg+xml"))
        return super().build()

    def page(self):
        return self.output / (self.tag.split("/")[1] + "-v1.0.0.html")

    def _repack(self, html=None, manifest=None):
        page = self.page()
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
        html = self.page().read_text(encoding="utf-8")
        parser = EditionHTML(PROFILE)
        parser.feed(html)
        parser.close()
        self.assertIn('id="sec-1"', html)
        self.assertIn('id="def-1"', html)
        self.assertIn('id="proof-1"', html)
        self.assertEqual(["definition", "theorem", "proof"], parser.formal_kinds)
        self.assertTrue({'sec-1', 'def-1', 'theorem-1', 'proof-1', 'sec-2', 'sec-3'} <= parser.ids)
        self.assertNotIn('<a id="def-1"></a>', html)
        self.assertIn('data-press-kind="definition"', html)
        self.assertIn('data-press-kind="proof"', html)
        self.assertIn('data-press-kind="theorem"', html)
        self.assertIn('<blockquote>', html)
        self.assertIn('<ol', html)
        self.assertIn('<ul', html)
        self.assertIn('type="checkbox"', html)
        self.assertIn('<del>', html)
        self.assertIn('<strong>', html)
        self.assertIn('<em>', html)
        self.assertIn('class="footnote-ref"', html)
        self.assertIn('<table', html)
        self.assertIn('<code', html)
        # Inline LaTeX is deliberate; the published edition must render it.
        self.assertEqual(9, html.count('<math'))
        self.assertIn('<mi>S</mi>', html)
        self.assertIn('<mi>R</mi>', html)
        self.assertIn('data:image/svg+xml;base64,', html)
        self.assertEqual(1, len(manifest['assets']))
        self.assertEqual(hashlib.sha256(self.preview_asset).hexdigest(), manifest['assets'][0]['sha256'])
        self.assertIn('id="press-edition-css"', html)
        self.assertEqual(PROFILE, manifest['presentation_profile'])
        self.assertEqual(manifest, verify_edition(self.output))

    def test_citation_identifies_the_exact_release(self):
        self.sample_with_structures()
        self.build()
        html = self.page().read_text(encoding="utf-8")
        self.assertIn("Nash Spence. <cite>Sample Work</cite>. Press, edition v1.0.0.", " ".join(html.split()))
        record = "https://github.com/nashspence/press/releases/tag/works/sample-work/v1.0.0"
        self.assertIn(f'href="{record}">{record}</a>', html)

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
        page = self.page()
        original = page.read_bytes()
        self.assertIn(b'color: #222;', original)
        self._repack(html=original.replace(b'color: #222;', b'color: #333;'))
        with self.assertRaisesRegex(AssetError, "CSS"):
            verify_edition(self.output)

    def test_profile_tampering_rejected_even_after_rehash(self):
        self.build()
        provenance = json.loads((self.output / "provenance.json").read_text())
        provenance['presentation_profile'] = 'press.html.v99'
        self._repack(manifest=provenance)
        with self.assertRaisesRegex(AssetError, "presentation provenance"):
            verify_edition(self.output)

    def test_duplicate_heading_id_rejected_even_after_rehash(self):
        self.sample_with_structures()
        self.build()
        page = self.page()
        html = page.read_bytes()
        self.assertIn(b'id="def-1"', html)
        self._repack(html=html.replace(b'id="def-1"', b'id="sec-1"', 1))
        with self.assertRaisesRegex(AssetError, "duplicate HTML id"):
            verify_edition(self.output)

    def test_unresolved_html_fragment_rejected_even_after_rehash(self):
        self.sample_with_structures()
        self.build()
        page = self.page()
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
                      fetcher=lambda *_: (self.preview_asset, "image/svg+xml"))
        for path in self.output.iterdir():
            self.assertEqual(path.read_bytes(), (other / path.name).read_bytes())


def load_tests(loader, tests, pattern):
    suite = loader.loadTestsFromTestCase(ArchivedEditionTests)
    for name in RenderContractTests.__dict__:
        if name.startswith("test_"):
            suite.addTest(RenderContractTests(name))
    return suite
