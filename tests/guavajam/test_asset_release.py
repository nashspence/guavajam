"""Private staging -> signed main -> explicit release, with real Git/SSH authority."""
from dataclasses import replace
import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from guavajam.asset_policy import AssetError
from guavajam.asset_release import Releases, release, release_stores, remote_main
from guavajam.asset_storage import S3
from guavajam.asset_versions import pin_key
from guavajam.gateway_index import Index
from guavajam.manage_assets import asset_inventory, candidates, upload
from guavajam.object_store import RELEASE_PREFIX
from guavajam.private_assets import PrivateAssets
from guavajam.asset_policy import AssetPolicy
from guavajam.publication_commit import verify as verify_commit
from guavajam.validate_sources import validate_repository
from guavajam_test_support.discovery_fixture import Publisher
from guavajam_test_support.storage_fixture import MemoryStore, RAW, KEY, VALUES


class ReleaseCredentialTests(unittest.TestCase):
    def test_explicit_release_file_isolated_from_private_read_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def config(name, values):
                path = root / name
                path.write_text(''.join(k+'='+v+'\n' for k,v in values.items()))
                path.chmod(0o600)
                return path
            read = config('read.env', VALUES)
            release_values = {**VALUES, 'S3_ACCESS_KEY_ID': 'release-id', 'S3_SECRET_ACCESS_KEY': 'release-secret'}
            authority = config('release.env', release_values)
            with patch.dict(os.environ, {'S3_ENV': str(read), 'S3_ACCESS_KEY_ID': 'inherited-key'}):
                store, writer = release_stores(root, authority, apply=True)
                self.assertEqual('inspect', store.operation)
                self.assertEqual('release', writer.operation)
                self.assertEqual(VALUES['S3_ACCESS_KEY_ID'], store.config.access_key)
                self.assertEqual('release-id', writer.config.access_key)
                self.assertEqual('inspect', release_stores(root, authority)[1].operation)
                for bad in [VALUES, {**release_values, 'S3_BUCKET': 'another-bucket'},
                            {**release_values, 'S3_VERSIONING': 'on'}]:
                    config('release.env', bad)
                    with self.assertRaises(AssetError):
                        release_stores(root, authority, apply=True)
                authority.unlink()
                with self.assertRaises(AssetError):
                    release_stores(root, authority, apply=True)


class ReleaseRecordTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(seed=True)
        self.pin = pin_key(self.store, Index(self.store), KEY)
        self.releases = Releases(self.store)

    def test_pin_alone_is_private_and_release_is_idempotent(self):
        self.assertIsNone(self.releases.get(KEY))
        self.assertTrue(self.releases.put(KEY, self.pin, 'a'*40, 'https://assets.example.org'))
        count = len(self.store.uploads)
        self.assertFalse(self.releases.put(KEY, self.pin, 'b'*40, 'https://assets.example.org'))
        self.assertEqual(count, len(self.store.uploads))
        self.assertEqual('a'*40, self.releases.get(KEY)['publication'])

    def test_release_does_not_follow_a_changed_private_pin(self):
        self.releases.put(KEY, self.pin, 'a'*40, 'https://assets.example.org')
        changed = {**self.pin, 'version_id': 'a-new-version'}
        with self.assertRaisesRegex(AssetError, 'another object'):
            self.releases.put(KEY, changed, 'b'*40, 'https://assets.example.org')
        self.assertEqual(self.pin, self.releases.get(KEY)['asset'])

    def test_release_metadata_and_retention_are_verified(self):
        store = MemoryStore(seed=True, object_lock=True)
        until = int(time.time()*1000) + 100000
        pin = pin_key(store, Index(store), KEY, retain_until=until, mode='compliance')
        releases = Releases(store)
        releases.put(KEY, pin, 'a'*40, 'https://assets.example.org')
        item = store.head(releases.path(KEY))
        self.assertEqual(until, item.retain_until)
        store.records[store.records.index(item)] = replace(item, retain_until=None)
        with self.assertRaisesRegex(AssetError, 'retention'):
            releases.get(KEY)

    def test_retention_can_only_extend_without_changing_identity_or_first_authority(self):
        store = MemoryStore(seed=True, object_lock=True)
        first = int(time.time()*1000) + 100000
        pin = pin_key(store, Index(store), KEY, retain_until=first, mode='compliance')
        records = Releases(store)
        records.put(KEY, pin, 'a'*40, 'https://assets.example.org')
        renewed = pin_key(store, Index(store), KEY, retain_until=first+100000, mode='compliance')
        self.assertTrue(records.put(KEY, renewed, 'b'*40, 'https://assets.example.org'))
        self.assertEqual('a'*40, records.get(KEY)['publication'])
        self.assertEqual(first+100000, records.get(KEY)['asset']['retain_until'])
        self.assertEqual(first+100000, store.head(records.path(KEY)).retain_until)
        self.assertFalse(records.put(KEY, renewed, 'b'*40, 'https://assets.example.org'))
        with self.assertRaisesRegex(AssetError, 'weaken'):
            records.put(KEY, pin, 'a'*40, 'https://assets.example.org')

    def test_release_origin_is_not_reassignable(self):
        self.releases.put(KEY, self.pin, 'a'*40, 'https://assets.example.org')
        with self.assertRaisesRegex(AssetError, 'origin'):
            self.releases.put(KEY, self.pin, 'a'*40, 'https://different.example.org')

    def test_missing_corrupt_or_extra_release_fields_fail_closed(self):
        self.releases.put(KEY, self.pin, 'a'*40, 'https://assets.example.org')
        name = self.releases.path(KEY)
        for raw in [b'not-json', json.dumps({'schema': 'guavajam.asset.v1', **self.pin}).encode(),
                    json.dumps({'schema': 'guavajam.asset-release.v1', 'publication': 'main', 'asset': self.pin}).encode(),
                    json.dumps({'schema': 'guavajam.asset-release.v1', 'publication': 'a'*40, 'asset': self.pin, 'public': True}).encode()]:
            self.store.upload(name, raw, 'application/json')
            with self.assertRaises(ValueError):
                self.releases.get(KEY)

    def test_staging_capabilities_cannot_write_release_records(self):
        for operation in ['upload', 'pin', 'inspect']:
            with self.subTest(operation=operation), patch('guavajam.asset_storage.response') as request:
                store = S3(VALUES, operation=operation)
                with self.assertRaisesRegex(AssetError, 'not permitted'):
                    store.upload(Releases.path(KEY), b'{}', 'application/json')
                request.assert_not_called()
        store = S3(VALUES, operation='release')
        with self.assertRaisesRegex(AssetError, 'not permitted'):
            store.upload(KEY, RAW, 'image/svg+xml')
        with self.assertRaisesRegex(AssetError, 'not permitted'):
            store.retain(self.store.current(KEY), 'governance', int(time.time()*1000)+100000)

    def test_collection_never_retracts_released_assets_when_branch_roots_disappear(self):
        self.releases.put(KEY, self.pin, 'a'*40, 'https://assets.example.org')
        self.assertEqual({}, candidates(asset_inventory(self.store), set(), time.time()+100*86400))

    def test_private_reads_bind_complete_bytes_to_the_observed_pin(self):
        reader = PrivateAssets(self.store, AssetPolicy('https://assets.example.org'))
        with reader.response('https://assets.example.org/'+KEY, '*/*') as response:
            self.assertEqual(RAW, response.read())
        self.store.upload(KEY, b'replacement', 'image/svg+xml')
        with reader.response('https://assets.example.org/'+KEY, '*/*') as response:
            self.assertEqual(RAW, response.read())  # Fixed version, not latest.
        with self.assertRaises(AssetError):
            reader.observe('https://other.example.org/'+KEY)


