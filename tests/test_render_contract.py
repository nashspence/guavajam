"""Executable, offline assertions for the versioned press.html.v1 output contract."""
from __future__ import annotations

import hashlib
import html
import subprocess
import sys
import json
import re
import runpy
import unittest
from unittest.mock import patch
from pathlib import Path

from asset_policy import AssetError
from build_edition import build_edition
from editorial_notices import release_notes, render_initial_body
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

    def test_checksums_use_the_exact_archived_bytes(self):
        import shutil
        import tempfile
        source = Path(__file__).parent / 'fixtures/archived-v1'
        with tempfile.TemporaryDirectory() as temp:
            directory = shutil.copytree(source, Path(temp) / 'edition')
            sums = directory / 'SHA256SUMS'
            sums.write_bytes(sums.read_bytes().replace(b'\n', b'\r\n'))
            with self.assertRaisesRegex(AssetError, 'checksums do not match'):
                verify_edition(directory)

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
        manifest = self.build()
        html = self.page().read_text(encoding="utf-8")
        self.assertIn('Nash Spence. <cite lang="en">Sample Work</cite>. Press, edition v1.0.0.', ' '.join(html.split()))
        record = "https://press.example.org/works/sample-work/v1.0.0"
        self.assertEqual(record, manifest["record_url"])
        self.assertIn(f'href="{record}/read"', html)
        self.assertIn(f'href="{record}">{record}</a>', html)
        self.assertIn('Corrections and withdrawal status</a>', html[:html.index('<article')])
        parser = EditionHTML(PROFILE)
        parser.feed(html)
        self.assertEqual(parser.status_links, [record])
        self.assertEqual(parser.permalinks, [(key, record + '/read#' + key) for key in parser.heading_ids])
        self.assertIn(('theorem-1', record + '/read#theorem-1'), parser.permalinks)
        self.assertIn('href="#def-1"', html)  # existing navigation also works offline
        self.assertEqual((manifest['author'], manifest['language']), ('Nash Spence', 'en'))
        self.assertNotIn('initial_note', manifest)
        self._repack(html=self.page().read_bytes().replace(
            ('href="' + record + '"').encode(), b'href="https://other.example.org/"'))
        with self.assertRaisesRegex(AssetError, "edition record link"):
            verify_edition(self.output)

    def test_offline_theorem_link_cannot_point_to_another_passage(self):
        self.sample_with_structures()
        self.build()
        original = self.page().read_bytes()
        self._repack(html=original.replace(b'/read#theorem-1"', b'/read#def-1"'))
        with self.assertRaisesRegex(AssetError, 'canonical permalink'):
            verify_edition(self.output)

    def test_reviewed_attribution_includes_work_registration_and_source_ids(self):
        self.sample_with_structures()
        path = self.repo / 'works/sample-work/manuscript.md'
        path.write_text(path.read_text().replace('language: en', 'language: fr-CA\n'
            'author_orcid: 0000-0002-1825-0097\nidentifiers:\n  arxiv: "1501.01234"'))
        reg = self.repo / 'registrations/works/sample-work.json'
        reg.parent.mkdir(parents=True)
        reg.write_text(json.dumps({'schema': 'press.work-registration.v1', 'work': 'sample-work',
                                   'identifiers': {'concept_doi': '10.1234/essai'}}, sort_keys=True, indent=2) + '\n')
        self.git('add', '-A')
        self.git('commit', '-qm', 'Review bibliographic metadata')
        self.commit = self.git('rev-parse', 'HEAD')
        manifest = self.build()
        self.assertEqual(manifest['work_identifiers'], {'arxiv': '1501.01234', 'concept_doi': '10.1234/essai'})
        self.assertEqual(manifest['author_orcid'], '0000-0002-1825-0097')
        self.assertEqual(manifest['language'], 'fr-CA')
        body = manifest['initial_release_body']
        for field in ('Author: Nash Spence', 'Language: fr-CA', 'Concept DOI: 10.1234/essai',
                      'Author ORCID: 0000-0002-1825-0097', 'arXiv work: 1501.01234'):
            self.assertIn(field, body)

    def test_trusted_registration_verifies_the_reviewed_bytes_offline(self):
        from issuance import authorization
        manifest = self.build()
        record = authorization(self.output, manifest)
        registration = self.base / "registration.json"
        verifier = Path(__file__).resolve().parents[1] / "scripts/verify_edition.py"

        def check(**extra):
            registration.write_text(json.dumps({**record, **extra}, sort_keys=True, indent=2) + "\n")
            return subprocess.run([sys.executable, str(verifier), "--registration", str(registration),
                                   str(self.output)], text=True, capture_output=True)

        self.assertIn("pending authorization", check().stdout)
        self.assertIn("publication receipt", check(issuing=True, published_at="2026-11-04T01:00:00Z").stdout)
        self.assertNotEqual(check(cancellation_reason="Retired").returncode, 0)
        self._repack(html=self.page().read_bytes().replace(b"</article>", b"<p>Changed.</p></article>"))
        verify_edition(self.output)
        self.assertIn("artifact_sha256 differs", check().stderr)

    def test_edition_rights_are_literal_and_travel_with_the_standalone_html(self):
        directory = self.repo / "works/sample/editions"
        directory.mkdir()
        path = directory / "v1.0.0.rights.txt"
        statement = 'Plate <img src=x> © Lee & Co. [Exception](#elsewhere) applies.'
        path.write_text(statement + "\n", encoding="utf-8")
        (directory / "v2.0.0.rights.txt").write_text("Other terms.\n")
        (directory / "v1.0.0.md").write_text("Editorial introduction.\n")
        self.git("add", "works/sample/editions")
        self.git("commit", "-qm", "Review edition rights and notes")
        self.commit = self.git("rev-parse", "HEAD")
        path.write_text("Unreviewed terms.\n")
        manifest = self.build()
        self.assertEqual(statement, manifest["rights_statement"])
        self.assertEqual(manifest["initial_note"], "Editorial introduction.\n")
        page = self.page().read_text(encoding="utf-8")
        notes = subprocess.check_output(["pandoc", "-f", "gfm", "-t", "html5", "--wrap=none"],
                                        input=release_notes(manifest), text=True)
        for rendered in (page, notes):
            self.assertIn(statement, html.unescape(" ".join(rendered.split())))
            self.assertNotIn("<img src=x>", rendered)
            self.assertNotIn('href="#elsewhere"', rendered)
        self.assertNotIn("Editorial introduction.", page)
        self.assertIn("Editorial introduction.", notes)
        with self.assertRaisesRegex(AssetError, "reserved release field"):
            render_initial_body(manifest, "Edition rights: contradictory.\n")

    def test_rehashed_package_must_preserve_the_declared_rights_statement(self):
        manifest = self.build()
        original = self.page().read_bytes()
        span = b'<span data-press-field="rights">Plate &lt;I&gt;\nby permission.</span>'
        statement = "Plate <I>  by permission."
        declared = {**manifest, "rights_statement": statement}

        def repack(rights_markup, provenance):
            page = original.replace(b"</footer>", rights_markup + b"</footer>")
            self._repack(html=page, manifest=dict(provenance))

        repack(span, declared)
        verify_edition(self.output)
        for markup, provenance in [
            (b"", declared),
            (span, manifest),
            (span, {**manifest, "rights_statement": []}),
            (span + span, declared),
            (span.replace(b"permission", b"purchase"), declared),
            (span.replace(b"by permission.", b"<b>by permission.</b>"), declared),
        ]:
            with self.subTest(markup=markup, provenance=provenance):
                repack(markup, provenance)
                with self.assertRaisesRegex(AssetError, "edition rights"):
                    verify_edition(self.output)

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
