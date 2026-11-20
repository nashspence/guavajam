"""Core exports a derived, exact history without a view or publisher database."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from guavajam_core.export_catalog import export
from guavajam_core.publication_protocol import discover
from guavajam_core.publication_commit import commit_identity, signature
from guavajam_core.catalog import encoded
from guavajam_test_support.discovery_fixture import Publisher

ROOT = Path(__file__).resolve().parents[1]


class CatalogExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.p = Publisher(self.temp.name)
        self.sha = self.p.revisions[0]
        self.output = Path(self.temp.name) / 'catalog.json'

    def test_export_needs_only_git_source_and_external_trust(self):
        catalog, evidence = discover(self.p.repo, **self.p.trust)
        self.assertEqual([self.sha], evidence['publications'])
        _, changed = export(self.p.repo, self.output, **self.p.trust)
        self.assertTrue(changed)
        self.assertEqual(catalog, json.loads(self.output.read_text()))
        self.assertFalse(evidence['assets_verified'])

    def test_real_make_target_emits_exact_catalog_and_noop_keeps_the_file(self):
        args = ['make', 'catalog-export', 'PYTHON=' + sys.executable,
                'REPO=' + str(self.p.repo), 'OUTPUT=' + str(self.output),
                'SSH_ALLOWED_SIGNERS=' + str(self.p.allowed_signers), 'TRUSTED_GPG_FINGERPRINT=']
        subprocess.run(args, cwd=ROOT, env=self.p.env, check=True, capture_output=True)
        catalog, _ = discover(self.p.repo, **self.p.trust)
        self.assertEqual(encoded(catalog), self.output.read_bytes())
        stamp = self.output.stat().st_mtime_ns
        result = subprocess.run(args, cwd=ROOT, env=self.p.env, check=True, capture_output=True, text=True)
        self.assertIn('Unchanged', result.stdout)
        self.assertEqual(stamp, self.output.stat().st_mtime_ns)

    def test_discovered_edition_is_independently_verifiable(self):
        work, source = commit_identity(self.p.repo, self.sha)
        self.assertEqual('content/manuscript.md', work)
        self.assertIn('The first observation.', source)
        self.assertEqual('external-ssh-allowed-signers', signature(self.p.repo, self.sha, **self.p.trust))

    def test_invalid_new_content_does_not_replace_last_verified_catalog(self):
        export(self.p.repo, self.output, **self.p.trust)
        original = self.output.read_bytes()
        (self.p.repo / 'content' / 'manuscript.md').write_text('# Invalid manuscript\n')
        self.p.publish('An invalid source')
        with self.assertRaises(ValueError):
            export(self.p.repo, self.output, **self.p.trust)
        self.assertEqual(original, self.output.read_bytes())

    def test_last_export_is_automatically_a_continuity_checkpoint(self):
        self.p.source('Field Notes', 'Second observation.')
        second = self.p.publish('Add second observation')
        export(self.p.repo, self.output, **self.p.trust)
        original = self.output.read_bytes()
        self.p.git('reset', '--hard', self.sha)
        with self.assertRaisesRegex(ValueError, 'first-parent'):
            export(self.p.repo, self.output, **self.p.trust)
        self.assertEqual(original, self.output.read_bytes())


if __name__ == '__main__':
    unittest.main()
