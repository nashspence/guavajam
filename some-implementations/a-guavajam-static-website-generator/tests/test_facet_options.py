"""Static facet discovery stays bounded and agrees with exact scope membership."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from a_guavajam_static_website_generator.facet_options import emit_options, MAX_NODE_BYTES, PAGE_SIZE


class FacetOptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.database = self.root / 'documents.sqlite'
        self.db = sqlite3.connect(self.database)
        self.addCleanup(self.db.close)
        self.db.executescript("""
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO metadata VALUES ('schema','guavajam.search.v1'),('scope','documents'),
              ('generation','test-generation'),('revision','exact-edition');
            CREATE TABLE document_facets (document_id INTEGER,key TEXT,value TEXT);
            CREATE TABLE passages (id INTEGER PRIMARY KEY,document_id INTEGER);
        """)

    def read(self, descriptor):
        data = (self.root / descriptor['url']).read_bytes()
        self.assertEqual(len(data), descriptor['size'])
        self.assertLessEqual(len(data), MAX_NODE_BYTES)
        self.assertEqual(hashlib.sha256(data).hexdigest(), descriptor['sha256'])
        record = json.loads(data)
        self.assertEqual('test-generation', record['generation'])
        self.assertEqual('exact-edition', record['revision'])
        return record

    def items(self, descriptor):
        node = self.read(descriptor)
        if 'items' in node:
            self.assertLessEqual(len(node['items']), PAGE_SIZE)
            yield from node['items']
        else:
            self.assertLessEqual(len(node['children']), PAGE_SIZE)
            for child in node['children']:
                yield from self.items(child)

    def test_high_cardinality_keys_and_values_have_bounded_pages_and_exact_totals(self):
        rows = [(1, 'Applies to', value) for value in ['01', '001', 'A', 'a', 'café', 'CAFÉ', '', '🍋']]
        rows += [(2, 'Applies to', '001')]
        rows += [(1, 'Applies to', 'version-' + str(n).zfill(5)) for n in range(1400)]
        rows += [(1, 'Field-' + str(n).zfill(4), 'same') for n in range(300)]
        self.db.executemany('INSERT INTO document_facets VALUES (?,?,?)', rows)
        self.db.commit()
        descriptor = emit_options(self.database)
        root = self.read(descriptor)
        keys = list(self.items(root['metadata']))
        self.assertEqual(301, len(keys))
        applies = next(item for item in keys if item['key'] == 'Applies to')
        self.assertEqual(2, applies['total'])
        values = list(self.items(applies['values']))
        self.assertEqual(1408, len(values))
        expected = dict(self.db.execute(
            "SELECT value,count(*) FROM document_facets WHERE key='Applies to' GROUP BY value"))
        self.assertEqual(expected, {item['key']: item['total'] for item in values})
        self.assertEqual(sorted(values, key=lambda item: (item['name'].lower(), item['name'])), values)
        self.assertLess(descriptor['size'], 1024)

    def test_valid_large_literal_values_keep_exact_identity_through_multilevel_pages(self):
        from guavajam.supporting_metadata import split_frontmatter
        values = [format(number, '04d') + 'İ' * 29000 for number in range(40)]
        for number, value in enumerate(values):
            source = '---\n' + 'title: Note\nmetadata:\n  Exact: "' + value + '"\n---\n\nEvidence.\n'
            _, fields, _ = split_frontmatter(source)
            self.assertEqual([('Exact', value)], fields)
            self.db.execute('INSERT INTO document_facets VALUES (?,?,?)', (number, 'Exact', value))
        self.db.commit()
        root = self.read(emit_options(self.database))
        key = next(self.items(root['metadata']))
        actual = list(self.items(key['values']))
        self.assertEqual(values, [item['key'] for item in actual])
        self.assertTrue(all(item['order'] == item['name'].lower() for item in actual))
        self.assertTrue(all(item['total'] == 1 for item in actual))

    def test_passage_totals_inherit_document_occurrences_without_collapsing_editions(self):
        self.db.executemany('INSERT INTO document_facets VALUES (?,?,?)', [
            (1, 'Status', 'ready'), (2, 'Status', 'ready'), (3, 'Status', 'draft')])
        self.db.executemany('INSERT INTO passages VALUES (?,?)', [(1, 1), (2, 1), (3, 2)])
        self.db.execute("UPDATE metadata SET value='history' WHERE key='scope'")
        self.db.commit()
        root = self.read(emit_options(self.database))
        keys = list(self.items(root['metadata']))
        self.assertEqual([('Status', 3)], [(item['key'], item['total']) for item in keys])
        values = list(self.items(keys[0]['values']))
        self.assertEqual([('ready', 3)], [(item['key'], item['total']) for item in values])

    def test_collection_disambiguates_structured_subjects_and_excludes_stale_memberships(self):
        self.db.execute("UPDATE metadata SET value='collection' WHERE key='scope'")
        self.db.executescript("""
            CREATE TABLE keywords (work_id INTEGER,name TEXT);
            INSERT INTO keywords VALUES (1,'current'),(2,'current'),(2,'Other');
            CREATE TABLE subjects (work_id INTEGER,key TEXT,name TEXT,label TEXT);
            INSERT INTO subjects VALUES
              (1,'a','Shared','Shared · Authority A'),(2,'b','Shared','Shared · Authority B'),
              (1,'c','Unique','Unique · Context');
        """)
        self.db.commit()
        root = self.read(emit_options(self.database))
        self.assertEqual({'Other': 1, 'current': 2},
                         {item['key']: item['total'] for item in self.items(root['keyword'])})
        self.assertEqual({'a': 'Shared · Authority A', 'b': 'Shared · Authority B', 'c': 'Unique'},
                         {item['key']: item['name'] for item in self.items(root['subject'])})

    def test_derivative_output_is_reproducible_and_immutable_nodes_reject_corruption(self):
        self.db.execute("INSERT INTO document_facets VALUES (1,'Literal','001')")
        self.db.commit()
        first = emit_options(self.database)
        before = {p.name: p.read_bytes() for p in self.root.glob('*.json')}
        self.assertEqual(first, emit_options(self.database))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.glob('*.json')})
        (self.root / first['url']).write_text('{}')
        from guavajam.catalog import CatalogError
        with self.assertRaisesRegex(CatalogError, 'immutable node differs'):
            emit_options(self.database)


if __name__ == '__main__':
    unittest.main()
