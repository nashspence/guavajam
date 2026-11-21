"""Owned cleanup must be safe even when a live qualification is interrupted."""
from contextlib import redirect_stderr
from io import StringIO
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from guavajam.asset_policy import AssetError
from guavajam_test_support.storage_fixture import MemoryStore
from guavajam_test_support.provider_qualification import paths, claim, cleanup, main, fixtures, SCHEMA

KEYS = ['sha256/' + c * 64 + '.' + ext for c, ext in zip('abcd', ['png', 'mp3', 'mp4', 'vtt'])]


class ProviderQualificationTests(unittest.TestCase):
    def test_claim_refuses_existing_versions_before_journaling(self):
        store = MemoryStore(); store.upload(KEYS[0], b'old', 'image/png')
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / 'owned.json'
            with self.assertRaises(AssetError): claim(store, KEYS, journal, 'binding')
            self.assertFalse(journal.exists()); self.assertFalse(store.deleted)

    def test_cleanup_filters_prefix_neighbors_and_repeats_safely(self):
        store = MemoryStore()
        with tempfile.TemporaryDirectory() as directory:
            value = claim(store, KEYS, Path(directory) / 'owned.json', 'binding')
            for key in paths(KEYS): store.upload(key, b'test', 'application/json')
            neighbor = KEYS[0] + '.unowned'; store.upload(neighbor, b'keep', 'text/plain')
            self.assertEqual(cleanup(store, value, 'binding'), 16)
            self.assertEqual([row.key for row in store.records], [neighbor])
            self.assertEqual(cleanup(store, value, 'binding'), 0)
            with self.assertRaises(AssetError): cleanup(store, value, 'other-provider')

    def test_cleanup_waits_for_lock_and_refuses_long_or_held_retention(self):
        for held, until, allowed in [(False, 102000, True), (False, 999999999, False), (True, None, False)]:
            with self.subTest(held=held, until=until):
                store = MemoryStore(); row = store.upload(KEYS[0], b'x', 'image/png')
                store.records[0] = replace(row, legal_hold=held, retain_until=until)
                value = dict(schema=SCHEMA, claimed=True, binding='b', keys=KEYS)
                now = [100.0]
                def sleep(seconds): now[0] += seconds
                if allowed:
                    self.assertEqual(cleanup(store, value, 'b', clock=lambda: now[0], sleep=sleep), 1)
                    self.assertGreaterEqual(now[0], 102)
                else:
                    with self.assertRaises(AssetError): cleanup(store, value, 'b', clock=lambda: now[0], sleep=sleep)
                    self.assertFalse(store.deleted)

    def test_failed_delete_keeps_manifest_for_an_exact_retry(self):
        store = MemoryStore()
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / 'owned.json'; value = claim(store, KEYS, journal, 'b')
            for key in KEYS: store.upload(key, b'x', 'application/octet-stream')
            actual = store.delete
            calls = [0]
            def interrupted(row):
                calls[0] += 1
                if calls[0] == 2: raise OSError('synthetic interruption')
                actual(row)
            with patch.object(store, 'delete', interrupted):
                with self.assertRaises(OSError): cleanup(store, value, 'b')
            self.assertEqual(cleanup(store, json.loads(journal.read_text()), 'b'), 3)

    def test_fixtures_are_unique_even_when_runs_are_concurrent(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            first, second = fixtures(Path(a)), fixtures(Path(b))
            self.assertTrue(set(paths(first)).isdisjoint(paths(second)))

    def test_opt_in_required_before_loading_credentials(self):
        with patch('guavajam_test_support.provider_qualification.credentials') as creds:
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                main(['--roles-dir', '/absent', '--origin', 'https://assets.example', '--run-dir', '/absent'])
            creds.assert_not_called()


if __name__ == '__main__': unittest.main()
