"""Explicit private drafts never claim publication or execute repository code."""
import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from a_guavajam_static_website_generator.preview import build_preview
from guavajam_test_support.discovery_fixture import Draft


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.draft = Draft(self.root)
        self.output = self.root / 'preview'

    def test_draft_uses_exact_committed_bytes_without_signing_or_public_reads(self):
        sha = self.draft.git('rev-parse', 'HEAD')
        (self.draft.repo / 'content/manuscript.md').write_text('Dirty checkout')
        with patch('guavajam_core.asset_storage.credentials', side_effect=AssertionError('no asset credentials')):
            manifest = build_preview(self.draft.repo, 'refs/heads/draft', self.output)
        self.assertEqual(sha, manifest['revision'])
        self.assertFalse(manifest['publication'])
        self.assertTrue(manifest['provisional_identity'])
        html = (self.output / 'index.html').read_text()
        self.assertIn('Unsigned proposed edition, not issued.', html)
        self.assertIn('The first observation.', html)
        self.assertNotIn('Dirty checkout', html)
        self.assertFalse((self.output / 'CATALOG.json').exists())
        self.assertTrue(json.loads((self.output / 'VIEW.json').read_text())['preview'])

    def test_parentless_snapshot_marks_all_documents_and_retains_exact_inventories(self):
        notes = self.draft.repo / 'content/supporting'
        notes.mkdir()
        (notes / 'note.md').write_text('<a name="document"></a>\n\n# An unlinked note\n\n<a name="claim"></a>\n\nA source passage.\n')
        self.draft.git('checkout', '--orphan', 'first-draft')
        self.draft.git('add', 'content')
        self.draft.git('commit', '-qm', 'First draft snapshot')
        sha = self.draft.git('rev-parse', 'HEAD')
        self.assertEqual([sha], self.draft.git('rev-list', '--parents', '-n', '1', sha).split())
        manifest = build_preview(self.draft.repo, 'refs/heads/first-draft', self.output)
        routes = {path.relative_to(self.output).as_posix() for path in self.output.rglob('*.html')}
        self.assertEqual({'index.html', 'documents.html', 'supporting/note.html'}, routes)
        for route in routes:
            text = (self.output / route).read_text()
            self.assertEqual(1, text.count('<html class="guavajam-preview-new-page"'))
            self.assertIn('name="guavajam:preview" content="unsigned"', text)
            self.assertIn('Unsigned proposed edition, not issued.', text)
            self.assertIn('The committed Git revision identifies the draft source.', text)
            self.assertNotIn('class="guavajam-status"', text)
        view = json.loads((self.output / 'VIEW.json').read_text())
        self.assertTrue(view['preview'])
        self.assertTrue(json.loads((self.output / 'NAV.json').read_text())['preview'])
        for inventory in (manifest['files'], view['files']):
            for route, expected in inventory.items():
                self.assertEqual(expected, hashlib.sha256((self.output / route).read_bytes()).hexdigest())
        self.assertEqual(self.draft.git('show', sha + ':content/manuscript.md'),
                         (self.output / 'source.gfm').read_text().strip())

    def test_invalid_or_uncontained_destination_is_rejected(self):
        with self.assertRaises(ValueError):
            build_preview(self.draft.repo, 'refs/heads/draft', self.draft.repo / 'preview')
        build_preview(self.draft.repo, 'refs/heads/draft', self.output)
        with self.assertRaises(ValueError):
            build_preview(self.draft.repo, 'refs/heads/draft', self.output)

    def test_failed_validation_does_not_emit_partial_preview(self):
        self.draft.source('Field Notes', '<script>bad</script>')
        self.draft.git('add', 'content'); self.draft.git('commit', '-qm', 'Bad draft')
        with self.assertRaises(ValueError):
            build_preview(self.draft.repo, 'refs/heads/draft', self.output)
        self.assertFalse(self.output.exists())
