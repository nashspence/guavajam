"""Immutable compressed indexes preserve bytes and publish bounded descriptors."""
import gzip
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from guavajam.search_artifact import download_attributes, emit_download


class SearchArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / 'work.sqlite'
        with sqlite3.connect(self.database) as db:
            db.execute('CREATE TABLE example (id INTEGER PRIMARY KEY, body TEXT)')
            db.executemany('INSERT INTO example(body) VALUES (?)',
                           [('Coastal ecology, field notes and source passages. ' * 20,)] * 50)

    def test_deterministic_hash_named_gzip_roundtrips_and_reuses_the_same_asset(self):
        raw = self.database.read_bytes()
        first = emit_download(self.database)
        target = self.root / first['url']
        encoded = target.read_bytes()
        self.assertEqual(raw, gzip.decompress(encoded))
        self.assertEqual('work.' + hashlib.sha256(encoded).hexdigest() + '.sqlite.gz', first['url'])
        self.assertEqual(len(raw), first['size'])
        self.assertEqual(len(encoded), first['transfer_size'])
        self.assertLess(len(encoded), len(raw))
        self.assertEqual(b'\0\0\0\0', encoded[4:8])
        self.assertEqual(0, encoded[3] & 8, 'gzip must not contain a source filename')
        os.utime(self.database, (100, 100))
        self.assertEqual(first, emit_download(self.database))
        self.assertEqual([target], list(self.root.glob('*.sqlite.gz')))
        self.assertEqual(raw, self.database.read_bytes(), 'the interoperable SQLite file remains intact')
        other = self.root / 'other'
        other.mkdir()
        (other / 'work.sqlite').write_bytes(raw)
        self.assertEqual(first, emit_download(other / 'work.sqlite'))

    def test_new_content_has_a_new_address_and_old_bytes_are_never_replaced(self):
        before = emit_download(self.database)
        old = (self.root / before['url']).read_bytes()
        with sqlite3.connect(self.database) as db:
            db.execute('INSERT INTO example(body) VALUES (?)', ('A new edition.',))
        after = emit_download(self.database)
        self.assertNotEqual(before['url'], after['url'])
        self.assertEqual(old, (self.root / before['url']).read_bytes())
        self.assertEqual(self.database.read_bytes(), gzip.decompress((self.root / after['url']).read_bytes()))
        attributes = download_attributes(after, prefix='../')
        self.assertIn('data-database="../' + after['url'] + '"', attributes)
        self.assertIn('data-database-size="' + str(after['size']) + '"', attributes)
        self.assertIn('data-database-encoding="gzip"', attributes)

    def test_bounds_symlinks_and_corrupt_existing_assets_fail_without_temporary_output(self):
        with patch('guavajam.search_artifact.MAX_DATABASE_BYTES', 1), self.assertRaises(ValueError):
            emit_download(self.database)
        self.assertEqual([], list(self.root.glob('*.gz')))
        description = emit_download(self.database)
        target = self.root / description['url']
        target.write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            emit_download(self.database)
        self.assertEqual(b'corrupt', target.read_bytes())
        self.assertEqual([], list(self.root.glob('.search-download-*')))
        alias = self.root / 'index.sqlite'
        alias.symlink_to(self.database)
        with self.assertRaises(ValueError):
            emit_download(alias)


if __name__ == '__main__':
    unittest.main(verbosity=2)
