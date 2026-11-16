"""First complete private preview needs source data, not publisher authority."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from guavajam.preview import build_preview
from guavajam.site import build_site
from guavajam_core.publication_protocol import discover
from guavajam_core.asset_versions import pin_key
from guavajam_core.gateway_index import Index
from guavajam_test_support.discovery_fixture import Draft
from guavajam_test_support.storage_fixture import MemoryStore, KEY
from guavajam_test_support import RESOURCES


class BootstrapPreviewTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.draft = Draft(self.root)
        self.output = self.root / 'preview'

    def preview(self, **kwargs):
        return build_preview(self.draft.repo, 'refs/heads/draft', 'field-notes', self.output,
                             published='2026-11-16', **kwargs)

    def commit(self):
        self.draft.git('add', '-A'); self.draft.git('commit', '-qm', 'Review draft')

    def test_complete_site_without_keys_main_remote_or_public_origin(self):
        supporting = self.draft.repo / 'works/field-notes/supporting'
        supporting.mkdir()
        shutil.copyfile(RESOURCES / 'templates/work/supporting/note.md', supporting / 'note.md')
        self.commit()
        config = (self.draft.repo / 'guavajam.json').read_bytes()
        with patch('guavajam_core.trusted_git.verify_signature', side_effect=AssertionError('no signing needed')), \
             patch('guavajam_core.asset_policy.public_addresses', side_effect=AssertionError('no public reads')), \
             patch('guavajam_core.qualify_assets.credentials', side_effect=AssertionError('no asset credentials')):
            manifest = self.preview()
        sha = manifest['revision']; edition = 'works/field-notes/editions/' + sha
        self.assertEqual('draft', self.draft.git('branch', '--show-current'))
        self.assertEqual('', self.draft.git('remote'))
        self.assertFalse((self.root / 'keys').exists())
        self.assertNotIn('signingkey', (self.draft.repo / '.git/config').read_text())
        self.assertTrue(manifest['provisional_repository'])
        self.assertIsNone(manifest['repository_checkpoint'])
        self.assertIsNone(manifest['verified_catalog'])
        self.assertFalse(manifest['publication'])
        self.assertFalse(manifest['candidate_signed'])
        for name in ['index.html', 'works/field-notes/index.html', edition+'/index.html',
                     edition+'/read/index.html', edition+'/read/cite.html',
                     edition+'/read/supporting/note.html', 'feed.xml', 'sitemap.xml']:
            self.assertTrue((self.output / name).is_file(), name)
        for path in self.output.rglob('*.html'):
            self.assertIn('publisher settings are provisional', path.read_text())
        for path in self.output.rglob('citation.json'):
            self.assertTrue(json.loads(path.read_text())['provisional'])
        self.assertEqual(config, (self.draft.repo / 'guavajam.json').read_bytes())
        self.assertEqual((self.draft.repo / 'works/field-notes/manuscript.md').read_bytes(),
                         (self.output / edition / 'read/source.gfm').read_bytes())
        self.assertFalse((self.output / 'SITE.json').exists())
        self.assertEqual('https://preview.invalid', json.loads((self.output/'CATALOG.json').read_text())['base_url'])
        for name, digest in manifest['files'].items():
            self.assertEqual(digest, hashlib.sha256((self.output/name).read_bytes()).hexdigest())
        with self.assertRaises(ValueError):
            discover(self.draft.repo, ref=sha)
        with self.assertRaisesRegex(ValueError, 'external signer trust'):
            build_site(self.draft.repo, self.output/'CATALOG.json', self.root/'production', initial_catalog=True)
        self.assertFalse((self.root/'production').exists())

    def test_make_preview_accepts_fresh_source_without_signer_arguments(self):
        root = Path(__file__).resolve().parents[3]
        result = subprocess.run(['make', '--no-print-directory', '--file', str(root/'Makefile'), 'preview',
                                 'PYTHON='+sys.executable, 'REPO='+str(self.draft.repo),
                                 'SOURCE_REF=refs/heads/draft', 'WORK=field-notes',
                                 'OUTPUT='+str(self.output), 'PUBLISHED=2026-11-16',
                                 'SSH_ALLOWED_SIGNERS=', 'TRUSTED_GPG_FINGERPRINT='],
                                cwd=self.draft.repo, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(json.loads((self.output/'PREVIEW.json').read_text())['provisional_repository'])

    def test_private_assets_are_qualified_without_mutation_or_public_reads(self):
        store = MemoryStore(seed=True); pin_key(store, Index(store), KEY)
        (self.draft.repo/'asset-policy.json').write_text('{"origin":"https://assets.example.org"}')
        self.draft.source('field-notes', 'Field Notes', '![Private](https://assets.example.org/'+KEY+')')
        self.commit()
        writes = (len(store.uploads), len(store.deleted), len(store.retentions))
        with patch('guavajam_core.asset_policy.public_addresses', side_effect=AssertionError('no public reads')):
            manifest = self.preview(store=store)
        self.assertEqual({KEY}, set(manifest['assets']))
        self.assertEqual(writes, (len(store.uploads), len(store.deleted), len(store.retentions)))

    def test_declarations_never_fall_back_to_provisional_verification(self):
        before = self.draft.git('rev-parse', 'HEAD')
        declarations = ['GuavaJam-Repository: 1', 'GuavaJam-Repository: 2', 'GuavaJam-Unrecognized: 1',
                        'GuavaJam-Publication: 1\nGuavaJam-Work: works/field-notes/manuscript.md\nGuavaJam-Published: 2026-11-16']
        for index, declaration in enumerate(declarations):
            self.draft.git('switch', '-C', 'draft', before)
            self.draft.git('commit', '--allow-empty', '-qm', 'Unapproved declaration\n\n'+declaration)
            with self.subTest(declaration=declaration), self.assertRaises(ValueError):
                self.preview()
            self.assertFalse(self.output.exists())
        with self.assertRaises(ValueError):
            self.preview(fingerprints=['a'*40], base_ref=self.draft.git('rev-parse','HEAD'))
        self.assertFalse(self.output.exists())

    def test_other_branches_and_saved_authority_cannot_be_hidden(self):
        self.draft.git('switch', '-c', 'known')
        self.draft.git('commit', '--allow-empty', '-qm', 'Declare settings\n\nGuavaJam-Repository: 1')
        self.draft.git('switch', 'draft')
        with self.assertRaisesRegex(ValueError, 'omits known'):
            self.preview()
        self.assertFalse(self.output.exists())

    def test_incomplete_or_over_budget_history_cannot_initialize_a_preview(self):
        sha = self.draft.git('rev-parse', 'HEAD')
        shallow = self.draft.repo / '.git/shallow'; shallow.write_text(sha+'\n')
        with self.assertRaisesRegex(ValueError, 'complete history'):
            self.preview()
        shallow.unlink()
        with patch('guavajam_core.publication_protocol.MAX_HISTORY', 0), self.assertRaisesRegex(ValueError, 'history exceeds'):
            self.preview()
        self.assertFalse(self.output.exists())

    def test_provisional_settings_cannot_invent_published_editions(self):
        self.draft.settings['editions'] = {'a'*40: {'published':'2026-11-01'}}
        self.draft.write_settings(); self.commit()
        with self.assertRaisesRegex(ValueError, 'cannot claim issued'):
            self.preview()
        self.assertFalse(self.output.exists())
