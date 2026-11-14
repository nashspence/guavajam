"""Content pins work with and without optional storage guarantees."""
from dataclasses import replace
import hashlib
import time
import unittest
from unittest.mock import patch

from guavajam_core.asset_policy import AssetError
from guavajam_core.asset_versions import pin_key
from guavajam_core.gateway_index import Index, validate
from guavajam_core.manage_assets import candidates
from guavajam_core.object_store import StoredObject
from guavajam_test_support.storage_fixture import MemoryStore, RAW, KEY


class PinTests(unittest.TestCase):
    def test_three_storage_profiles_pin_and_read_back_original_bytes(self):
        for versioning, lock in [(False, False), (True, False), (True, True)]:
            with self.subTest(versioning=versioning, lock=lock):
                store = MemoryStore(versioning=versioning, object_lock=lock, seed=True)
                value = pin_key(store, Index(store), KEY)
                self.assertEqual('version_0' if versioning else None, value['version_id'])
                self.assertEqual(store.current(KEY).etag, value['etag'])
                self.assertIsNone(value['retain_until'])
                self.assertIsNone(value['retention_mode'])
                self.assertEqual(value, Index(store).get(KEY))
                self.assertEqual([], store.retentions)
                self.assertEqual(value, pin_key(store, Index(store), KEY))

    def test_explicit_retention_protects_asset_and_sidecar(self):
        store = MemoryStore(object_lock=True, seed=True)
        until = int(time.time() * 1000) + 100000
        value = pin_key(store, Index(store), KEY, retain_until=until, mode='governance')
        self.assertEqual(until, value['retain_until'])
        self.assertTrue(all(r.retain_until == until for r in store.records))
        self.assertEqual({}, candidates(store.inventory(), set(), time.time()))

    def test_requesting_unavailable_or_incomplete_retention_never_silently_pins(self):
        for versioning in [False, True]:
            store = MemoryStore(versioning=versioning, seed=True)
            for args in [dict(retain_until=int(time.time()*1000)+100000, mode='compliance'),
                         dict(mode='governance'), dict(retain_until=123)]:
                with self.subTest(versioning=versioning, args=args), self.assertRaises(AssetError):
                    pin_key(store, Index(store), KEY, **args)
                self.assertIsNone(Index(store).get(KEY))
                self.assertEqual(1, len(store.uploads))

    def test_fixed_version_stays_pinned_after_a_replacement(self):
        store = MemoryStore(seed=True)
        first = pin_key(store, Index(store), KEY)
        replacement = store.upload(KEY, b'wrong bytes', 'image/svg+xml')
        with self.assertRaisesRegex(AssetError, 'different pinned'):
            pin_key(store, Index(store), KEY, version=replacement.version_id)
        self.assertEqual(first, pin_key(store, Index(store), KEY))

    def test_unversioned_replacement_fails_instead_of_repinning(self):
        store = MemoryStore(versioning=False, seed=True)
        first = pin_key(store, Index(store), KEY)
        store.upload(KEY, b'wrong bytes', 'image/svg+xml')
        with self.assertRaises(AssetError):
            pin_key(store, Index(store), KEY)
        self.assertEqual(first, Index(store).get(KEY))

    def test_false_hash_and_retention_readback_cannot_be_pinned(self):
        store = MemoryStore(object_lock=True, seed=True)
        store.bytes[(KEY, 'version_0')] = b'!' * len(RAW)
        with self.assertRaisesRegex(AssetError, 'SHA-256'):
            pin_key(store, Index(store), KEY)
        self.assertIsNone(Index(store).get(KEY))
        store.bytes[(KEY, 'version_0')] = RAW
        with patch.object(store, 'retain', return_value=store.current(KEY)), self.assertRaisesRegex(AssetError, 'retention readback'):
            pin_key(store, Index(store), KEY, retain_until=int(time.time()*1000)+100000, mode='governance')
        self.assertIsNone(Index(store).get(KEY))

    def test_changed_etag_after_hash_cannot_publish_a_false_pin(self):
        store = MemoryStore(versioning=False, seed=True)
        original = store.read
        from contextlib import contextmanager
        @contextmanager
        def changed(stored, **kwargs):
            with original(stored, **kwargs) as response:
                response.headers['etag'] = '"replacement"'
                yield response
        with patch.object(store, 'read', changed), self.assertRaises(AssetError):
            pin_key(store, Index(store), KEY)
        self.assertIsNone(Index(store).get(KEY))

    def test_retention_commitments_cannot_be_shortened_or_disabled(self):
        store = MemoryStore(object_lock=True, seed=True)
        until = int(time.time()*1000) + 100000
        value = pin_key(store, Index(store), KEY, retain_until=until, mode='compliance')
        row = store.head(KEY, value['version_id'])
        store.records[store.records.index(row)] = replace(row, retain_until=until-1)
        with self.assertRaisesRegex(AssetError, 'pinned commitment'):
            pin_key(store, Index(store), KEY)
        with self.assertRaises(AssetError):
            Index(store).put(KEY, {**value, 'retain_until': None, 'retention_mode': None})

    def test_a_retention_commitment_also_requires_its_sidecar_to_remain_protected(self):
        store = MemoryStore(object_lock=True, seed=True)
        until = int(time.time()*1000) + 100000
        pin_key(store, Index(store), KEY, retain_until=until, mode='governance')
        sidecar = store.current(Index.path(KEY))
        store.records[store.records.index(sidecar)] = replace(sidecar, retain_until=None, retention_mode=None)
        with self.assertRaisesRegex(AssetError, 'mapping retention'):
            Index(store).get(KEY)

    def test_unreferenced_assets_collect_sidecars_in_both_version_modes(self):
        for versioning in [False, True]:
            store = MemoryStore(versioning=versioning, seed=True)
            pin_key(store, Index(store), KEY)
            planned = candidates(store.inventory(), set(), time.time()+40*86400)
            self.assertEqual({KEY}, set(planned))
            self.assertEqual(2, len(planned[KEY]))
            self.assertEqual({}, candidates(store.inventory(), {hashlib.sha256(RAW).hexdigest()}, time.time()+40*86400))

    def test_mapping_requires_a_strong_etag_and_consistent_optional_fields(self):
        store = MemoryStore(versioning=False, seed=True)
        good = pin_key(store, Index(store), KEY)
        for changed in [dict(etag=None), dict(etag='W/"weak"'), dict(etag='"bad\r\n"'),
                        dict(version_id='x\n'), dict(retain_until=1), dict(retention_mode='governance'),
                        dict(etag='*'), dict(version_id='a'*1025)]:
            with self.subTest(changed=changed), self.assertRaises(AssetError):
                validate({**good, **changed}, KEY)
        for version in [None, 'null', 'opaque.+/=%with-punctuation']:
            validate({**good, 'version_id': version}, KEY)


