"""Publication defaults do not impose optional storage-provider guarantees."""
import contextlib
from dataclasses import replace
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, Mock
from urllib.parse import quote

from guavajam_core.asset_policy import AssetError
from guavajam_core.asset_versions import pin_key
from guavajam_core.gateway_index import Index
from guavajam_core.prepare_publication import prepare
from guavajam_core.check_publications import check
from guavajam_test_support.storage_fixture import MemoryStore, KEY, Response


class PublicationStorageTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root/'asset-policy.json').write_text('{"origin":"https://assets.example.org"}')
        self.files = [Path('works/test/manuscript.md')]
        @contextlib.contextmanager
        def snapshot(*args):
            yield self.root, self.files
        self.enterContext(patch('guavajam_core.prepare_publication.snapshot', snapshot))
        self.enterContext(patch('guavajam_core.prepare_publication.validate_repository', return_value=[]))
        self.enterContext(patch('guavajam_core.prepare_publication.references', return_value=['https://assets.example.org/'+KEY]))
        self.full = self.enterContext(patch('guavajam_core.prepare_publication.verify_source_assets'))
        self.enterContext(patch('guavajam_core.prepare_publication.git', return_value=b'a'*40))

    def prepare(self, store, **kwargs):
        return prepare(self.root, 'a'*40, 'test', published='2026-11-13', store=store, **kwargs)

    def public(self, mapping, *, etag=None):
        return patch('guavajam_core.prepare_publication.public_response', side_effect=lambda *a, **kw: Response(headers={
            'ETag': etag or mapping['etag'], 'X-Press-Retain-Until': str(mapping['retain_until'] or 0),
            **({'X-Press-Asset-Version': quote(mapping['version_id'], safe='')} if mapping['version_id'] not in {None, 'null'} else {})}))

    def test_plain_and_versioned_buckets_without_lock_can_prepare(self):
        for versioning in [False, True]:
            store = MemoryStore(versioning=versioning, seed=True)
            mapping = pin_key(store, Index(store), KEY)
            with self.public(mapping):
                result = self.prepare(store)
            self.assertTrue(result['assets_verified'])
            self.assertFalse(result['retention_verified'])
            self.assertEqual(mapping['version_id'], result['assets'][0]['version_id'])
            self.assertIsNone(result['assets'][0]['retain_until'])
            self.assertFalse(self.full.call_args.kwargs['require_retention'])
            with self.public(mapping), self.assertRaisesRegex(AssetError, 'retention'):
                self.prepare(store, require_retention=True)

    def test_explicit_lock_policy_is_verified_not_assumed(self):
        store = MemoryStore(object_lock=True, seed=True)
        until = int(time.time()*1000) + 100000
        mapping = pin_key(store, Index(store), KEY, retain_until=until, mode='governance')
        with self.public(mapping):
            self.assertTrue(self.prepare(store, require_retention=True)['retention_verified'])
        self.assertTrue(self.full.call_args.kwargs['require_retention'])
        row = store.head(KEY, mapping['version_id'])
        store.records[store.records.index(row)] = replace(row, retain_until=None, retention_mode=None)
        with self.public(mapping), self.assertRaisesRegex(AssetError, 'private retention'):
            self.prepare(store)

    def test_changed_public_or_private_identity_cannot_prepare(self):
        store = MemoryStore(versioning=False, seed=True)
        mapping = pin_key(store, Index(store), KEY)
        with self.public(mapping, etag='"different"'), self.assertRaisesRegex(AssetError, 'public delivery'):
            self.prepare(store)
        store.upload(KEY, b'replacement bytes', 'image/svg+xml')
        with self.public(mapping), self.assertRaises(AssetError):
            self.prepare(store)

    def test_incoming_publication_checks_default_to_no_retention_requirement(self):
        from guavajam_core.publication_commit import PublicationError
        env = {'PRESS_PUBLISHER_GPG_FINGERPRINTS':'', 'PRESS_PUBLISHER_SSH_SIGNERS':'publisher ssh-ed25519 fixture'}
        with patch.dict('os.environ', env), patch('guavajam_core.check_publications.incoming', return_value=['a'*40]), \
             patch('guavajam_core.publication_protocol.commit', return_value=(None, 'Press-Publication: 1')), \
             patch('guavajam_core.publication_protocol.declaration', return_value={'kind':'publication'}), \
             patch('guavajam_core.check_publications.verify', return_value={}) as verify:
            check(self.root, 'b'*40)
            self.assertFalse(verify.call_args.kwargs['require_retention'])
            self.assertTrue(verify.call_args.kwargs['online_media'])
            check(self.root, 'b'*40, require_retention=True)
            self.assertTrue(verify.call_args.kwargs['require_retention'])
        with self.assertRaises(PublicationError):
            check(self.root, 'b'*40, online=False, require_retention=True)

    def test_local_image_paths_work_with_either_retention_policy(self):
        from guavajam_core.verify_source_assets import verify_source_assets
        asset = Path('works/test/assets/local image.png')
        (self.root/asset).parent.mkdir(parents=True)
        (self.root/asset).write_bytes(b'fixture')
        ast = {'blocks': [{'t': 'Para', 'c': [
            {'t': 'Image', 'c': [['', [], []], [], ['assets/local%20image.png', '']]}]}]}
        with patch('guavajam_core.verify_source_assets.parse_document', return_value=(ast, [])), \
             patch('guavajam_core.verify_source_assets.local_asset_size', return_value=7) as size:
            for required in [False, True]:
                self.assertEqual(0, verify_source_assets(self.root, paths=[*self.files, asset], require_retention=required))
            self.assertEqual(2, size.call_count)
            size.assert_called_with((self.root/asset).resolve())
