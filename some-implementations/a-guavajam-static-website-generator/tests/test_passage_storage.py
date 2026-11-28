"""Normalized storage preserves occurrence fields, posting positions and exact citations."""
from contextlib import closing
import json
import sqlite3
import unittest

import test_scoped_search as fixtures
from a_guavajam_static_website_generator.navigation_manifest import load_navigation
from a_guavajam_static_website_generator.passage_storage import windows, WINDOW_CHARS
from a_guavajam_static_website_generator.citation import citation_presentation
from guavajam.bibliography import ordered_authors, author_text

FIELDS = 'title,authors,abstract,labels,body,git'


class PassageStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ScopedSearchTests.setUpClass.__func__(cls)

    connect = fixtures.ScopedSearchTests.connect

    def test_shared_text_and_shapes_retain_every_source_occurrence(self):
        with self.connect('history', appended=True) as db:
            occurrences = db.execute('SELECT count(*) FROM passage_occurrences').fetchone()[0]
            self.assertEqual(occurrences, db.execute('SELECT count(*) FROM passages').fetchone()[0])
            self.assertLess(db.execute('SELECT count(*) FROM passage_shapes').fetchone()[0], occurrences)
            self.assertLess(db.execute('SELECT count(*) FROM source_contents').fetchone()[0],
                            db.execute('SELECT count(*) FROM document_contents').fetchone()[0])
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_schema WHERE name='passage_fts_content'").fetchone())
            self.assertEqual(0, db.execute(
                'SELECT count(*) FROM (SELECT value,count(*) n FROM source_texts GROUP BY value HAVING n>1)').fetchone()[0])
            repeated = db.execute('SELECT d.source_sha256,count(DISTINCT e.revision) FROM documents d '
                'JOIN editions e ON e.id=d.edition_id GROUP BY d.source_sha256 HAVING count(DISTINCT e.revision)>1').fetchall()
            self.assertTrue(repeated)
            for digest, count in repeated:
                citations = db.execute('SELECT p.citation_json FROM passages p JOIN documents d ON d.id=p.document_id '
                    'WHERE d.source_sha256=?', (digest,)).fetchall()
                self.assertEqual(count, len({json.loads(row[0])['source'][1][1] for row in citations}))

    def test_external_content_fields_tokens_and_scores_equal_independent_occurrence_rows(self):
        with self.connect('history', appended=True) as db, closing(sqlite3.connect(':memory:')) as oracle:
            oracle.execute('CREATE VIRTUAL TABLE expected USING fts5(' + FIELDS +
                           ",tokenize='unicode61 remove_diacritics 2',detail=full,columnsize=1)")
            for identifier, edition_id, document_id, anchor, citation_json in db.execute(
                    'SELECT id,edition_id,document_id,anchor,citation_json FROM passages ORDER BY id'):
                revision, published, message, position, facts_json = db.execute(
                    'SELECT revision,published,message,position,facts_json FROM editions WHERE id=?', (edition_id,)).fetchone()
                facts = json.loads(facts_json)
                source, view, title = db.execute('SELECT path,url,title FROM documents WHERE id=?', (document_id,)).fetchone()
                document = load_navigation(self.appended / 'editions' / revision / 'read')['documents'][source]
                record = next(value for value in document['anchors'] if value['anchor'] == anchor)
                fields = db.execute('SELECT ' + FIELDS + ' FROM passage_content WHERE id=?', (identifier,)).fetchone()
                self.assertEqual(record['text'], fields[4])
                self.assertEqual(facts['title'] + (' — ' + title if title != facts['title'] else '') + ' — ' + record['title'], fields[0])
                presentation = json.loads(citation_json)
                expected = citation_presentation(
                    {'revision': revision, 'manuscript': 'content/manuscript.md', 'document': source, 'anchor': anchor},
                    presentation['href'], title=facts['title'], authors=author_text(ordered_authors(facts)),
                    published=published, edition_number=position + 1,
                    document_title=title if source != 'content/manuscript.md' else '')
                self.assertEqual(expected, presentation)
                oracle.execute('INSERT INTO expected(rowid,' + FIELDS + ') VALUES (?,?,?,?,?,?,?)', (identifier, *fields))
            for query in ['evidence', 'Currenttitle AND evidence', 'Historicalbodyselector',
                          'Windowstartselector AND Windowendselector', 'Oversizedprefixselector*', 'metadata', 'cafe*']:
                expected = oracle.execute('SELECT rowid,bm25(expected,4,2,1,2,1,0.5) FROM expected '
                    'WHERE expected MATCH ? ORDER BY 2,rowid', (query,)).fetchall()
                actual = db.execute('SELECT rowid,bm25(passage_fts,4,2,1,2,1,0.5) FROM passage_fts '
                    'WHERE passage_fts MATCH ? ORDER BY 2,rowid', (query,)).fetchall()
                self.assertEqual(expected, actual, query)
            oracle.execute("CREATE VIRTUAL TABLE vocabulary USING fts5vocab(expected,'instance')")
            db.execute("CREATE VIRTUAL TABLE temp.vocabulary USING fts5vocab(main,passage_fts,'instance')")
            sql = 'SELECT term,doc,col,offset FROM {} ORDER BY doc,col,offset,term'
            self.assertEqual(oracle.execute(sql.format('vocabulary')).fetchall(), db.execute(sql.format('temp.vocabulary')).fetchall())
            db.execute("INSERT INTO passage_fts(passage_fts,rank) VALUES ('integrity-check',1)")
            self.assertEqual('ok', db.execute('PRAGMA integrity_check').fetchone()[0])
            self.assertEqual([], db.execute('PRAGMA foreign_key_check').fetchall())


class EvidenceWindowTests(unittest.TestCase):
    def test_windows_keep_whole_units_and_exact_offsets_with_an_oversized_fallback(self):
        text = 'Café 李 token-words ' * 300 + ' ' + 'oversizedselector' + 'x' * 9000 + ' Endselector.'
        rows = list(windows(text))
        self.assertTrue(rows)
        for start, end, value in rows:
            self.assertLessEqual(len(value), WINDOW_CHARS)
            self.assertEqual(text[start:end], value)
            self.assertTrue(start == 0 or text[start - 1].isspace())
            self.assertTrue(end == len(text) or text[end].isspace())
        self.assertFalse(any('oversizedselector' in value for _, _, value in rows))
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute('CREATE VIRTUAL TABLE full_fts USING fts5(value)')
            db.execute('INSERT INTO full_fts VALUES (?)', (text,))
            excerpt = db.execute("SELECT snippet(full_fts,0,char(57344),char(57345),'…',24) "
                "FROM full_fts WHERE full_fts MATCH 'oversizedselector*'").fetchone()[0]
            self.assertIn('\ue000oversizedselector', excerpt)