class LifecycleTests(unittest.TestCase):
    def test_plain_and_versioned_upload_reuse_public_readback_and_cleanup(self):
        import contextlib
        import io
        from pathlib import Path
        import tempfile
        from guavajam_core.manage_assets import upload, collect
        for versioning in [False, True]:
            with self.subTest(versioning=versioning), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root/'asset-policy.json').write_text('{"origin":"https://assets.example.org"}')
                image = root/'image.svg'
                image.write_bytes(RAW)
                store = MemoryStore(versioning=versioning)
                index = Index(store)
                public = lambda *args: (RAW, 'image/svg+xml')
                state, url = upload(root, image, store, index=index, fetcher=public)
                self.assertEqual('Uploaded', state)
                self.assertEqual('https://assets.example.org/'+KEY, url)
                pin = index.get(KEY)
                self.assertIsNone(pin['retain_until'])
                self.assertEqual('Already stored', upload(root, image, store, index=index, fetcher=public)[0])
                self.assertEqual(pin, index.get(KEY))
                with contextlib.redirect_stdout(io.StringIO()):
                    deleted = collect(root, 'fixture/repo', store, apply=True, now=time.time()+40*86400, scan=lambda *args:set())
                self.assertGreaterEqual(deleted, 3)
                self.assertEqual([], store.records)
