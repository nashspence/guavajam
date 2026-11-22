"""One edition-wide Passages index keeps exact citation identities and bounded HTML."""
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from a_guavajam_static_website_generator.icons import icon
from a_guavajam_static_website_generator.citation_pages import add_passage_links, build_citation_pages
from a_guavajam_static_website_generator.citation_routes import (citation_fragment, citation_href, citation_page,
                                      passage_json, relative_href)
from guavajam.catalog import CatalogError


class Markup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.ids, self.entries, self.links = [], [], []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if 'guavajam-citation-entry' in attrs.get('class', '').split():
            self.entries.append(attrs['id'])
        if tag == 'a' and 'href' in attrs:
            self.links.append(attrs['href'])


class CitationPageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name) / 'work'
        self.sha = 'a' * 40
        self.read = self.output / 'editions' / self.sha / 'read'
        self.read.mkdir(parents=True)
        self.editions = [(self.sha, 'v1', '2026-11-20', 'Publish')]
        self.anchors = ['z-opening', 'main', 'guavajam-query', *[f'passage-{n}' for n in range(39, -1, -1)]]
        self.documents = {
            'content/manuscript.md': {'view': 'index.html', 'title': 'Coastal <notes>', 'anchors': self.anchors},
            'content/supporting/note.md': {'view': 'supporting/note.html', 'title': 'Evidence & records',
                                          'anchors': ['42', 'main']},
            'content/supporting/empty.md': {'view': 'supporting/empty.html', 'title': 'Unanchored note', 'anchors': []}}
        view = {'facts': {'title': 'Coastal <notes>', 'author': 'Mara & Lee'},
                'documents': self.documents, 'files': {}}
        nav = {'schema': 'guavajam.navigation.v1', 'canonical': {'manuscript': 'content/manuscript.md'},
               'documents': {source: {**document, 'anchors': [
                   {'anchor': anchor, 'position': n, 'title': 'Source <title>',
                    'scope': 'quotation:42' if anchor == '42' else 'document'}
                   for n, anchor in enumerate(document['anchors'])]}
                   for source, document in self.documents.items()}}
        proof = {'schema': 'guavajam.source-citation.v1', 'hash_algorithm': 'sha1',
                 'revision': self.sha, 'manuscript': 'content/manuscript.md',
                 'documents': {source: sorted(document['anchors']) for source, document in self.documents.items()}}
        for target, data in ((self.read / 'VIEW.json', view), (self.read / 'NAV.json', nav),
                             (self.read.parent / 'SOURCE.json', proof)):
            target.write_text(json.dumps(data))

    def build(self, download=None):
        build_citation_pages(self.output, self.editions, collection=True, runtime='../../', download=download)

    def test_citation_routes_remain_relative_without_host_cwd(self):
        cases = (
            ('passages.html', '../../../', '../../..'),
            ('passages/page/2.html', '../../../', '../../../../..'),
            ('passages/page/2.html', 'index.html#main', '../../index.html#main'),
            ('supporting/note.html', 'passages/page/2.html#citation:x',
             '../passages/page/2.html#citation:x'),
        )
        for directory in ('/', '/state', '/home/runner/work/guavajam/guavajam'):
            with patch('os.getcwd', return_value=directory):
                for filename, target, expected in cases:
                    with self.subTest(directory=directory, filename=filename):
                        self.assertEqual(expected, relative_href(filename, target))
        self.assertNotEqual(citation_fragment('content/manuscript.md', 'main'),
                            citation_fragment('content/supporting/note.md', 'main'))
        self.assertEqual('passages/page/3.html', citation_page(40))
        self.assertTrue(citation_href('content/manuscript.md', 40, 'main').startswith('passages/page/3.html#citation:'))
        with self.assertRaises(ValueError):
            citation_page(-1)
        with self.assertRaises(ValueError):
            citation_page(True)

    def test_all_documents_share_one_bounded_index_with_unique_fragments(self):
        self.build()
        pages = ['passages.html', 'passages/page/2.html', 'passages/page/3.html']
        seen = []
        for filename, count in zip(pages, [20, 20, 5]):
            html = (self.read / filename).read_text()
            parsed = Markup(html)
            self.assertEqual(count, len(parsed.entries))
            self.assertEqual(len(parsed.ids), len(set(parsed.ids)))
            seen.extend(parsed.entries)
            self.assertNotIn('<script', html)
            self.assertIn("script-src 'none'", html)
            self.assertIn('Mara &amp; Lee. <cite>Coastal &lt;notes&gt;</cite>. v1, 2026-11-20.', html)
            self.assertIn('aria-current="page">Passages</h1>', html)
            self.assertEqual(count, html.count('<div class="guavajam-citation"'))
        expected = ([citation_fragment('content/manuscript.md', anchor) for anchor in self.anchors] +
                    [citation_fragment('content/supporting/note.md', anchor) for anchor in ('42', 'main')])
        self.assertEqual(expected, seen)
        # One human citation directory. Document-local JSON is preserved, not
        # parallel per-document citation HTML pages.
        self.assertFalse((self.read / 'cite.html').exists())
        self.assertFalse((self.read / 'cite/page/3.html').exists())
        self.assertFalse((self.read / 'supporting/note.cite.html').exists())
        self.assertIn('../../passages.html', Markup((self.read / pages[1]).read_text()).links)
        self.assertIn('./3.html', Markup((self.read / pages[1]).read_text()).links)
        self.assertIn('../../index.html#' + self.anchors[20], Markup((self.read / pages[1]).read_text()).links)
        for source, anchor in (('content/manuscript.md', 'main'), ('content/supporting/note.md', 'main')):
            record = json.loads((self.read / passage_json('index.html' if source == 'content/manuscript.md' else 'supporting/note.html', anchor)).read_text())
            self.assertEqual(self.sha, record['revision'])
            self.assertEqual(anchor, record['anchor'])
            self.assertEqual('content/manuscript.md', record['manuscript'])
            if source != record['manuscript']:
                self.assertEqual(source, record['document'])
            else:
                self.assertNotIn('document', record)
        view = json.loads((self.read / 'VIEW.json').read_text())
        for filename, digest in view['files'].items():
            self.assertEqual(hashlib.sha256((self.read / filename).read_bytes()).hexdigest(), digest)
        self.assertEqual(45, len(list(self.read.rglob('cite/passage/*.json')) +
                                 list(self.read.rglob('supporting/note.cite/passage/*.json'))))

    def test_supporting_document_entries_preserve_quotation_provenance(self):
        self.build()
        html = (self.read / 'passages/page/3.html').read_text()
        self.assertIn('Evidence &amp; records</cite>. In <cite>Coastal &lt;notes&gt;</cite>', html)
        self.assertNotIn('aria-label="Passage source"', html)
        self.assertIn('href="../../supporting/note.html#42"', html)
        self.assertIn(citation_fragment('content/supporting/note.md', '42'), html)
        self.assertNotIn('Unanchored note', html)

    def test_deep_sqlite_page_scopes_to_version_with_supporting_filter(self):
        self.build(download={'url': 'work.hash.sqlite.gz', 'encoding': 'gzip', 'size': 1234, 'transfer_size': 567})
        html = (self.read / 'passages/page/3.html').read_text()
        self.assertIn('data-directory="citations"', html)
        self.assertIn('id="guavajam-index-search" hidden', html)
        self.assertIn('Indexed Search <span class="guavajam-index-size"', html)
        self.assertNotIn('Enable search', html)
        self.assertIn('data-static-heading="Passages">Passages</h2>', html)
        self.assertIn('Showing static page 3 of 3.', html)
        self.assertEqual(5, html.split('id="guavajam-static-section"', 1)[-1].count('<div class="guavajam-citation"'))
        self.assertIn('data-root="../../../../../"', html)
        self.assertIn('data-worker="../../../../../../../search-worker.js"', html)
        self.assertIn('name="edition" value="' + self.sha + '"', html)
        self.assertIn('name="supporting"', html)
        self.assertIn('Supporting documents', html)
        self.assertIn('name="kind" value="passages"', html)
        self.assertNotIn('name="document"', html)
        self.assertNotIn(citation_fragment('content/manuscript.md', 'z-opening'), html)
        self.assertEqual(5, len(Markup(html).entries))

    def test_reader_insertion_preserves_source_and_ignores_generated_ids(self):
        html = ('<html><body><h1 id="main">Source &amp; title '
                '<a href="#main" class="guavajam-permalink">¶</a></h1>\n'
                '<p id="generated">Footnote <a href="#generated" class="guavajam-permalink">¶</a></p>'
                '</body></html>')
        target = citation_href('content/supporting/note.md', 43, 'main')
        result = add_passage_links(html, 'supporting/note.html',
                                   'content/supporting/note.md', [{'anchor': 'main', 'position': 1}], 42)
        expected = ('<a class="guavajam-passage-cite" lang="en" href="' +
                    relative_href('supporting/note.html', target) + '" '
                    'aria-label="Cite committed source passage main">' + icon('quote') + '</a>')
        self.assertEqual(html, result.replace(expected, ''))
        self.assertEqual(1, result.count('class="guavajam-passage-cite"'))
        with self.assertRaisesRegex(CatalogError, 'lost a declared source permalink'):
            add_passage_links(html, 'index.html', 'content/manuscript.md',
                              [{'anchor': 'missing', 'position': 0}], 0)
        with self.assertRaisesRegex(CatalogError, 'duplicate generated source permalink'):
            add_passage_links(html.replace('</h1>', '<a href="#main" class="guavajam-permalink">¶</a></h1>'),
                              'index.html', 'content/manuscript.md', [{'anchor': 'main', 'position': 0}], 0)


if __name__ == '__main__':
    unittest.main()
