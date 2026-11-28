"""Work discovery reuses verified navigation without changing publication identity."""
from contextlib import closing
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urljoin, urlsplit
import yaml

from a_guavajam_static_website_generator.index import build_pages
from a_guavajam_static_website_generator.citation_routes import citation_fragment, citation_href
from a_guavajam_static_website_generator.search_index import create_index
from a_guavajam_static_website_generator.site import build_site
from a_guavajam_static_website_generator.update import update
from guavajam_test_support.discovery_fixture import Publisher


class Links(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.links = []
        self.passage_citations = []
        self.ids = set()
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and 'href' in attrs:
            self.links.append(attrs['href'])
            if 'guavajam-passage-cite' in attrs.get('class', '').split():
                self.passage_citations.append(attrs['href'])
        if 'id' in attrs:
            self.ids.add(attrs['id'])


class WorkSearchTests(unittest.TestCase):
    def assert_body_tokens(self, db, table, rows):
        """Every source token and position survives without storing plaintext."""
        with closing(sqlite3.connect(':memory:')) as expected:
            expected.execute("CREATE VIRTUAL TABLE body_fts USING fts5(body, tokenize='unicode61 remove_diacritics 2')")
            expected.executemany('INSERT INTO body_fts(rowid,body) VALUES (?,?)', rows)
            expected.execute("CREATE VIRTUAL TABLE tokens USING fts5vocab(body_fts, 'instance')")
            db.execute('CREATE VIRTUAL TABLE temp.actual_' + table +
                       " USING fts5vocab(main," + table + ", 'instance')")
            self.assertEqual(expected.execute('SELECT doc,term,offset FROM tokens ORDER BY doc,offset,term').fetchall(),
                             [tuple(row) for row in db.execute('SELECT doc,term,offset FROM temp.actual_' +
                                table + " WHERE col='body' ORDER BY doc,offset,term")])

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.publisher = Publisher(cls.root / 'publisher')
        supporting = cls.publisher.repo / 'content/supporting'
        supporting.mkdir()
        (supporting / 'unlinked.md').write_text(
            '---\ntitle: Unlinked evidence\nabstract: Lacustrine methods and supplemental observations.\n---\n\n'
            '<a name="42"></a>\n\nGlaciolacustrine evidence survives without a manuscript link.\n')
        review = (Path(__file__).parent / 'fixtures/work-search.gfm.md').read_bytes()
        (supporting / 'review.md').write_bytes(review)
        cls.publisher.source('Earlier field notes', 'Palaeoriver belongs to the historical edition.')
        cls.older = cls.publisher.publish('Historical observation', date='2030-11-10T12:00:00Z')
        cls.publisher.source('Current field notes', 'An estuary observation with eco**logy** inside a word.')
        manuscript = cls.publisher.repo / 'content/manuscript.md'
        with manuscript.open('a') as stream:
            stream.write('\n<a id="detail"></a>\n### Opening detail\n\nA nested estuary observation.\n')
            for number in range(1, 25):
                marker = ' Pelagicendpointselector.' if number == 24 else ''
                stream.write(f'\n<a id="section-{number}"></a>\n## Observation {number}\n\n'
                             f'Observation number {number} has independently addressable source text.{marker}\n')
        cls.latest = cls.publisher.publish('Current observation', date='2025-11-01T12:00:00Z')
        cls.site = cls.root / 'collection/works/field-notes'
        build_site(cls.publisher.repo, cls.site, **cls.publisher.trust, search_assets='')

    def copy_site(self, suffix):
        directory = self.root / suffix
        shutil.copytree(self.site, directory)
        self.addCleanup(shutil.rmtree, directory)
        (directory / 'history.sqlite').unlink()
        return directory

    def rebuild(self, root):
        return create_index(root, [{'repository': '', 'path': '', 'tip': self.latest}],
                            filename='history.sqlite', scope='history')

    def reader(self, revision):
        return self.site / 'editions' / revision / 'read'

    def test_search_citations_keep_the_same_exact_download_and_source_parts(self):
        with sqlite3.connect(self.site / 'history.sqlite') as db:
            for payload, revision, source, anchor in db.execute(
                    'SELECT p.citation_json,e.revision,d.path,p.anchor FROM passages p '
                    'JOIN editions e ON e.id=p.edition_id JOIN documents d ON d.id=p.document_id'):
                presented = json.loads(payload)
                self.assertIn(['code', revision], presented['source'])
                self.assertIn(['code', source], presented['source'])
                self.assertIn(['code', '#' + anchor], presented['source'])
                cited = json.loads((self.site / unquote(presented['href'])).read_text())
                self.assertEqual(revision, cited['revision'])
                self.assertEqual(anchor, cited['anchor'])
                self.assertEqual(source, cited.get('document', cited['manuscript']))
                self.assertEqual('Download official citation JSON', presented['label'])
                self.assertTrue(presented['description'])

    def test_static_directories_reach_every_document_without_script(self):
        home = (self.site / 'index.html').read_text()
        self.assertNotIn('browse/index.html', Links(home).links)
        self.assertIn('editions/' + self.latest + '/read/index.html', Links(home).links)
        self.assertNotIn('editions/' + self.latest + '/metadata.html', Links(home).links)
        self.assertIn('data-static-heading="Versions"', home)
        history = (self.site / 'index.html').read_text()
        for revision in self.publisher.revisions:
            self.assertIn('editions/' + revision + '/metadata.html#provenance', Links(history).links)
            self.assertIn('editions/' + revision + '/read/index.html', Links(history).links)
        self.assertIn("script-src 'none'", home)
        self.assertNotIn('<script', home)
        reader = self.reader(self.latest)
        directory = (reader / 'documents.html').read_text()
        self.assertIn('supporting/unlinked.html', Links(directory).links)
        self.assertIn('supporting/review.html', Links(directory).links)
        self.assertNotIn('<script', directory)
        supporting = (reader / 'supporting/unlinked.html').read_text()
        self.assertIn('../index.html', Links(supporting).links)
        self.assertNotIn('../documents.html', Links(supporting).links)
        manuscript = (reader / 'index.html').read_text()
        self.assertIn('documents.html', Links(manuscript).links)
        self.assertIn('../metadata.html', Links(manuscript).links)
        self.assertNotIn('../verify.html', Links(manuscript).links)
        self.assertNotIn('../license.html', Links(manuscript).links)
        view = json.loads((reader / 'VIEW.json').read_text())
        for name in ('NAV.json', 'documents.html', 'contents.html', 'passages.html'):
            self.assertEqual(hashlib.sha256((reader / name).read_bytes()).hexdigest(), view['files'][name])
        site = json.loads((self.site / 'SITE.json').read_text())
        for name in ('history.sqlite', 'index.html'):
            self.assertEqual(hashlib.sha256((self.site / name).read_bytes()).hexdigest(), site['files'][name])

    def test_work_records_keep_source_order_text_hashes_and_exact_fragment_targets(self):
        with closing(sqlite3.connect(self.site / 'history.sqlite')) as db:
            db.row_factory = sqlite3.Row
            self.assertEqual('guavajam.search.v1', db.execute(
                "SELECT value FROM metadata WHERE key='schema'").fetchone()[0])
            self.assertEqual('history', db.execute("SELECT value FROM metadata WHERE key='scope'").fetchone()[0])
            self.assertEqual((self.latest, '2025-11-01', 3), tuple(db.execute(
                'SELECT latest_revision,latest_published,edition_count FROM works').fetchone()))
            self.assertEqual(self.publisher.revisions, [row[0] for row in db.execute(
                'SELECT revision FROM editions ORDER BY position')])
            self.assertEqual([], db.execute('PRAGMA foreign_key_check').fetchall())
            self.assertEqual('ok', db.execute('PRAGMA integrity_check').fetchone()[0])
            for row in db.execute('SELECT d.*,e.revision FROM documents d JOIN editions e ON e.id=d.edition_id'):
                from a_guavajam_static_website_generator.navigation_manifest import load_navigation
                nav = load_navigation(self.reader(row['revision']))
                detail = nav['documents'][row['path']]
                self.assertEqual(detail['source_sha256'], row['source_sha256'])
                self.assertEqual(hashlib.sha256((self.reader(row['revision']) /
                    ('source.gfm' if row['path'] == 'content/manuscript.md' else
                     Path(detail['view']).with_suffix('.gfm'))).read_bytes()).hexdigest(), row['source_sha256'])
            passages = db.execute('SELECT p.*,e.revision,d.path FROM passages p '
                                  'JOIN editions e ON e.id=p.edition_id '
                                  'JOIN documents d ON d.id=p.document_id').fetchall()
            self.assertGreater(len(passages), 20)
            passage_bodies = []
            for passage in passages:
                from a_guavajam_static_website_generator.navigation_manifest import load_navigation
                nav = load_navigation(self.reader(passage['revision']))
                anchor = nav['documents'][passage['path']]['anchors'][passage['position']]
                for key in ('anchor', 'title', 'kind', 'level', 'position', 'parent', 'scope'):
                    self.assertEqual(anchor[key], passage[key])
                passage_bodies.append((passage['id'], anchor['text']))
                target = urlsplit(passage['url'])
                self.assertEqual('', target.scheme)
                self.assertEqual(passage['anchor'], unquote(target.fragment))
                self.assertIn(passage['revision'], target.path)
                self.assertIn(passage['anchor'], Links((self.site / unquote(target.path)).read_text()).ids)
            self.assert_body_tokens(db, 'passage_fts', passage_bodies)
            latest = [row for row in passages if row['revision'] == self.latest]
            main = {row['anchor']: row for row in latest if row['path'] == 'content/manuscript.md'}
            self.assertEqual('opening', main['detail']['parent'])
            self.assertEqual('document', main['detail']['scope'])
            self.assertTrue(any(row['scope'] == 'quotation:reply' and row['kind'] == 'section' for row in latest))
            author = next(row for row in latest if row['title'] == 'Author continuation')
            self.assertEqual('document', author['scope'])
            self.assertNotEqual('reply', author['parent'])
            self.assertEqual(1, db.execute('SELECT count(*) FROM passage_fts JOIN passages p '
                'ON p.id=passage_fts.rowid JOIN editions e ON e.id=p.edition_id '
                'JOIN documents d ON d.id=p.document_id '
                "WHERE passage_fts MATCH 'body:ecology' AND e.revision=? AND d.path='content/manuscript.md'",
                (self.latest,)).fetchone()[0])
            historical = db.execute('SELECT p.url FROM passage_fts JOIN passages p '
                "ON p.id=passage_fts.rowid WHERE passage_fts MATCH 'Palaeoriver'").fetchall()
            self.assertEqual(['editions/' + self.older + '/read/index.html#opening'], [row[0] for row in historical])

    def test_document_discovery_indexes_only_supporting_titles_and_their_own_abstracts(self):
        with sqlite3.connect(self.site / 'editions' / self.latest / 'search/documents.sqlite') as db:
            self.assertTrue(db.execute("SELECT rowid FROM document_fts WHERE document_fts MATCH 'Lacustrine'").fetchall())
            for term in ['Glaciolacustrine', 'Palaeoriver', 'Pelagicendpointselector', 'content', 'manuscript']:
                self.assertEqual([], db.execute('SELECT rowid FROM document_fts WHERE document_fts MATCH ?', (term,)).fetchall())
            rows = db.execute("SELECT d.path,d.abstract FROM document_fts JOIN documents d ON d.id=document_fts.rowid "
                              "WHERE document_fts MATCH 'Lacustrine'").fetchall()
            self.assertTrue(all(path == 'content/supporting/unlinked.md' and abstract.startswith('Lacustrine')
                                for path,abstract in rows))
        html = (self.reader(self.latest) / 'documents.html').read_text()
        self.assertIn('Lacustrine methods and supplemental observations.',html)
        self.assertNotIn('Document source',html)
        self.assertNotIn('Supporting document ·',html)
        self.assertNotIn('content/supporting/unlinked.md</code>',html)

    def test_edition_documents_share_exact_history_source_facts(self):
        collection = self.root / 'collection'
        database = create_index(collection, [{'repository': 'https://example.org/field-notes.git',
                                              'path': 'works/field-notes', 'tip': self.latest}])
        self.addCleanup(database.unlink)
        with closing(sqlite3.connect(database)) as aggregate, \
                closing(sqlite3.connect(self.site / 'history.sqlite')) as history, \
                closing(sqlite3.connect(self.site / 'editions' / self.latest / 'search/documents.sqlite')) as documents:
            self.assertEqual(1, aggregate.execute('SELECT count(*) FROM work_fts').fetchone()[0])
            self.assertFalse(aggregate.execute(
                "SELECT name FROM sqlite_schema WHERE name IN ('documents','passages','passage_fts')").fetchall())
            columns = 'd.title,d.path,d.language,d.source_sha256,d.abstract'
            expected = history.execute('SELECT ' + columns + ' FROM documents d JOIN editions e ON e.id=d.edition_id '
                "WHERE e.revision=? AND d.path<>'content/manuscript.md' ORDER BY d.path", (self.latest,)).fetchall()
            self.assertEqual(expected, documents.execute('SELECT ' + columns + ' FROM documents d ORDER BY d.path').fetchall())

    def test_sqlite_citation_targets_match_reader_links_and_static_pages(self):
        """A hit retains its source page even when filtering changes result order."""
        origin = 'https://example.invalid/prefix/'
        with closing(sqlite3.connect(self.site / 'history.sqlite')) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute('SELECT p.*,e.revision,d.path,d.url AS reader_url FROM passages p '
                              'JOIN editions e ON e.id=p.edition_id '
                              'JOIN documents d ON d.id=p.document_id').fetchall()
            self.assertTrue(any('/passages/page/2.html#' in row['citation_url'] for row in rows))
            self.assertTrue(any(row['scope'].startswith('quotation:') for row in rows))
            for row in rows:
                with self.subTest(revision=row['revision'], document=row['path'], anchor=row['anchor']):
                    cited = urlsplit(row['citation_url'])
                    self.assertEqual('', cited.scheme)
                    self.assertEqual(citation_fragment(row['path'], row['anchor']), unquote(cited.fragment))
                    self.assertIn(row['revision'], cited.path)
                    target = self.site / unquote(cited.path)
                    self.assertIn(unquote(cited.fragment), Links(target.read_text()).ids)
                    reading = self.site / unquote(row['reader_url'])
                    links = Links(reading.read_text())
                    self.assertIn(origin + row['citation_url'], [
                        urljoin(origin + row['reader_url'], href) for href in links.passage_citations])
            # Query a later source passage by a unique word, never a number that
            # can also occur in random Git provenance. This hit
            # becomes result one but must still link to the second static page.
            found = db.execute('SELECT p.citation_url FROM passage_fts '
                               'JOIN passages p ON p.id=passage_fts.rowid '
                               'JOIN editions e ON e.id=p.edition_id '
                               'JOIN documents d ON d.id=p.document_id '
                               "WHERE passage_fts MATCH 'pelagicendpointselector*' "
                               "AND e.revision=? AND d.path='content/manuscript.md'",
                               (self.latest,)).fetchall()
            self.assertEqual(['editions/' + self.latest +
                              '/read/' + citation_href('content/manuscript.md', 25, 'section-24')],
                             [row['citation_url'] for row in found])

    def test_corrupt_navigation_cannot_create_a_partial_database(self):
        for name in ('hash', 'schema', 'revision', 'preview', 'documents', 'abstract', 'anchors', 'missing'):
            with self.subTest(name=name):
                site = self.copy_site('corrupt-' + name)
                reader = site / 'editions' / self.latest / 'read'
                navigation = reader / 'NAV.json'
                data = json.loads(navigation.read_text())
                if name in {'abstract', 'anchors'}:
                    source = 'content/supporting/unlinked.md' if name == 'abstract' else 'content/manuscript.md'
                    descriptor = data['documents'][source]
                    child = reader / descriptor['url']
                    value = json.loads(child.read_text())
                    if name == 'abstract':
                        value['document']['abstract'] = 'Unverified replacement'
                    else:
                        value['document']['anchors'][0]['anchor'] = 'missing-target'
                    child.write_text(json.dumps(value))
                    descriptor['sha256'] = hashlib.sha256(child.read_bytes()).hexdigest()
                    descriptor['size'] = child.stat().st_size
                    view_path = reader / 'VIEW.json'
                    view = json.loads(view_path.read_text())
                    view['files'][descriptor['url']] = descriptor['sha256']
                    view_path.write_text(json.dumps(view))
                elif name == 'schema':
                    data['schema'] = 'unknown.navigation.v1'
                elif name == 'revision':
                    data['canonical']['revision'] = self.older
                elif name == 'preview':
                    data['preview'] = True
                elif name == 'documents':
                    data['documents'].pop('content/supporting/unlinked.md')
                navigation.write_text(json.dumps(data))
                if name == 'missing':
                    navigation.unlink()
                elif name != 'hash':
                    view_path = reader / 'VIEW.json'
                    view = json.loads(view_path.read_text())
                    view['files']['NAV.json'] = hashlib.sha256(navigation.read_bytes()).hexdigest()
                    view_path.write_text(json.dumps(view))
                with self.assertRaises((OSError, ValueError)):
                    self.rebuild(site)
                self.assertFalse((site / 'history.sqlite').exists())

    def test_work_database_is_reproducible_and_bounded(self):
        site = self.copy_site('reproducible')
        first = self.rebuild(site).read_bytes()
        (site / 'history.sqlite').unlink()
        self.assertEqual(first, self.rebuild(site).read_bytes())
        (site / 'history.sqlite').unlink()
        with patch('a_guavajam_static_website_generator.navigation_manifest.MAX_MANIFEST_BYTES', 1), self.assertRaises(ValueError):
            self.rebuild(site)
        self.assertFalse((site / 'history.sqlite').exists())

    def test_unpublished_work_still_has_a_self_contained_directory(self):
        site = self.root / 'unpublished'
        before_publication = self.publisher.git('rev-parse', self.publisher.revisions[0] + '^')
        build_site(self.publisher.repo, site, ref=before_publication,
                   **self.publisher.trust, search_assets='')
        with closing(sqlite3.connect(site / 'history.sqlite')) as db:
            self.assertEqual(0, db.execute('SELECT COUNT(*) FROM editions').fetchone()[0])
            self.assertEqual(0, db.execute('SELECT COUNT(*) FROM documents').fetchone()[0])
            self.assertEqual(0, db.execute('SELECT COUNT(*) FROM passages').fetchone()[0])
            self.assertEqual(('', 'Unpublished work'), db.execute('SELECT latest_revision,title FROM works').fetchone())
        self.assertIn('No published versions.', (site / 'index.html').read_text())
        self.assertFalse((site / 'browse/index.html').exists())
        self.assertTrue((site / 'index.html').is_file())

    def test_aggregate_build_shares_one_runtime_across_work_search_pages(self):
        directory = self.root / 'shared-runtime'
        directory.mkdir()
        runtime = directory / 'runtime'
        runtime.mkdir()
        (runtime / 'index.mjs').write_text('export default function fixture() {}')
        (runtime / 'sqlite3.wasm').write_bytes(b'\0asm')
        urls = ['https://example.org/field-one.git', 'https://example.org/field-two.git']
        index, trust = directory / 'index.yaml', directory / 'trust.yaml'
        index.write_text(yaml.safe_dump({'repositories': urls}))
        trust.write_text(yaml.safe_dump({url: {'allowed_signers': str(self.publisher.allowed_signers)}
                                        for url in urls}))
        previous = directory / 'previous.json'
        previous.write_text(json.dumps({'schema': 'guavajam.index-checkpoint.v1', 'catalogs': {}, 'feed': None}))
        output = directory / 'site'
        with patch.dict('os.environ', {'GUAVAJAM_SQLITE_WASM_DIR': str(runtime)}), \
                patch('a_guavajam_static_website_generator.index.prepare_source', return_value=self.publisher.repo):
            build_pages(index, trust, output, previous, directory / 'checkpoint.json')
        self.assertEqual([output / 'sqlite3.wasm'], list(output.rglob('sqlite3.wasm')))
        self.assertEqual([output / 'search-worker.js'], list(output.rglob('search-worker.js')))
        entries = json.loads((output / 'INDEX.json').read_text())['repositories']
        self.assertEqual(2, len(entries))
        for entry in entries:
            work = output / entry['path']
            with closing(sqlite3.connect(work / 'versions.sqlite')) as db:
                self.assertEqual(entry['repository'], db.execute('SELECT url FROM repositories').fetchone()[0])
            history = (work / 'index.html').read_text()
            self.assertIn('data-worker="../../search-worker.js"', history)
            self.assertIn('src="../../search.js"', history)
            self.assertRegex(history, r'data-database="versions\.[a-f0-9]{64}\.sqlite\.gz"')
            self.assertTrue((work / 'versions.sqlite').is_file())
            self.assertFalse((work / 'browse/index.html').exists())
            self.assertIn('data-static-heading="Versions"', history)
            self.assertIn('aria-label="Breadcrumb"', history)
            manuscript = work / 'editions' / self.latest / 'read/index.html'
            self.assertRegex(manuscript.read_text(), r'<a href="contents.html"[^>]*><svg class="guavajam-icon guavajam-icon-list-tree"')
            self.assertRegex(manuscript.read_text(), r'<a href="passages.html"[^>]*><svg class="guavajam-icon guavajam-icon-book-open-text"')


    def test_standalone_updater_rebuilds_when_external_runtime_changes(self):
        runtime = self.root / 'runtime'
        runtime.mkdir()
        (runtime / 'index.mjs').write_text('export default function fixture() {}')
        (runtime / 'sqlite3.wasm').write_bytes(b'\0asm')
        output = self.root / 'periodic'
        with patch.dict('os.environ', {'GUAVAJAM_SQLITE_WASM_DIR': str(runtime)}):
            first = update(self.publisher.repo, output, no_fetch=True, **self.publisher.trust)
            self.assertEqual('updated', first['status'])
            original = (output / 'current').readlink()
            self.assertEqual('unchanged', update(self.publisher.repo, output, no_fetch=True,
                                                **self.publisher.trust)['status'])
            (runtime / 'sqlite3.wasm').write_bytes(b'\0asmchanged')
            self.assertEqual('updated', update(self.publisher.repo, output, no_fetch=True,
                                              **self.publisher.trust)['status'])
            self.assertNotEqual(original, (output / 'current').readlink())
            self.assertEqual(b'\0asmchanged', (output / 'current/sqlite3.wasm').read_bytes())


if __name__ == '__main__':
    unittest.main(verbosity=2)