@unittest.skipUnless(shutil.which('ssh-keygen') and shutil.which('pandoc'), 'Git/SSH/Pandoc integration fixture')
class ReleaseAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.publisher = Publisher(self.root)
        self.remote = self.root/'remote.git'
        subprocess.run(['git', 'init', '--bare', '-q', str(self.remote)], check=True)
        self.publisher.git('remote', 'add', 'origin', str(self.remote))
        self.publisher.git('push', '-q', 'origin', 'main')
        self.base = self.publisher.git('rev-parse', 'main')
        self.publisher.git('switch', '-c', 'review')
        (self.publisher.repo/'content/asset-policy.json').write_text('{"origin":"https://assets.example.org"}\n')
        self.publisher.source('Field Notes', '![Reviewed diagram](https://assets.example.org/'+KEY+')')
        self.revision = self.publisher.publish()
        self.publisher.git('push', '-q', 'origin', 'review')
        self.store = MemoryStore(seed=True)
        self.mapping = pin_key(self.store, Index(self.store), KEY)
        def verify(repo, revision, **kwargs):
            if kwargs.get('online_media'):
                self.public_calls += 1
                if self.fail_public:
                    raise AssetError('synthetic public delivery outage')
                kwargs['online_media'] = False
            return verify_commit(repo, revision, **kwargs)
        self.public_calls = 0
        self.fail_public = False
        self.enterContext(patch('guavajam.asset_release.verify', side_effect=verify))

    def merge(self, signed=True):
        self.publisher.git('switch', 'main')
        self.publisher.git('merge', '--no-ff', 'review', *(['-S'] if signed else []), '-m', 'Merge reviewed content')
        self.publisher.git('push', '-q', 'origin', 'main')
        self.tip = self.publisher.git('rev-parse', 'main')
        self.branch_revision = self.revision
        self.revision = self.tip

    def run_release(self, **kwargs):
        return release(self.publisher.repo, self.revision, getattr(self, 'tip', self.base),
                       store=self.store, release_store=self.store, **self.publisher.trust, **kwargs)

    def test_signed_branch_is_not_main_authority_and_stale_main_is_rejected(self):
        before = list(self.store.uploads)
        with self.assertRaises(ValueError):
            self.run_release(apply=True)
        self.assertEqual(before, self.store.uploads)
        self.merge()
        with self.assertRaises(ValueError):
            remote_main(self.publisher.repo, self.base)
        self.assertEqual(before, self.store.uploads)

    def test_plan_reads_privately_then_release_and_retry_do_not_duplicate(self):
        self.merge()
        before = list(self.store.uploads)
        plan = self.run_release()
        self.assertFalse(plan['applied'])
        self.assertFalse(plan['public_delivery_verified'])
        self.assertEqual(0, self.public_calls)
        self.assertEqual(before, self.store.uploads)
        first = self.run_release(apply=True)
        self.assertTrue(first['public_delivery_verified'])
        self.assertEqual(1, len([p for p in self.store.uploads if p[0].startswith(RELEASE_PREFIX)]))
        self.assertEqual(self.revision, Releases(self.store).get(KEY)['publication'])
        self.run_release(apply=True)
        self.assertEqual(1, len([p for p in self.store.uploads if p[0].startswith(RELEASE_PREFIX)]))
        # The signed merge is the edition; deleting the draft branch cannot
        # change the released mapping or source citation identity.
        self.publisher.git('push', '-q', 'origin', '--delete', 'review')
        self.publisher.git('branch', '-d', 'review')
        self.run_release(apply=True)

    def test_unreferenced_staged_objects_never_receive_a_release(self):
        raw = RAW.replace(b'r="4"', b'r="3"')
        key = 'sha256/'+hashlib.sha256(raw).hexdigest()+'.svg'
        self.store.upload(key, raw, 'image/svg+xml')
        pin_key(self.store, Index(self.store), key)
        self.merge()
        result = self.run_release(apply=True)
        self.assertEqual(1, result['assets'])
        self.assertIsNone(Releases(self.store).get(key))

    def test_partial_multifile_write_reconciles_without_duplicate_publication(self):
        raw = RAW.replace(b'r="4"', b'r="3"')
        key = 'sha256/'+hashlib.sha256(raw).hexdigest()+'.svg'
        self.store.upload(key, raw, 'image/svg+xml')
        pin_key(self.store, Index(self.store), key)
        self.publisher.source('Field Notes',
                              '![One](https://assets.example.org/'+KEY+')\n\n![Two](https://assets.example.org/'+key+')')
        self.revision = self.publisher.publish()
        self.merge()
        original = self.store.upload
        count = 0
        def fail_second(name, *args, **kwargs):
            nonlocal count
            if name.startswith(RELEASE_PREFIX):
                count += 1
                if count == 2:
                    raise AssetError('synthetic failure before second public write')
            return original(name, *args, **kwargs)
        with patch.object(self.store, 'upload', side_effect=fail_second), self.assertRaises(AssetError):
            self.run_release(apply=True)
        self.assertEqual(1, sum(Releases(self.store).get(k) is not None for k in (KEY, key)))
        before = self.publisher.git('rev-parse', 'main')
        self.assertTrue(self.run_release(apply=True)['public_delivery_verified'])
        self.assertEqual(before, self.publisher.git('rev-parse', 'main'))
        self.assertEqual(2, len([p for p in self.store.uploads if p[0].startswith(RELEASE_PREFIX)]))

    def test_lost_successful_write_response_is_reconciled_on_retry(self):
        self.merge()
        original = self.store.upload
        def lost(name, *args, **kwargs):
            result = original(name, *args, **kwargs)
            if name.startswith(RELEASE_PREFIX):
                raise AssetError('synthetic lost success response')
            return result
        with patch.object(self.store, 'upload', side_effect=lost), self.assertRaises(AssetError):
            self.run_release(apply=True)
        self.assertIsNotNone(Releases(self.store).get(KEY))
        count = len(self.store.uploads)
        self.assertTrue(self.run_release(apply=True)['public_delivery_verified'])
        self.assertEqual(count, len(self.store.uploads))

    def test_delivery_failure_keeps_approved_grants_for_exact_retry(self):
        self.merge(); self.fail_public = True
        with self.assertRaises(AssetError):
            self.run_release(apply=True)
        self.assertIsNotNone(Releases(self.store).get(KEY))
        count = len(self.store.uploads)
        self.fail_public = False
        self.assertTrue(self.run_release(apply=True)['public_delivery_verified'])
        self.assertEqual(count, len(self.store.uploads))

    def test_corrupt_asset_or_required_missing_retention_never_releases(self):
        self.merge()
        with self.assertRaises(AssetError):
            self.run_release(apply=True, require_retention=True)
        self.store.bytes[(KEY, 'version_0')] = b'!' * len(RAW)
        with self.assertRaisesRegex(AssetError, 'SHA-256'):
            self.run_release(apply=True)
        self.assertIsNone(Releases(self.store).get(KEY))
        self.assertEqual(0, self.public_calls)

    def test_unsigned_merge_and_untrusted_signature_cannot_issue(self):
        self.merge(signed=False)
        with self.assertRaises(ValueError):
            release(self.publisher.repo, self.tip, self.tip, store=self.store, release_store=self.store,
                    **self.publisher.trust, apply=True)
        with self.assertRaises(ValueError):
            release(self.publisher.repo, self.revision, self.tip, store=self.store, release_store=self.store,
                    fingerprints=['0'*40], apply=True)
        self.assertIsNone(Releases(self.store).get(KEY))

    def test_unsigned_content_ancestor_blocks_release_of_a_later_signed_edition(self):
        self.publisher.git('switch', 'main')
        (self.publisher.repo / 'content/unreviewed.txt').write_text('An unsigned accepted source change\n')
        self.publisher.git('add', 'content/unreviewed.txt')
        self.publisher.git('commit', '-qm', 'Unsigned content change')
        self.merge()
        before = list(self.store.uploads)
        with self.assertRaises(ValueError):
            self.run_release(apply=True)
        self.assertEqual(before, self.store.uploads)
        self.assertEqual(0, self.public_calls)

    def test_signed_second_parent_draft_is_not_an_accepted_edition(self):
        self.merge()
        with self.assertRaises(ValueError):
            release(self.publisher.repo, self.branch_revision, self.tip, store=self.store,
                    release_store=self.store, **self.publisher.trust, apply=True)
        self.assertIsNone(Releases(self.store).get(KEY))

    def test_main_movement_before_first_write_stops_release(self):
        self.merge()
        actual = remote_main
        calls = 0
        def concurrent(repo, expected, remote):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.publisher.git('commit', '--allow-empty', '-qm', 'Concurrent main advance')
                self.publisher.git('push', '-q', 'origin', 'main')
            return actual(repo, expected, remote)
        with patch('guavajam.asset_release.remote_main', side_effect=concurrent), self.assertRaises(ValueError):
            self.run_release(apply=True)
        self.assertIsNone(Releases(self.store).get(KEY))


if __name__ == '__main__':
    unittest.main()
