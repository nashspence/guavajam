"""Edition/document HTML pages are bounded; SQLite remains the only enhancement."""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
import re
from pathlib import Path
import sqlite3
import tempfile
import unittest

from a_guavajam_static_website_generator.directory_pages import PAGE_SIZE, _static_pager, build_directories, _write_contents


class Markup(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.links = []
        self.ids = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag == 'a' and data.get('href'):
            self.links.append(data['href'])
        if data.get('id'):
            self.ids.append(data['id'])


class DirectoryPageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name) / 'sources' / 'work'
        self.output.mkdir(parents=True)
        self.editions = []
        for n in range(1, 28):
            sha = f'{n:040x}'
            date = f'2026-09-{(n % 27) + 1:02d}'
            message = f'Review station {n:02d} and historical notes'
            if n == 5:
                message += '\n\nCorrected an archive citation.\nCoordinated the maps.'
            self.editions.append((sha, f'v{n}', date, message))
            read = self.output / 'editions' / sha / 'read'
            read.mkdir(parents=True)
            documents = {
                'content/manuscript.md': {'title': 'Shoreline work', 'view': 'index.html', 'anchors': []}}
            if n == 27:
                for i in range(1, 53):
                    slug = f'field-{i:02d}'
                    documents[f'content/supporting/{slug}.md'] = {
                        'title': f'Field observation {i:02d}', 'view': f'supporting/{slug}.html', 'anchors': []}
            view = {'facts': {'title': 'Shoreline work', 'author': 'Mara Levin'},
                    'documents': documents, 'files': {'NAV.json': 'old'}}
            (read / 'VIEW.json').write_text(json.dumps(view))
            (read / 'NAV.json').write_text(json.dumps({'canonical': {'manuscript': 'content/manuscript.md'}, 'documents': documents}))
            (read.parent / 'CITATION.json').write_text(json.dumps({
                'revision': sha, 'manuscript': 'content/manuscript.md'}))
            (read.parent / 'SOURCE.json').write_text(json.dumps({
                'schema': 'guavajam.source-citation.v1', 'hash_algorithm': 'sha1',
                'revision': sha, 'manuscript': 'content/manuscript.md',
                'documents': {path: [] for path in documents}}))
        self.database = self.output / 'work.sqlite'
        with sqlite3.connect(self.database) as db:
            db.execute('CREATE TABLE representative (x INTEGER)')
        # Presence of the official search controller is sufficient for the
        # page generator to expose controls; it does not create a new engine.
        (self.output / 'search.js').write_text('// supplied by normal build_work_index')

    def test_generation_bounds_every_page_and_links_to_every_record(self):
        build_directories(self.output, self.editions, collection=True,
                          search_assets='../../', database=self.database)
        history = self.output
        history_pages = [history / 'index.html', history / 'page/2.html']
        old = []
        for number, location in enumerate(history_pages, 1):
            markup = location.read_text()
            self.assertIn('aria-label="Breadcrumb"', markup)
            self.assertIn('Search versions and commit messages', markup)
            self.assertIn('id="guavajam-index-search" hidden', markup)
            self.assertIn('class="guavajam-index-search guavajam-directory-enhancement"', markup)
            self.assertIn('Indexed Search <span class="guavajam-index-size"', markup)
            self.assertNotIn('Enable search', markup)
            self.assertNotIn('data-lazy=', markup)
            self.assertIn('<summary>Filters and sort</summary>', markup)
            self.assertIn('name="edition" value="all"', markup)
            self.assertIn('data-worker="../../search-worker.js"' if number == 1
                          else 'data-worker="../../../search-worker.js"', markup)
            # Simple labels, reading-first links, textual verification and messages.
            rows = re.findall(r'<li><article><h3[^>]*><a [^>]*>(v\d+)</a>', markup)
            self.assertEqual(20 if number == 1 else 7, len(rows))
            self.assertLessEqual(len(rows), PAGE_SIZE)
            old.extend(rows)
            self.assertIn('id="guavajam-search-prev"', markup)
            self.assertIn('href="editions/' + self.editions[-1][0] + '/metadata.html#provenance"', history_pages[0].read_text())
            self.assertIn('guavajam-record-list { list-style: none', markup)
            self.assertIn('data-static-heading="Versions"', markup)
            self.assertIn(f'Showing static page {number} of 2.', markup)
            self.assertIn('id="guavajam-search-spinner"', markup)
            self.assertNotIn('Verified versions, newest first', markup)
            self.assertIn('>Metadata</a>', markup)
            self.assertNotIn('<a class="guavajam-record-link"', markup)

            self.assertNotIn('listing.js', markup)
        self.assertEqual([f'v{n}' for n in range(27, 0, -1)], old)
        self.assertIn('Full commit message', history_pages[1].read_text())
        self.assertIn('href="page/2.html" rel="next"', history_pages[0].read_text())
        self.assertIn('href="../index.html" rel="prev"', history_pages[1].read_text())

        latest = self.output / 'editions' / self.editions[-1][0] / 'read'
        doc_pages = [latest / 'documents.html', latest / 'documents/page/2.html', latest / 'documents/page/3.html']
        totals = []
        for number, location in enumerate(doc_pages, 1):
            html = location.read_text()
            documents = re.findall(r'<li><article><h3[^>]*><a href="[^"]+">', re.search(
                r'<ul class="guavajam-document-list guavajam-record-list">(.*?)</ul>', html, re.S).group(1))
            totals.append(len(documents))
            self.assertLessEqual(len(documents), PAGE_SIZE)
            self.assertIn('value="documents"', html)
            self.assertIn('name="edition" value="' + self.editions[-1][0] + '"', html)
            self.assertNotIn('<div class="guavajam-citation"', html)
            self.assertIn('data-static-heading="Documents"', html)
            self.assertIn(f'Showing static page {number} of 3.', html)
            self.assertIn('id="guavajam-search-spinner"', html)
            self.assertIn('.guavajam-record-list { list-style: none;', html)
            self.assertIn('>v27</a>', html)

            self.assertNotIn('Manuscript and supporting documents in this edition.', html)
            self.assertNotIn('listing.js', html)
            self.assertEqual(1, html.count('<h1 aria-current="page">Documents</h1>'))
            if number == 1:
                self.assertIn('href="documents/page/2.html" rel="next"', html)
            if number == 2:
                self.assertIn('href="../../documents.html"', html)
            if number == 3:
                self.assertIn('href="2.html" rel="prev"', html)
        self.assertEqual([20, 20, 13], totals)
        view = json.loads((latest / 'VIEW.json').read_text())
        for location in doc_pages:
            relative = location.relative_to(latest).as_posix()
            self.assertEqual(hashlib.sha256(location.read_bytes()).hexdigest(), view['files'][relative])

    def test_standalone_work_pages_resolve_search_and_readers_from_the_work_root(self):
        build_directories(self.output, self.editions, collection=False,
                          search_assets='', database=self.database)
        for number, location in enumerate((self.output / 'index.html', self.output / 'page/2.html'), 1):
            html = location.read_text()
            prefix = '' if number == 1 else '../'
            self.assertIn('data-root="' + prefix + '"', html)
            self.assertIn('data-worker="' + prefix + 'search-worker.js"', html)
            self.assertIn('src="' + prefix + 'search.js"', html)
            self.assertRegex(html, 'data-database="' + re.escape(prefix) + r'work\.[a-f0-9]{64}\.sqlite\.gz"')
            self.assertIn('<title>Shoreline work' + ('' if number == 1 else ' — page 2') + ' — GuavaJam</title>', html)
            if number == 1:
                self.assertIn('<h1 aria-current="page">Shoreline work</h1>', html)
            else:
                self.assertIn('href="../index.html">Shoreline work</a>', html)
                self.assertIn('<h1 aria-current="page">Page 2</h1>', html)
            self.assertTrue(any(link.startswith(prefix + 'editions/') and link.endswith('/read/index.html')
                                for link in Markup(html).links))

    def test_static_only_mode_shows_native_paging_without_nonfunctional_controls(self):
        (self.output / 'search.js').unlink()
        build_directories(self.output, self.editions, collection=False,
                          search_assets='', database=None)
        history = (self.output / 'index.html').read_text()
        docs = (self.output / 'editions' / self.editions[-1][0] / 'read/documents.html').read_text()
        for html in (history, docs):
            self.assertNotIn('data-directory=', html)
            self.assertNotIn('<script', html)
            self.assertIn('rel="next"', html)
            self.assertNotIn('listing.js', html)

    def test_contents_uses_only_the_author_outline_and_exact_reader_anchors(self):
        read = self.output / 'editions' / self.editions[-1][0] / 'read'
        anchors = [
            {'anchor': 'opening', 'title': 'Opening <study>', 'kind': 'section', 'level': 2, 'scope': 'document'},
            {'anchor': 'detail', 'title': 'Detail & evidence', 'kind': 'section', 'level': 3, 'scope': 'document'},
            {'anchor': 'quoted', 'title': 'Quoted heading', 'kind': 'section', 'level': 2, 'scope': 'quotation:turn'},
            {'anchor': 'closing', 'title': 'Closing', 'kind': 'section', 'level': 2, 'scope': 'document'}]
        nav = json.loads((read / 'NAV.json').read_text())
        nav['documents']['content/manuscript.md']['anchors'] = anchors
        (read / 'NAV.json').write_text(json.dumps(nav))
        _write_contents(self.output, [self.editions[-1]], collection=True)
        html = (read / 'contents.html').read_text()
        links = Markup(html).links
        for anchor in ('opening', 'detail', 'closing'):
            self.assertIn('index.html#' + anchor, links)
        self.assertNotIn('index.html#quoted', links)
        self.assertIn('Opening &lt;study&gt;', html)
        self.assertIn('Detail &amp; evidence', html)
        self.assertIn('</a><ol><li><a href="index.html#detail">', html)
        self.assertIn('.guavajam-contents-list > ol > li { padding: 0; border: 0;', html)
        self.assertNotIn('border-bottom: 1px solid var(--guavajam-border); padding: .55rem 0;', html)
        self.assertIn('href="index.html">v1</a>', html)
        self.assertNotIn('<script', html)
        view = json.loads((read / 'VIEW.json').read_text())
        self.assertEqual(hashlib.sha256(html.encode()).hexdigest(), view['files']['contents.html'])

    def test_pager_never_emits_unbounded_page_link_lists(self):
        nav = _static_pager(2500, 5000, first_href='../index.html', page_prefix='')
        self.assertLess(nav.count('href='), 10)
        self.assertIn('Page 2500 of 5000', nav)
        self.assertIn('rel="prev"', nav)
        self.assertIn('rel="next"', nav)


if __name__ == '__main__':
    unittest.main()
