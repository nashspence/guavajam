"""Private branch previews share rendering, preserve source bytes and carry no authority."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from guavajam.preview import build_preview
from guavajam.update import verify_public_delivery
from guavajam_core.asset_policy import AssetError
from guavajam_core.asset_versions import pin_key
from guavajam_core.gateway_index import Index
from guavajam_test_support.discovery_fixture import Publisher
from guavajam_test_support.storage_fixture import MemoryStore, RAW, KEY


@unittest.skipUnless(shutil.which('gpg') and shutil.which('pandoc'), 'Git/GPG/Pandoc fixture')
class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.publisher = Publisher(self.root)
        self.enterContext(patch.dict(os.environ, {'GNUPGHOME': self.publisher.env['GNUPGHOME']}))
        self.output = self.root/'preview'
        self.store = MemoryStore(seed=True)
        pin_key(self.store, Index(self.store), KEY)

    def source(self, assets=True):
        (self.publisher.repo/'asset-policy.json').write_text('{"origin":"https://assets.example.org"}\n')
        body = 'Unpublished observation.'
        if assets:
            url = 'https://assets.example.org/'+KEY
            body += '\n\n![Diagram][diagram]\n\n[diagram]: '+url+'\n\n```text\n'+url+'\n```'
        self.publisher.source('field-notes', 'Field Notes', body)
        self.publisher.git('add', '-A'); self.publisher.git('commit', '-qm', 'Unpublished change')
        return self.publisher.git('rev-parse', 'HEAD')

    def test_preview_keeps_canonical_source_and_routes_only_presented_assets(self):
        sha = self.source()
        before = (self.publisher.repo/'works/field-notes/manuscript.md').read_bytes()
        uploads = len(self.store.uploads)
        with patch('guavajam_core.asset_policy.public_addresses', side_effect=AssertionError('public access before release')):
            manifest = build_preview(self.publisher.repo, 'refs/heads/main', 'field-notes', self.output, store=self.store)
        self.assertEqual(sha, manifest['revision'])
        self.assertFalse(manifest['publication'])
        self.assertEqual({KEY}, set(manifest['assets']))
        self.assertEqual(uploads, len(self.store.uploads))
        self.assertEqual(before, (self.publisher.repo/'works/field-notes/manuscript.md').read_bytes())
        self.assertEqual(sha, self.publisher.git('rev-parse', 'HEAD'))
        reader = self.output/'works/field-notes/preview/index.html'
        html = reader.read_text()
        self.assertIn('src="/_assets/'+KEY+'"', html)
        self.assertIn('https://assets.example.org/'+KEY, html)  # Literal code is not a URL target.
        self.assertIn('PRIVATE DRAFT PREVIEW', html)
        self.assertIn('UNSIGNED PREVIEW', html)
        self.assertEqual(before, (reader.parent/'source.gfm').read_bytes())
        self.assertNotIn('PREVIEW.json', manifest['files'])
        self.assertFalse(list(self.output.rglob('VIEW.json')))
        self.assertEqual(0o700, self.output.stat().st_mode & 0o777)
        for name, digest in manifest['files'].items():
            self.assertEqual(digest, hashlib.sha256((self.output/name).read_bytes()).hexdigest())
        self.assertNotIn('private-secret', (self.output/'PREVIEW.json').read_text())

    def test_no_asset_draft_does_not_request_storage_credentials(self):
        self.source(assets=False)
        with patch('guavajam_core.qualify_assets.credentials', side_effect=AssertionError('unneeded credentials')):
            manifest = build_preview(self.publisher.repo, 'refs/heads/main', 'field-notes', self.output)
        self.assertEqual({}, manifest['assets'])

    def test_signed_site_context_is_separate_from_unsigned_candidate(self):
        published = self.publisher.publish()
        self.publisher.git('switch', '-c', 'review')
        sha = self.source()
        manifest = build_preview(self.publisher.repo, 'refs/heads/review', 'field-notes', self.output,
                                 base_ref='refs/heads/main', fingerprints=[self.publisher.fingerprint], store=self.store)
        self.assertEqual(sha, manifest['revision'])
        catalog = json.loads((self.output/'CATALOG.json').read_text())
        self.assertEqual([published], [e['revision'] for w in catalog['works'] for e in w['editions']])
        self.assertIn('Review draft', (self.output/'works/field-notes/index.html').read_text())
        self.assertIn('PRIVATE DRAFT PREVIEW', (self.output/'index.html').read_text())
        self.assertFalse((self.output/'SITE.json').exists())

    def test_private_output_and_fail_closed_storage_are_required(self):
        self.source()
        with self.assertRaises(AssetError):
            build_preview(self.publisher.repo, 'refs/heads/main', 'field-notes', self.publisher.repo/'preview', store=self.store)
        self.store.bytes[(KEY, 'version_0')] = b'!'*len(RAW)
        with self.assertRaises(AssetError):
            build_preview(self.publisher.repo, 'refs/heads/main', 'field-notes', self.output, store=self.store)
        self.assertFalse(self.output.exists())


class ReadinessTests(unittest.TestCase):
    def test_old_generation_receipts_without_public_readiness_are_rejected(self):
        from guavajam.update import current
        from guavajam_core.catalog import CatalogError
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = 'generations/' + 'a'*32 + '/site'
            site = root / name
            site.mkdir(parents=True)
            (site / 'CATALOG.json').write_text('{}')
            (root / 'current').symlink_to(name)
            receipt = {'schema': 'guavajam.update.v1', 'catalog_sha256': hashlib.sha256(b'{}').hexdigest()}
            for flag in [None, False, 'true']:
                (site.parent / 'STATE.json').write_text(json.dumps({**receipt, 'public_delivery_verified': flag}))
                with self.assertRaises(CatalogError):
                    current(root)
            (site.parent / 'STATE.json').write_text(json.dumps({**receipt, 'public_delivery_verified': True}))
            self.assertEqual(site, current(root)[0])

    def test_only_new_or_newly_readable_editions_need_public_qualification(self):
        previous = {'works': [{'editions': [{'revision': 'a'*40, 'status': 'available'},
                                           {'revision': 'b'*40, 'status': 'unavailable'}]}]}
        catalog = {'works': [{'editions': [{'revision': 'a'*40, 'status': 'available'},
                                          {'revision': 'b'*40, 'status': 'available'},
                                          {'revision': 'c'*40, 'status': 'withdrawn'},
                                          {'revision': 'd'*40, 'status': 'unavailable'}]}]}
        with patch('guavajam_core.publication_commit.verify') as verify:
            verify_public_delivery(Path('.'), catalog, previous=previous, fingerprints=['F'*40])
            self.assertEqual(['b'*40, 'c'*40], [call.args[1] for call in verify.call_args_list])
            self.assertTrue(all(call.kwargs['online_media'] for call in verify.call_args_list))
            self.assertFalse(any('store' in call.kwargs for call in verify.call_args_list))
        with patch('guavajam_core.publication_commit.verify', side_effect=AssetError('not released')):
            with self.assertRaises(AssetError):
                verify_public_delivery(Path('.'), catalog, fingerprints=['F'*40])


if __name__ == '__main__':
    unittest.main()
