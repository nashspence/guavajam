"""Explicit private drafts never claim publication or execute repository code."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from guavajam.preview import build_preview
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
        self.assertIn('UNSIGNED PREVIEW', html)
        self.assertIn('The first observation.', html)
        self.assertNotIn('Dirty checkout', html)
        self.assertFalse((self.output / 'CATALOG.json').exists())
        self.assertTrue(json.loads((self.output / 'VIEW.json').read_text())['preview'])

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
