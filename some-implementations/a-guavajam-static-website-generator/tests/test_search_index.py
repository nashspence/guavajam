"""Search preserves signed edition facts and never adds index-file authority."""
from contextlib import closing
import hashlib
import json
import re
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import yaml

from a_guavajam_static_website_generator.index import build_pages, read_index, update_index
from a_guavajam_static_website_generator.search_index import create_index, emit_browser_assets, homepage, runtime_identity, subject_key
from guavajam_test_support.discovery_fixture import Publisher


class SearchIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.site = Path(self.tmp.name)

    def source(self, name, title, phrase, *, keywords=(), subjects=()):
        number = len(list(self.site.glob('sources/*'))) + 1
        sha = format(number, '040x')
        path = 'sources/' + name
        root = self.site / path
        root.mkdir(parents=True)
        (root / 'SITE.json').write_text(json.dumps({'tip': sha, 'title': title}))
        (root / 'CATALOG.json').write_text(json.dumps({'tip': sha, 'editions': [
            {'revision': sha, 'tree': 'f' * 40, 'published': '2026-11-01', 'message': 'Signed ' + title}]}))
        read = root / 'editions' / sha / 'read'
        read.mkdir(parents=True)
        view = {
            'preview': False, 'canonical': {'revision': sha},
            'facts': {'title': title, 'author': 'Example Author', 'language': 'en',
                      'rights': 'CC-BY-4.0', 'abstract': 'A study in ' + phrase,
                      'keywords': list(keywords), 'subjects': list(subjects)},
            'documents': {'content/manuscript.md': {'title': title, 'view': 'index.html',
                                                  'language': 'en', 'source_sha256': 'a' * 64, 'anchors': []},
                          'content/supporting/notes.md': {'title': 'Notes', 'view': 'supporting/notes.html',
                                                        'language': 'en', 'source_sha256': 'b' * 64, 'anchors': []}}}
        navigation = {'schema': 'guavajam.navigation.v1', 'canonical': view['canonical'], 'preview': False,
                      'documents': {path: {**detail, 'text': detail['title'] + ' ' + phrase}
                                    for path, detail in view['documents'].items()}}
        from navigation_fixture import write_navigation
        write_navigation(read, view, navigation)
        (read.parent / 'BIBLIOGRAPHY.json').write_text(json.dumps({
            'schema':'guavajam.search-bibliography.v1','revision':sha,'preview':False,'facts':view['facts']}))
        (read / 'index.html').write_text('<html><head><style>stylesheet</style></head><body><article><h1>' +
                                        title + '</h1><p>' + phrase + '</p></article></body></html>')
        (read / 'supporting').mkdir()
        (read / 'supporting/notes.html').write_text('<article><p>Supporting evidence: ' + phrase + '</p></article>')
        return {'repository': 'https://example.org/' + name + '.git', 'path': path, 'tip': sha}

    def test_fts_combines_latest_bibliographic_fields_across_repositories(self):
        entries = [self.source('alpha', 'Field Notes', 'Arctic ecology', keywords=['fieldwork']),
                   self.source('beta', 'River Records', 'Freshwater biology', keywords=['hydrology'])]
        database = create_index(self.site, entries)
        self.assertEqual(self.site / 'index.sqlite', database)
        with closing(sqlite3.connect(database)) as db:
            self.assertEqual('ok', db.execute('PRAGMA integrity_check').fetchone()[0])
            self.assertEqual([], db.execute('PRAGMA foreign_key_check').fetchall())
            self.assertEqual([('River Records',)], db.execute(
                "SELECT w.title FROM work_fts JOIN works w ON w.id=work_fts.rowid "
                "WHERE work_fts MATCH 'hydrology AND Freshwater'").fetchall())
            self.assertEqual(2, db.execute('SELECT count(*) FROM work_fts').fetchone()[0])
            for term in ['alpha','Signed','stylesheet']:
                self.assertEqual(0, db.execute('SELECT count(*) FROM work_fts WHERE work_fts MATCH ?', (term,)).fetchone()[0])
            self.assertFalse(db.execute("SELECT name FROM sqlite_schema WHERE name IN ('documents','editions','passages')").fetchall())

    def test_directory_restores_editorial_hierarchy_with_optional_search(self):
        entry = self.source('alpha', 'Field Notes', 'Arctic ecology', keywords=['fieldwork'])
        marker = self.site / entry['path'] / 'editions' / entry['tip'] / 'COMMITTER.json'
        marker.write_text(json.dumps({'name': 'Signer Test', 'email': 'signer@example.org'}))
        create_index(self.site, [entry])
        page = homepage(self.site, interactive=True)
        self.assertNotIn('href="mailto:signer@example.org"', page)
        self.assertNotIn('Committed by', page)
        self.assertIn('data-facet="keyword" data-value="fieldwork"', page)
        self.assertIn('class="guavajam-search-spinner"', page)
        self.assertIn('Page 1 of 1', page)
        self.assertIn('1 total</p>', page)
        self.assertNotIn('Browse independently verified works', page)
        self.assertIn('guavajam-icon-search', page)
        self.assertIn('>Works</h2>', page)
        self.assertLess(page.index('Search'), page.index('>Works</h2>'))
        self.assertIn('id="guavajam-index-search" hidden', page)
        self.assertNotIn('Enable search', page)
        self.assertIn('<details class="guavajam-search-filters"><summary class="guavajam-disclosure-summary ', page)
        self.assertIn('guavajam-icon-chevron-right', page)
        self.assertIn('class="guavajam-byline guavajam-scrollline"', page)
        self.assertIn('class="guavajam-abstract"', page)
        self.assertIn('aria-current="page">GuavaJam</h1>', page)
        self.assertIn('sources/alpha/editions/' + entry['tip'] + '/read/index.html', page)
        self.assertIn('aria-label="Field Notes work page"', page)
        heading = re.search(r'<h3\b[^>]*>.*?</h3>', page).group()
        byline = re.search(r'<p class="guavajam-byline.*?</p>', page).group()
        self.assertNotIn('guavajam-record-link', heading)
        self.assertRegex(byline, r'aria-label="Work date and authors"> <a class="guavajam-record-link"')
        self.assertLess(byline.index('guavajam-record-link'), byline.index('<time'))
        self.assertLess(byline.index('<time'), byline.index('Example Author'))
        self.assertIn('aria-label="Field Notes work page"', byline)
        self.assertNotIn(' edition(s) · ', page)
        self.assertIn('id="guavajam-static-list"', page)
        self.assertIn('aria-label="List pages" hidden', page)
        static = homepage(self.site, interactive=False)
        self.assertNotIn('id="guavajam-index-search"', static)
        self.assertIn('id="guavajam-static-list"', static)

    def test_works_index_static_pages_are_bounded_and_sqlite_enhanced(self):
        entries = [self.source(f'work-{n:02d}', f'Work {n:02d}', f'Notes {n:02d}')
                   for n in range(1, 46)]
        create_index(self.site, entries)
        first = homepage(self.site, interactive=True)
        pages = [first] + [(self.site / 'page' / f'{n}.html').read_text('utf-8')
                           for n in (2, 3)]
        unique = []
        for n, markup in enumerate(pages, 1):
            work_rows = re.findall(r'<li><article><h3[^>]*>.*?<a href="([^"]+)"[^>]*>.*?</a><span class="guavajam-link-label" aria-hidden="true">Work (\d+)</span>', markup)
            self.assertEqual([20, 20, 5][n - 1], len(work_rows))
            self.assertLessEqual(len(work_rows), 20)
            self.assertIn('45 total</p>', markup)
            unique.extend(number for _, number in work_rows)
            self.assertEqual(1, markup.count('<h1 aria-current="page">GuavaJam</h1>'))
            self.assertIn('id="guavajam-index-search" hidden', markup)
            self.assertNotIn('Enable search', markup)
            self.assertIn('id="guavajam-search-results"', markup)
            self.assertIn('rel="next"' if n < 3 else 'rel="prev"', markup)
            if n == 1:
                self.assertIn('href="page/2.html" rel="next"', markup)
                self.assertIn('src="search.js"', markup)
                self.assertIn('data-worker="search-worker.js"', markup)
            else:
                self.assertIn('data-root="../"', markup)
                self.assertIn('data-worker="../search-worker.js"', markup)
                self.assertIn('src="../search.js"', markup)
                self.assertTrue(all(href.startswith('../sources/') for href, _ in work_rows))
                self.assertIn('href="../index.html" rel="prev"' if n == 2 else 'href="2.html" rel="prev"', markup)
                self.assertIn('href="../sources/', markup)
        self.assertEqual({f'{n:02d}' for n in range(1, 46)}, set(unique))
        self.assertEqual(45, len(unique))
        self.assertEqual(1, len(re.findall('href="page/2.html" rel="next"', first)))
        # Rendering without the runtime still has exactly the same bounded pages,
        # with no nonfunctional controls or script payloads.
        static = homepage(self.site, interactive=False)
        self.assertEqual(20, len(re.findall(r'<li><article><h3\b', static)))
        self.assertNotIn('<script', static)
        self.assertNotIn('id="guavajam-index-search"', static)
        self.assertEqual(5, len(re.findall(r'<li><article><h3\b', (self.site / 'page/3.html').read_text())))

    def test_subject_context_is_preserved_and_duplicate_claims_remain_in_facts(self):
        one = {'subject': 'Java', 'subject_scheme': 'Programming languages', 'value_uri': 'urn:example:java'}
        two = {'subject': 'Java', 'subject_scheme': 'Places', 'language': 'en'}
        entry = self.source('alpha', 'Field Notes', 'Evidence', keywords=['same', 'same'], subjects=[one, one, two])
        with closing(sqlite3.connect(create_index(self.site, [entry]))) as db:
            for table, count in [('keywords', 1), ('subjects', 2)]:
                self.assertEqual(count, db.execute('SELECT count(*) FROM ' + table).fetchone()[0])
            facts = json.loads(db.execute('SELECT facts_json FROM works').fetchone()[0])
            self.assertEqual([one, one, two], facts['subjects'])
            self.assertEqual(['same', 'same'], facts['keywords'])
            self.assertNotEqual(subject_key(one), subject_key(two))

    def test_static_subjects_and_tags_preserve_facet_keys_and_escape_labels(self):
        subject = {'subject': 'Landscape & sea', 'subject_scheme': 'Field <authority>'}
        entry = self.source('alpha', 'Long <field> & observations', 'Evidence',
                            keywords=['Coastal & studies'], subjects=[subject])
        create_index(self.site, [entry])
        html = homepage(self.site, interactive=True)
        self.assertIn('data-facet="keyword" data-value="Coastal &amp; studies"', html)
        self.assertIn('data-facet="subject" data-value="' + subject_key(subject) + '"', html)
        self.assertIn('Landscape &amp; sea', html)
        self.assertNotIn('Field &lt;authority&gt;', html)
        self.assertIn('class="guavajam-facets guavajam-scrollline"', html)
        self.assertNotIn('Committed by', html)
        self.assertNotIn('class="guavajam-facet" type="button"', html)
        self.assertIn('class="guavajam-byline guavajam-scrollline"', html)

    def test_subject_names_are_short_until_two_distinct_authorities_collide(self):
        # Two works with an identical structured subject do not make it
        # ambiguous; a third work with the same title but a different scheme does.
        shared = {'subject': 'Landscape & sea', 'subject_scheme': 'Field authority'}
        other = {'subject': 'Birds', 'subject_scheme': 'Ornithological index'}
        source_a = self.source('alpha', 'Ocean work', 'Evidence', subjects=[shared])
        source_b = self.source('beta', 'Sky work', 'Observations', subjects=[shared, other])
        database = create_index(self.site, [source_a, source_b])
        plain = homepage(self.site, interactive=True)
        self.assertIn('Landscape &amp; sea</span>', plain)
        self.assertIn('Birds</span>', plain)
        self.assertNotIn('Field authority</span>', plain)
        self.assertNotIn('Ornithological index</span>', plain)
        database.unlink()

        ambiguous = {'subject': 'Landscape & sea', 'subject_scheme': 'Marine taxonomy'}
        third = self.source('gamma', 'Third work', 'Insights', subjects=[ambiguous])
        create_index(self.site, [source_a, source_b, third])
        html = homepage(self.site, interactive=True)
        self.assertIn('Landscape &amp; sea · Field authority', html)
        self.assertIn('Landscape &amp; sea · Marine taxonomy', html)
        self.assertIn('Birds</span>', html)
        self.assertNotIn('Birds · Ornithological index', html)

    def test_failed_or_redirected_bibliography_leaves_no_partial_database(self):
        entry = self.source('alpha', 'Field Notes', 'Safe words')
        path = self.site / entry['path'] / 'editions' / entry['tip'] / 'BIBLIOGRAPHY.json'
        original = json.loads(path.read_text())
        for preview in (True, None):
            path.write_text(json.dumps({**original, 'preview': preview}))
            with self.assertRaises(ValueError): create_index(self.site, [entry])
            self.assertFalse((self.site / 'index.sqlite').exists())
        path.write_text(json.dumps(original))
        outside = self.site / 'outside.json'
        path.rename(outside)
        path.symlink_to(outside)
        with self.assertRaises(ValueError): create_index(self.site, [entry])
        self.assertFalse((self.site / 'index.sqlite').exists())

    def test_index_is_reproducible_and_handles_empty_collection(self):
        entry = self.source('alpha', 'Field Notes', 'Evidence')
        first = create_index(self.site, [entry]).read_bytes()
        (self.site / 'index.sqlite').unlink()
        self.assertEqual(first, create_index(self.site, [entry]).read_bytes())
        empty = self.site / 'empty'
        empty.mkdir()
        with closing(sqlite3.connect(create_index(empty, []))) as db:
            self.assertEqual(0, db.execute('SELECT count(*) FROM works').fetchone()[0])
        self.assertIn('>Works</h2>', homepage(empty, interactive=False))

    def test_runtime_identity_and_static_fallback_are_explicit(self):
        from a_guavajam_static_website_generator.site import page
        entry = self.source('alpha', 'Field Notes', 'Arctic ecology')
        create_index(self.site, [entry])
        self.assertIn('sources/alpha/index.html', homepage(self.site, interactive=False))
        self.assertNotIn('<script', homepage(self.site, interactive=False))
        self.assertIn("script-src 'none'", page('Edition', '<p>Static</p>'))
        runtime = self.site / 'runtime'
        runtime.mkdir()
        with patch.dict('os.environ', {'GUAVAJAM_SQLITE_WASM_DIR': str(runtime)}):
            with self.assertRaises(ValueError): emit_browser_assets(self.site)
            (runtime / 'index.mjs').write_text('export default () => {}')
            (runtime / 'sqlite3.wasm').write_bytes(b'\0asm')
            before = runtime_identity()
            self.assertTrue(emit_browser_assets(self.site))
            (runtime / 'sqlite3.wasm').write_bytes(b'\0asmchanged')
            self.assertNotEqual(before, runtime_identity())
        self.assertTrue((self.site / 'sqlite3-LICENSE.txt').is_file())
        html = homepage(self.site, interactive=True)
        for value in ('guavajam-keyword', 'guavajam-subject', 'wasm-unsafe-eval'):
            self.assertIn(value, html)

    def test_index_yaml_stays_url_only(self):
        path = self.site / 'index.yaml'
        urls = ['https://example.org/a.git', 'https://example.org/b.git']
        path.write_text(yaml.safe_dump({'repositories': urls}))
        self.assertEqual(urls, read_index(path))
        for item in ({'url': urls[0]}, {'url': urls[0], 'tags': ['example']}, {'url': urls[0], 'subjects': ['example']}):
            path.write_text(yaml.safe_dump({'repositories': [item]}))
            with self.subTest(item=item), self.assertRaises(ValueError): read_index(path)


class SignedSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.publisher = Publisher(self.root / 'publisher')
        self.url = 'https://example.org/field-notes.git'
        self.index = self.root / 'index.yaml'
        self.index.write_text(yaml.safe_dump({'repositories': [self.url]}))
        self.trust = self.root / 'trust.yaml'
        self.trust.write_text(yaml.safe_dump({self.url: {'allowed_signers': str(self.publisher.allowed_signers)}}))
        self.baseline = self.root / 'previous.json'
        self.baseline.write_text(json.dumps({'schema': 'guavajam.index-checkpoint.v1', 'catalogs': {}, 'feed': None}))
        fake = patch('a_guavajam_static_website_generator.index.prepare_source', return_value=self.publisher.repo)
        fake.start()
        self.addCleanup(fake.stop)

    def declare(self, title, phrase, keyword, *, date):
        self.publisher.source(title, phrase)
        path = self.publisher.repo / 'content/manuscript.md'
        text = path.read_text().replace('rights: CC-BY-4.0\n', '''rights: CC-BY-4.0
keywords:
  - "''' + keyword + '''"
subjects:
  - subject: "李 *literal* <term>"
    subject_scheme: Local classification
    classification_code: "001.20-A"
    value_uri: urn:example:term
    language: zh-Hant-TW
''')
        path.write_text(text)
        return self.publisher.publish('Record ' + title, date=date)

    def test_pages_preserves_latest_bibliography_and_separate_historical_passages(self):
        older = self.declare('Earlier Notes', 'Historic narwhal evidence.', 'old keyword', date='2026-11-10T12:00:00Z')
        latest = self.declare('Current Notes', 'Current estuary evidence.', 'new keyword', date='2026-11-01T12:00:00Z')
        output, checkpoint = self.root / 'public', self.root / 'next.json'
        build_pages(self.index, self.trust, output, self.baseline, checkpoint)
        entry = json.loads((output / 'INDEX.json').read_text())['repositories'][0]
        self.assertEqual({'repository', 'path', 'tip'}, set(entry))
        view = json.loads((output / entry['path'] / 'editions' / latest / 'read/VIEW.json').read_text())
        self.assertEqual('李 *literal* <term>', view['facts']['subjects'][0]['subject'])
        self.assertEqual('001.20-A', view['facts']['subjects'][0]['classification_code'])
        self.assertEqual('urn:example:term', view['facts']['subjects'][0]['value_uri'])
        self.assertIn('Current Notes', (output / 'index.html').read_text())
        self.assertFalse((output / 'STATE.json').exists())
        self.assertEqual(latest, json.loads(checkpoint.read_text())['catalogs'][self.url]['tip'])
        with closing(sqlite3.connect(output / 'index.sqlite')) as db:
            self.assertEqual(('Current Notes', latest, '2026-11-01'), db.execute(
                'SELECT title,latest_revision,latest_published FROM works').fetchone())
            self.assertEqual([('new keyword',)], db.execute('SELECT name FROM keywords').fetchall())
            self.assertEqual(0, db.execute("SELECT count(*) FROM work_fts WHERE work_fts MATCH 'narwhal'").fetchone()[0])
            stored = json.loads(db.execute('SELECT facts_json FROM works').fetchone()[0])
            self.assertEqual(view['facts'], stored)
        with closing(sqlite3.connect(output / entry['path'] / 'history.sqlite')) as db:
            hits = db.execute("SELECT p.url FROM passage_fts JOIN passages p ON p.id=passage_fts.rowid "
                              "WHERE passage_fts MATCH 'narwhal'").fetchall()
            self.assertEqual(1, len(hits))
            self.assertIn('/editions/' + older + '/read/index.html', '/' + hits[0][0])
            self.assertTrue((output / entry['path'] / hits[0][0].split('#')[0]).is_file())


    def test_managed_generations_include_database_and_runtime_identity(self):
        output = self.root / 'managed'
        with patch('a_guavajam_static_website_generator.index.runtime_identity', return_value={'assets': 'one'}):
            self.assertEqual('updated', update_index(self.index, self.trust, output)['status'])
            self.assertTrue((output / 'current/index.sqlite').is_file())
            first = (output / 'current').readlink()
            self.assertEqual('unchanged', update_index(self.index, self.trust, output)['status'])
        with patch('a_guavajam_static_website_generator.index.runtime_identity', return_value={'assets': 'two'}):
            self.assertEqual('updated', update_index(self.index, self.trust, output)['status'])
            self.assertNotEqual(first, (output / 'current').readlink())


if __name__ == '__main__':
    unittest.main(verbosity=2)
