"""The ordinary search scopes are physical, signed-source-derived corpora."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from a_guavajam_static_website_generator.search_index import create_index, subject_key
from a_guavajam_static_website_generator.site import build_site
from guavajam_test_support.discovery_fixture import Publisher


class ScopedSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.publisher = Publisher(cls.root / 'publisher')
        supporting = cls.publisher.repo / 'content/supporting'
        supporting.mkdir()
        cls.publisher.source('Supersededtitle', 'Historicalbodyselector evidence from the old manuscript.')
        manuscript = cls.publisher.repo / 'content/manuscript.md'
        manuscript.write_text(manuscript.read_text().replace('author: Example Author',
            'author: Supersededauthor').replace('rights: CC-BY-4.0',
            'rights: CC-BY-4.0\nabstract: Supersededabstract\nkeywords:\n  - Supersededtag\n'
            'subjects:\n  - subject: Supersededsubject'))
        (supporting / 'note.md').write_text(
            '---\ntitle: Old supporting title\nabstract: Old supporting description.\n---\n\n'
            '<a name="evidence"></a>\n\nHistoricalsupportselector.\n')
        cls.older = cls.publisher.publish('Oldmessageselector', date='2035-11-01T12:00:00Z')
        cls.publisher.source('Currenttitle', 'Currentbodyselector current evidence.\n\n' +
            'Windowstartselector ' + 'windowedword ' * 500 + 'Windowendselector.\n\n' +
            'Oversizedprefixselector' + 'x' * 9000 + '.')
        cls.subjects = [
            {'subject': 'Shared subject', 'subject_scheme': 'Authority A'},
            {'subject': 'Shared subject', 'subject_scheme': 'Authority B'}]
        manuscript.write_text(manuscript.read_text().replace('author: Example Author',
            'authors:\n  - name: Firstcurrentauthor\n  - name: Secondcurrentauthor')
            .replace('rights: CC-BY-4.0', 'rights: CC-BY-4.0\nkeywords:\n  - Currenttag\nsubjects:\n'
                     '  - subject: Shared subject\n    subject_scheme: Authority A\n'
                     '  - subject: Shared subject\n    subject_scheme: Authority B'))
        (supporting / 'note.md').write_text(
            '---\ntitle: Current supporting title\nabstract: Directoryabstractselector.\n'
            'metadata:\n  Applies to: ["01", "A", "café", ""]\n  Empty: []\n  Literal: 001\n---\n\n<a name="metadata"></a>\n\n'
            '<a name="evidence"></a>\n\nSupportingbodyselector supporting evidence.\n')
        note = supporting / 'note.md'
        note.write_text(note.read_text().replace('  Literal: 001\n',
            '  Literal: 001\n  Longliteral: "' + 'İ' * 28000 + '"\n'))
        cls.latest = cls.publisher.publish('Currentmessageselector\n\nA complete commit message.',
                                           date='2025-11-01T12:00:00Z')
        cls.first = cls.root / 'first'
        cls.first.mkdir()
        cls.work = cls.first / 'sources/work'
        build_site(cls.publisher.repo, cls.work, repository='https://example.org/work.git',
                   search_assets='../../', **cls.publisher.trust)
        entry = {'repository': 'https://example.org/work.git', 'path': 'sources/work', 'tip': cls.latest}
        create_index(cls.first, [entry])
        # Append a real accepted edition with optional metadata absent.
        cls.publisher.source('Newesttitle', 'A new accepted body.')
        cls.newest = cls.publisher.publish('Newestmessage', date='2020-11-01T12:00:00Z')
        cls.second = cls.root / 'second'
        cls.second.mkdir()
        cls.appended = cls.second / 'sources/work'
        build_site(cls.publisher.repo, cls.appended, repository='https://example.org/work.git',
                   search_assets='../../', **cls.publisher.trust)
        create_index(cls.second, [{**entry, 'tip': cls.newest}])

    def connect(self, scope, *, revision=None, appended=False):
        root = self.appended if appended else self.work
        path = root / ('editions/' + revision + '/search/' + scope + '.sqlite' if revision else scope + '.sqlite')
        return closing(sqlite3.connect(path))

    def test_works_is_latest_bibliography_without_historical_or_body_tables(self):
        with closing(sqlite3.connect(self.first / 'index.sqlite')) as db:
            tables = {row[0] for row in db.execute('SELECT name FROM sqlite_schema')}
            self.assertTrue({'editions', 'documents', 'passages', 'version_fts', 'passage_fts'}.isdisjoint(tables))
            self.assertEqual(1, db.execute('SELECT count(*) FROM work_fts').fetchone()[0])
            self.assertEqual(('Currenttitle', 'Firstcurrentauthor, Secondcurrentauthor', '',
                              '2025-11-01', self.latest, 3),
                             db.execute('SELECT title,authors,abstract,latest_published,latest_revision,edition_count FROM works').fetchone())
            for word in ['Currenttitle', 'Firstcurrentauthor', 'Secondcurrentauthor', 'Currenttag', 'Shared']:
                self.assertEqual(1, db.execute('SELECT count(*) FROM work_fts WHERE work_fts MATCH ?', (word,)).fetchone()[0])
            for word in ['Supersededtitle', 'Supersededauthor', 'Supersededabstract', 'Supersededtag',
                         'Supersededsubject', 'Historicalbodyselector', 'Historicalsupportselector',
                         'Currentbodyselector', 'Supportingbodyselector', 'Currentmessageselector']:
                self.assertEqual(0, db.execute('SELECT count(*) FROM work_fts WHERE work_fts MATCH ?', (word,)).fetchone()[0], word)
            self.assertEqual({subject_key(item) for item in self.subjects},
                             {row[0] for row in db.execute('SELECT key FROM subjects')})
            facts = json.loads(db.execute('SELECT facts_json FROM works').fetchone()[0])
            self.assertEqual([{'name': 'Firstcurrentauthor'}, {'name': 'Secondcurrentauthor'}], facts['authors'])

    def test_latest_absent_metadata_does_not_fall_back(self):
        with closing(sqlite3.connect(self.second / 'index.sqlite')) as db:
            self.assertEqual(('Newesttitle', '', '2020-11-01'), db.execute(
                'SELECT title,abstract,latest_published FROM works').fetchone())
            self.assertEqual([], db.execute('SELECT * FROM keywords').fetchall())
            self.assertEqual([], db.execute('SELECT * FROM subjects').fetchall())
            for term in ['Currenttitle', 'Currenttag', 'Shared', 'Supersededabstract']:
                self.assertEqual(0, db.execute('SELECT count(*) FROM work_fts WHERE work_fts MATCH ?', (term,)).fetchone()[0])

    def test_versions_matches_names_titles_and_full_messages_only(self):
        with self.connect('versions') as db:
            for term in ['v2', 'Currenttitle', 'Currentmessageselector', 'complete', 'Oldmessageselector']:
                self.assertTrue(db.execute('SELECT rowid FROM version_fts WHERE version_fts MATCH ?', (term,)).fetchall(), term)
            for term in ['Currentbodyselector', 'Historicalbodyselector', 'Supportingbodyselector', 'Currenttag', 'Firstcurrentauthor']:
                self.assertEqual([], db.execute('SELECT rowid FROM version_fts WHERE version_fts MATCH ?', (term,)).fetchall(), term)
            tables = {row[0] for row in db.execute('SELECT name FROM sqlite_schema')}
            self.assertTrue({'documents', 'passages', 'work_fts', 'passage_fts'}.isdisjoint(tables))

    def test_documents_are_supporting_title_abstracts_in_one_exact_edition(self):
        with self.connect('documents', revision=self.latest) as db:
            self.assertEqual([(self.latest,)], db.execute('SELECT revision FROM editions').fetchall())
            self.assertEqual([('content/supporting/note.md',)], db.execute('SELECT path FROM documents').fetchall())
            self.assertEqual(1, db.execute("SELECT count(*) FROM document_fts WHERE document_fts MATCH 'Directoryabstractselector'").fetchone()[0])
            for term in ['Supportingbodyselector', 'Historicalsupportselector', 'Currentbodyselector', '001']:
                self.assertEqual(0, db.execute('SELECT count(*) FROM document_fts WHERE document_fts MATCH ?', (term,)).fetchone()[0])
            self.assertEqual([('Literal', '001')], db.execute("SELECT key,value FROM document_facets WHERE key='Literal'").fetchall())
            self.assertEqual(0, db.execute("SELECT count(*) FROM document_facets WHERE key='Empty'").fetchone()[0])
            self.assertEqual([('list',)], db.execute("SELECT kind FROM document_fields WHERE key='Empty'").fetchall())
            self.assertEqual({'01', 'A', 'café', ''}, {row[0] for row in db.execute(
                "SELECT value FROM document_facets WHERE key='Applies to'")})

    def test_passages_are_exact_occurrences_and_history_preserves_identical_sources(self):
        with self.connect('passages', revision=self.latest) as db, self.connect('history') as history:
            self.assertEqual([(self.latest,)], db.execute('SELECT revision FROM editions').fetchall())
            self.assertEqual(0, db.execute("SELECT count(*) FROM passage_fts WHERE passage_fts MATCH 'Historicalbodyselector'").fetchone()[0])
            self.assertGreater(history.execute("SELECT count(*) FROM passage_fts WHERE passage_fts MATCH 'Historicalbodyselector'").fetchone()[0], 0)
            for url, payload, revision, source, anchor, digest in db.execute(
                    'SELECT p.url,p.citation_json,e.revision,d.path,p.anchor,d.source_sha256 '
                    'FROM passages p JOIN editions e ON e.id=p.edition_id JOIN documents d ON d.id=p.document_id'):
                citation = json.loads(payload)
                self.assertEqual(self.latest, revision)
                self.assertIn(['code', revision], citation['source'])
                self.assertIn(['code', source], citation['source'])
                self.assertIn(['code', '#' + anchor], citation['source'])
                self.assertTrue(url.endswith('#' + anchor))
                self.assertEqual(64, len(digest))
            # Native scope-specific ranking oracle built from those exact history occurrences.
            fields = 'title,authors,abstract,labels,body,git'
            expected = sqlite3.connect(':memory:')
            self.addCleanup(expected.close)
            expected.execute('CREATE VIRTUAL TABLE oracle USING fts5(' + fields +
                             ",tokenize='unicode61 remove_diacritics 2',detail=full,columnsize=1)")
            records = history.execute('SELECT p.url,' + ','.join('f.' + field for field in fields.split(',')) +
                ' FROM passage_fts f JOIN passages p ON p.id=f.rowid JOIN editions e ON e.id=p.edition_id WHERE e.revision=? ORDER BY p.id',
                (self.latest,)).fetchall()
            for number, row in enumerate(records, 1):
                expected.execute('INSERT INTO oracle(rowid,' + fields + ') VALUES (?,?,?,?,?,?,?)', (number, *row[1:]))
            actual = db.execute("SELECT p.url,bm25(passage_fts,4,2,1,2,1,0.5) FROM passage_fts "
                "JOIN passages p ON p.id=passage_fts.rowid WHERE passage_fts MATCH 'evidence' ORDER BY p.id").fetchall()
            scores = expected.execute("SELECT rowid,bm25(oracle,4,2,1,2,1,0.5) FROM oracle WHERE oracle MATCH 'evidence' ORDER BY rowid").fetchall()
            self.assertEqual(actual, [(records[rowid - 1][0], score) for rowid, score in scores])

    def test_appending_history_keeps_existing_edition_artifacts_byte_identical(self):
        for revision in [self.older, self.latest]:
            for scope in ['documents', 'passages']:
                relative = 'editions/' + revision + '/search/' + scope + '.sqlite'
                self.assertEqual((self.work / relative).read_bytes(), (self.appended / relative).read_bytes())
                first = sorted((self.work / Path(relative).parent).glob(scope + '.*.sqlite.gz'))
                second = sorted((self.appended / Path(relative).parent).glob(scope + '.*.sqlite.gz'))
                self.assertEqual([path.name for path in first], [path.name for path in second])
                self.assertEqual(first[0].read_bytes(), second[0].read_bytes())

    def test_routing_is_small_and_versions_has_native_radio_scope(self):
        routing = json.loads((self.work / 'SEARCH.json').read_text())
        self.assertEqual({'schema', 'versions', 'history'}, set(routing))
        self.assertEqual('guavajam.search-routing.v1', routing['schema'])
        html = (self.work / 'index.html').read_text()
        self.assertIn('name="search-scope" value="versions" checked', html)
        self.assertIn('name="search-scope" value="history"', html)
        self.assertLess(html.index('guavajam-search-scope'), html.index('class="guavajam-query-field"'))
        self.assertIn('data-scope="versions"', html)
        self.assertNotIn('work.sqlite', html)
        for name, scope in [('documents', 'documents'), ('passages', 'passages')]:
            page = (self.work / 'editions' / self.latest / 'read' / (name + '.html')).read_text()
            self.assertIn('data-scope="' + scope + '"', page)
            self.assertIn('search/' + scope + '.', page)


if __name__ == '__main__':
    unittest.main()
