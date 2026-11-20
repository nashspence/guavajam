"""Compact indexes retain the full external-content FTS search contract."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from guavajam.search_index import create_index


FIELDS = 'title,authors,abstract,labels,body,git'
QUERIES = ('"ecol"*', '"cafe"*', '"李"*', '"observ"* AND "estuar"*',
           '"ecol"* AND "bathymetric"*', '"palaeoriver"*', '"current"*',
           '"needle"*', '"field"*', '"signed"*', '"zzabsent"*')


def source(root, name, number):
    """Generated-view fixture; signed source verification has separate tests."""
    root.mkdir(parents=True)
    revisions = [format(number * 10 + edition, '040x') for edition in range(3)]
    catalog = {'tip': revisions[-1], 'editions': []}
    expected = {'search_fts': [], 'passage_fts': []}
    repository = 'https://example.org/' + name + '.git'
    author = 'Example 李 Author'
    for edition, revision in enumerate(revisions):
        title = name + (' Café historical ecology' if edition == 0 else ' Current field ecology')
        abstract = 'Estuary observation with needle evidence' if edition == 1 else 'Ecology observation'
        keyword = 'historical' if edition == 0 else 'current'
        facts = {'title': title, 'author': author, 'language': 'en', 'rights': 'CC-BY-4.0',
                 'abstract': abstract, 'keywords': ['fieldwork', keyword],
                 'subjects': [{'subject': 'Café studies', 'subject_scheme': 'Local'}]}
        labels = 'fieldwork ' + keyword + ' Café studies Local'
        published = '2026-11-0' + str(3 - edition)
        message = 'Signed ' + title
        catalog['editions'].append({'revision': revision, 'tree': 'f' * 40,
                                   'published': published, 'message': message})
        read = root / 'editions' / revision / 'read'
        read.mkdir(parents=True)
        canonical = {'revision': revision}
        view = {'preview': False, 'canonical': canonical, 'facts': facts, 'documents': {}}
        navigation = {'schema': 'guavajam.navigation.v1', 'preview': False,
                      'canonical': canonical, 'documents': {}}
        for index, path in enumerate(('content/manuscript.md', 'content/supporting/notes.md')):
            document_title = title if index == 0 else 'Supporting evidence'
            html = 'index.html' if index == 0 else 'supporting/notes.html'
            passage = ('Palaeoriver historical evidence.' if edition == 0 else 'Current bathymetric evidence.')
            passage += ' Estuary observation ecology.' * (number + edition + index)
            anchors = [
                {'anchor': 'opening', 'title': 'Ecology methods', 'kind': 'section', 'level': 2,
                 'position': 0, 'parent': None, 'scope': 'document', 'text': passage},
                {'anchor': 'detail', 'title': 'Detailed measurement', 'kind': 'passage', 'level': 0,
                 'position': 1, 'parent': 'opening', 'scope': 'document',
                 'text': 'Café 李 measurement ' + 'needle ' * (index + edition + 1)},
            ]
            body = document_title + '\n' + '\n'.join(anchor['text'] for anchor in anchors)
            detail = {'title': document_title, 'view': html, 'language': 'en',
                      'source_sha256': hashlib.sha256(body.encode()).hexdigest(),
                      'anchors': [anchor['anchor'] for anchor in anchors]}
            view['documents'][path] = detail
            navigation['documents'][path] = {**detail, 'text': body, 'anchors': anchors}
            (read / html).parent.mkdir(exist_ok=True)
            (read / html).write_text('<article>Rendered output is not the search source.</article>')
            search_title = title if index == 0 else title + ' — Supporting evidence'
            # This fixture's explicit expected fields are supplied to the old
            # external-content layout independently of the production writer.
            provenance = ' '.join((repository, revision, 'f' * 40, published, message, path,
                                   'CC-BY-4.0', '', '{}', '[{"name": "Example 李 Author"}]'))
            url = 'editions/' + revision + '/read/' + html
            expected['search_fts'].append((url, search_title, author, abstract, labels, body, provenance))
            for anchor in anchors:
                expected['passage_fts'].append((url + '#' + anchor['anchor'],
                    search_title + ' — ' + anchor['title'], author, abstract, labels, anchor['text'],
                    provenance + ' ' + anchor['anchor']))
        raw = json.dumps(navigation, ensure_ascii=False).encode()
        (read / 'NAV.json').write_bytes(raw)
        view['files'] = {'NAV.json': hashlib.sha256(raw).hexdigest()}
        (read / 'VIEW.json').write_text(json.dumps(view, ensure_ascii=False))
    (root / 'CATALOG.json').write_text(json.dumps(catalog))
    (root / 'SITE.json').write_text(json.dumps({'tip': revisions[-1], 'title': title}))
    return {'repository': repository, 'path': 'sources/' + name, 'tip': revisions[-1]}, expected


class SearchStorageTests(unittest.TestCase):
    def assert_ranked_results(self, actual, table, rows):
        records = 'search_records' if table == 'search_fts' else 'passage_records'
        with closing(sqlite3.connect(':memory:')) as reference:
            reference.executescript('CREATE TABLE records(id INTEGER PRIMARY KEY,url TEXT,' +
                ','.join(field + ' TEXT' for field in FIELDS.split(',')) + ');'
                'CREATE VIRTUAL TABLE reference_fts USING fts5(' + FIELDS +
                ",content='records',content_rowid='id',tokenize='unicode61 remove_diacritics 2');")
            reference.executemany('INSERT INTO records VALUES (?,?,?,?,?,?,?,?)',
                                  [(number, *row) for number, row in enumerate(rows, 1)])
            reference.execute("INSERT INTO reference_fts(reference_fts) VALUES ('rebuild')")
            for term in QUERIES:
                with self.subTest(table=table, term=term):
                    def query(db, fts, mapping):
                        return db.execute('SELECT r.id,r.url,bm25(' + fts + ',4.,2.,1.,2.,1.,.5) '
                            'FROM ' + fts + ' JOIN ' + mapping + ' r ON r.id=' + fts + '.rowid '
                            'WHERE ' + fts + ' MATCH ? ORDER BY 3,r.id', (term,)).fetchall()
                    # Equality checks every hit, its target, rank and exact score.
                    self.assertEqual(query(reference, 'reference_fts', 'records'),
                                     query(actual, table, records))
            reference.execute("CREATE VIRTUAL TABLE tokens USING fts5vocab(reference_fts,'instance')")
            actual.execute('CREATE VIRTUAL TABLE temp.tokens_' + table +
                           ' USING fts5vocab(main,' + table + ",'instance')")
            token_sql = 'SELECT term,doc,col,offset FROM {} ORDER BY doc,col,offset,term'
            self.assertEqual(reference.execute(token_sql.format('tokens')).fetchall(),
                             actual.execute(token_sql.format('temp.tokens_' + table)).fetchall())

    def test_collection_and_work_preserve_full_weighted_ranking_without_plaintext_copies(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entries, all_rows = [], []
            for number, name in enumerate(('alpha', 'beta'), 1):
                entry, rows = source(root / 'sources' / name, name, number)
                entries.append(entry)
                all_rows.append(rows)
            aggregate = create_index(root, entries)
            work = create_index(root / entries[0]['path'], [{**entries[0], 'path': ''}],
                                filename='work.sqlite', scope='work')
            for scope, database in (('collection', aggregate), ('work', work)):
                with self.subTest(scope=scope), closing(sqlite3.connect(database)) as db:
                    self.assertEqual(['id', 'work_id', 'edition_id', 'document_id', 'url'],
                                     [row[1] for row in db.execute('PRAGMA table_info(search_records)')])
                    if scope == 'collection':
                        self.assertFalse(db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'passage%'").fetchall())
                        document_rows = [(entry['path'] + '/' + row[0], *row[1:])
                                         for entry, rows in zip(entries, all_rows) for row in rows['search_fts']]
                    else:
                        self.assertNotIn('text', [row[1] for row in db.execute('PRAGMA table_info(passages)')])
                        self.assertEqual(['id', 'work_id', 'edition_id', 'document_id', 'url'],
                                         [row[1] for row in db.execute('PRAGMA table_info(passage_records)')])
                        document_rows = all_rows[0]['search_fts']
                        self.assert_ranked_results(db, 'passage_fts', all_rows[0]['passage_fts'])
                    self.assert_ranked_results(db, 'search_fts', document_rows)
                    self.assertEqual([(None,) * 6], db.execute('SELECT ' + FIELDS + ' FROM search_fts LIMIT 1').fetchall())


if __name__ == '__main__':
    unittest.main(verbosity=2)
