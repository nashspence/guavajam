"""Executable, offline assertions for the versioned press.html.v1 output contract."""
from __future__ import annotations

import hashlib
import html
import copy
from contextlib import contextmanager
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


@contextmanager
def starter_package():
    fixture = RenderContractTests()
    try:
        fixture.setUp()
        fixture.sample_with_structures()
        fixture.build()
        yield fixture
    finally:
        fixture.doCleanups()


class SavedEditionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = self.enterContext(starter_package())

    def test_saved_package_verifies_without_source_checkout_or_builder_inputs(self):
        import shutil
        fixture = self.fixture
        expected = verify_edition(fixture.output)
        shutil.rmtree(fixture.repo)
        read = Path.read_bytes
        root = Path(__file__).resolve().parents[1]
        def package_only(path):
            if path.is_relative_to(root):
                self.fail('verifier read a current builder input: ' + str(path))
            return read(path)
        # Re-import after changing the default, to catch import-time coupling.
        verifier_path = Path(__file__).resolve().parents[1] / "scripts/verify_edition.py"
        with (patch("render_profile.PROFILE", "press.html.future"),
              patch.object(Path, 'read_bytes', package_only),
              patch('build_edition.git', side_effect=AssertionError('Git is unavailable')),
              patch('build_edition.build_edition', side_effect=AssertionError('builder is unavailable')),
              patch('socket.create_connection', side_effect=AssertionError('offline'))):
            verifier = runpy.run_path(str(verifier_path))["verify_edition"]
            self.assertEqual(expected, verifier(fixture.output))

    def test_checksums_use_the_exact_saved_bytes(self):
        sums = self.fixture.output / 'SHA256SUMS'
        sums.write_bytes(sums.read_bytes().replace(b'\n', b'\r\n'))
        with self.assertRaisesRegex(AssetError, 'checksums do not match'):
            verify_edition(self.fixture.output)

    def test_rehashed_package_cannot_omit_current_required_metadata(self):
        fixture = self.fixture
        original = verify_edition(fixture.output)
        for fields in (('record_url',), ('initial_release_body',), ('author',), ('language',),
                       ('record_url', 'initial_release_body', 'author', 'language')):
            with self.subTest(fields=fields):
                changed = copy.deepcopy(original)
                for field in fields:
                    del changed[field]
                fixture._repack(manifest=changed)
                with self.assertRaises(AssetError):
                    verify_edition(fixture.output)

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
        import shutil
        shutil.copytree(root / 'templates/work/supporting', manuscript.parent / 'supporting')
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
        self.assertIn('id="eq-1"', html)
        self.assertIn('href="#eq-1"', html)
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
        self.assertIn('class="press-scroll press-table-scroll" role="group" aria-label="Table" tabindex="0"', " ".join(html.split()))
        self.assertIn('class="press-scroll press-math-scroll" role="group" aria-label="Displayed mathematics" tabindex="0"', " ".join(html.split()))
        self.assertNotIn('aria-label="Scrollable table"', html)
        self.assertNotIn('aria-label="Scrollable equation"', html)
        self.assertIn('<code', html)
        # Inline LaTeX is deliberate; the published edition must render it.
        self.assertEqual(10, html.count('<math'))
        self.assertIn('<mi>S</mi>', html)
        self.assertIn('<mi>R</mi>', html)
        self.assertIn('data:image/svg+xml;base64,', html)
        self.assertEqual(1, len(manifest['assets']))
        self.assertEqual(hashlib.sha256(self.preview_asset).hexdigest(), manifest['assets'][0]['sha256'])
        self.assertIn('id="press-edition-css"', html)
        self.assertEqual(PROFILE, manifest['presentation_profile'])
        self.assertEqual(manifest, verify_edition(self.output))

    def test_starter_contents_are_local_and_canonical_permalinks_are_unchanged(self):
        self.sample_with_structures()
        self.build()
        raw = self.page().read_text(encoding="utf-8")
        self.assertIn('<a class="press-skip" href="#press-main" lang="en">', raw)
        self.assertIn('<main id="press-main" tabindex="-1">', raw)
        self.assertIn('<a class="press-contents-link" href="#press-contents">Contents</a>', raw)
        toc = re.search(r'<nav id="press-contents".*?</nav>', raw, flags=re.S)
        self.assertIsNotNone(toc)
        self.assertEqual(["#sec-1", "#sec-2", "#sec-3", "#record-md-4-note"],
                         re.findall(r'href="(#[^"]+)"', toc.group()))
        self.assertNotIn("#theorem-1", toc.group())
        self.assertNotIn("#support-md-", toc.group())
        parser = EditionHTML(PROFILE); parser.feed(raw); parser.close()
        self.assertEqual([], parser.errors)
        self.assertIn(("sec-1", "https://press.example.org/works/sample-work/v1.0.0/read#sec-1"),
                      parser.permalinks)
        self.assertEqual([], [frag for frag in parser.fragment_links if frag not in parser.ids])

    def test_starter_caption_survives_validation_build_and_offline_verification(self):
        from validate_sources import parse_document, plain_text, validate_repository
        self.sample_with_structures()
        source = Path("works/sample-work/manuscript.md")
        ast, errors = parse_document(self.repo / source, source)
        self.assertEqual(errors, [])
        self.assertEqual(validate_repository(self.repo, [Path(p) for p in self.git("ls-files").splitlines()]), [])
        captions = [b["c"][1] for b in ast["blocks"] if b["t"] == "Table"]
        self.assertEqual([plain_text(c[1]) for c in captions], ["Table 1 — Notation"])
        manifest = self.build()
        page = self.page().read_text(encoding="utf-8")
        direct_captions = re.findall(r"<table\b[^>]*>\s*<caption>(.*?)</caption>", page, re.S)
        self.assertEqual([html.unescape(" ".join(c.split())) for c in direct_captions], ["Table 1 — Notation"])
        self.assertNotRegex(page, r"<p>:\s*Table")
        self.assertIn('class="press-scroll press-table-scroll" role="group" aria-label="Table" tabindex="0"', " ".join(page.split()))
        self.assertEqual(manifest, verify_edition(self.output))

    def test_invalid_caption_cannot_pass_validation_or_build(self):
        from validate_sources import validate_repository
        self.sample_with_structures()
        path = self.repo / "works/sample-work/manuscript.md"
        path.write_text(path.read_text().replace(": Table 1 — Notation", ": Table 1 — *Notation*"))
        self.git("commit", "-qam", "Reject a formatted caption")
        self.commit = self.git("rev-parse", "HEAD")
        errors = validate_repository(self.repo, [Path(p) for p in self.git("ls-files").splitlines()])
        self.assertTrue(any("table caption: plain text only" in e for e in errors), errors)
        with self.assertRaisesRegex(AssetError, "table caption: plain text only"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_caption_compiler_is_part_of_exact_builder_provenance(self):
        root = Path(__file__).resolve().parents[1]
        compiler = root / "scripts/table_captions.py"
        read = Path.read_bytes
        def changed(path):
            return read(path) + (b"# uncommitted compiler change\n" if path == compiler else b"")
        with patch.object(Path, "read_bytes", changed):
            with self.assertRaisesRegex(AssetError, "builder files must match.*scripts/table_captions.py"):
                self.build()

    def test_plain_and_highlighted_code_focus_the_native_pre(self):
        from html.parser import HTMLParser
        self.sample_with_structures()
        source = self.repo / "works/sample-work/manuscript.md"
        literal = "LONG_LITERAL_" * 40
        source.write_text(source.read_text().replace("A fenced code block preserves literal code and indentation:",
            "```\n" + literal + "\n```\n\nA fenced code block preserves literal code and indentation:"))
        self.git("commit", "-qam", "Review plain and highlighted code")
        self.commit = self.git("rev-parse", "HEAD")
        self.build()
        class NativeCode(HTMLParser):
            def __init__(self):
                super().__init__(); self.pres = []; self.wrappers = []; self.text = []; self.in_pre = False
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == "pre":
                    self.pres.append(attrs); self.in_pre = True
                if tag == "div" and "sourceCode" in attrs.get("class", "").split():
                    self.wrappers.append(attrs)
            def handle_endtag(self, tag):
                if tag == "pre": self.in_pre = False
            def handle_data(self, text):
                if self.in_pre: self.text.append(text)
        parser = NativeCode(); parser.feed(self.page().read_text())
        self.assertEqual(len(parser.pres), 3)
        self.assertTrue(all(p.get("tabindex") == "0" and p.get("role") == "group" and p.get("aria-label") == "Code block" for p in parser.pres))
        self.assertTrue(parser.wrappers)
        self.assertTrue(all("tabindex" not in d for d in parser.wrappers))
        self.assertIn(literal, "".join(parser.text))
        self.assertIn("def identity(value):", "".join(parser.text))

    def test_mathematical_title_in_article_and_citation(self):
        self.sample_with_structures()
        manuscript = self.repo / "works/sample-work/manuscript.md"
        manuscript.write_text(manuscript.read_text(encoding="utf-8").replace(
            "title: Sample Work", "title: The α-invariant").replace(
            "# Sample Work", r"# The $\alpha$-invariant"), encoding="utf-8")
        self.git("commit", "-qam", "Use a mathematical heading with a plain citation")
        self.commit = self.git("rev-parse", "HEAD")
        manifest = self.build()
        page = self.page().read_text(encoding="utf-8")
        self.assertEqual(manifest["title"], "The α-invariant")
        self.assertIn("<title>The α-invariant — v1.0.0</title>", page)
        self.assertIn('<cite lang="en">The α-invariant</cite>', " ".join(page.split()))
        self.assertRegex(page, r"(?s)<h1[^>]*>The\s+<math\b.*?<mi>α</mi>.*?</math>-invariant</h1>")

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
        self.assertIn(b'--press-ink: #222;', original)
        self._repack(html=original.replace(b'--press-ink: #222;', b'--press-ink: #333;'))
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
    suite = loader.loadTestsFromTestCase(SavedEditionTests)
    for name in RenderContractTests.__dict__:
        if name.startswith("test_"):
            suite.addTest(RenderContractTests(name))
    return suite
