"""Physical chunk transport preserves the exact logical database and immutable identity."""
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from a_guavajam_static_website_generator.search_artifact import emit_download, MAX_DATABASE_BYTES
from a_guavajam_static_website_generator.search_chunks import emit_chunks, CHUNK_BYTES


class SearchChunkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / 'history.sqlite'
        with sqlite3.connect(self.database) as db:
            db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)')
            db.executemany('INSERT INTO metadata VALUES (?,?)', [
                ('generation', 'a' * 64), ('revision', 'b' * 40), ('scope', 'history')])
            db.execute('CREATE TABLE document_facets(document_id INTEGER,key TEXT,value TEXT)')
            db.execute('CREATE TABLE passages(id INTEGER,document_id INTEGER)')
            db.execute('CREATE TABLE payload(id INTEGER PRIMARY KEY,body BLOB)')
            db.executemany('INSERT INTO payload(body) VALUES (zeroblob(?))', [(CHUNK_BYTES,)] * 3)

    def test_ordered_immutable_chunks_reconstruct_exact_sqlite_and_metadata(self):
        first = emit_chunks(self.database)
        manifest_path = self.root / first['url']
        raw_manifest = manifest_path.read_bytes()
        self.assertEqual(first['transfer_sha256'], hashlib.sha256(raw_manifest).hexdigest())
        manifest = json.loads(raw_manifest)
        self.assertEqual('guavajam.search-chunks.v1',manifest['schema'])
        self.assertEqual('a' * 64,manifest['generation'])
        self.assertEqual('b' * 40,manifest['source'])
        self.assertEqual('history',manifest['scope'])
        decoded = bytearray()
        for number, chunk in enumerate(manifest['chunks']):
            self.assertEqual(number * CHUNK_BYTES,chunk['offset'])
            payload = (self.root / chunk['url']).read_bytes()
            self.assertEqual(len(payload),chunk['transfer_size'])
            self.assertEqual(chunk['transfer_sha256'],hashlib.sha256(payload).hexdigest())
            data = gzip.decompress(payload)
            self.assertEqual(chunk['size'],len(data))
            self.assertEqual(chunk['sha256'],hashlib.sha256(data).hexdigest())
            decoded.extend(data)
        self.assertEqual(self.database.read_bytes(),decoded)
        self.assertEqual(first,emit_chunks(self.database))
        self.assertEqual(first['sha256'],hashlib.sha256(decoded).hexdigest())
        self.assertEqual(256 * 1024 * 1024,MAX_DATABASE_BYTES)
        with patch('a_guavajam_static_website_generator.search_artifact.MAX_DATABASE_BYTES',CHUNK_BYTES):
            combined = emit_download(self.database)
            self.assertIn('facets', combined)
            self.assertEqual(first, {key: value for key, value in combined.items() if key != 'facets'})
        before = (self.root / manifest['chunks'][0]['url'])
        before.write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            emit_chunks(self.database)
        self.assertEqual(b'corrupt',before.read_bytes())

    def test_logical_manifest_and_page_bounds(self):
        with patch('a_guavajam_static_website_generator.search_chunks.MAX_LOGICAL_BYTES',1):
            with self.assertRaisesRegex(ValueError,'8-GiB'):
                emit_chunks(self.database)
        with patch('a_guavajam_static_website_generator.search_chunks.MAX_MANIFEST_BYTES',1):
            with self.assertRaisesRegex(ValueError,'4-MiB'):
                emit_chunks(self.database)
        with self.database.open('ab') as stream:
            stream.write(b'x')
        with self.assertRaisesRegex(ValueError,'alignment'):
            emit_chunks(self.database)


if __name__ == '__main__':
    unittest.main(verbosity=2)
