"""Citation directories stay bounded and preserve exact source identities."""
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
import unittest

from guavajam.citation_pages import add_passage_links, build_citation_pages
from guavajam_core.catalog import CatalogError


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
        self.editions = [(self.sha, 'Edition 1', '2026-11-20', 'Publish')]
        # Deliberately nonalphabetical, including valid source IDs that also
        # name generated landmarks and search controls.
        self.anchors = ['z-opening', 'main', 'guavajam-query', *[f'passage-{n}' for n in range(39, -1, -1)]]
        self.documents = {
            'content/manuscript.md': {'view': 'index.html', 'title': 'Coastal <notes>', 'anchors': self.anchors},
            'content/supporting/note.md': {'view': 'supporting/note.html', 'title': 'Evidence & records',
                                          'anchors': ['42']},
            'content/supporting/empty.md': {'view': 'supporting/empty.html', 'title': 'Unanchored note', 'anchors': []}}
        view = {'facts': {'title': 'Coastal <notes>', 'author': 'Mara & Lee'},
                'documents': self.documents, 'files': {}}
        nav = {'documents': {source: {**document, 'anchors': [
            {'anchor': anchor, 'position': n, 'title': 'Source <title>',
             'scope': 'quotation:42' if anchor == '42' else 'document'}
            for n, anchor in enumerate(document['anchors'])]} for source, document in self.documents.items()}}
        proof = {'schema': 'guavajam.source-citation.v1', 'hash_algorithm': 'sha1',
                 'revision': self.sha, 'manuscript': 'content/manuscript.md',
                 'documents': {source: sorted(document['anchors']) for source, document in self.documents.items()}}
        for target, data in ((self.read / 'VIEW.json', view), (self.read / 'NAV.json', nav),
                             (self.read.parent / 'SOURCE.json', proof)):
            target.write_text(json.dumps(data))

    def build(self, download=None):
        build_citation_pages(self.output, self.editions, collection=True, runtime='../../', download=download)

    def test_static_pages_bound_source_order_and_link_exact_canonical_downloads(self):
        self.build()
        pages = ['cite.html', 'cite/page/2.html', 'cite/page/3.html']
        entries = []
        for filename, count in zip(pages, [20, 20, 3]):
            html = (self.read / filename).read_text()
            parsed = Markup(html)
            self.assertEqual(count, len(parsed.entries))
            self.assertEqual(len(parsed.ids), len(set(parsed.ids)))
            entries.extend(parsed.entries)
            self.assertNotIn('<script', html)
            self.assertNotIn('name="q"', html)
            self.assertIn("script-src 'none'", html)
            self.assertIn('Mara &amp; Lee. <cite>Coastal &lt;notes&gt;</cite>. Edition 1, 2026-11-20.', html)
            self.assertIn('Return to reading', html)
            self.assertIn('Download exact GFM source', html)
        self.assertEqual(['citation:' + anchor for anchor in self.anchors], entries)
        second = Markup((self.read / pages[1]).read_text())
        self.assertIn('../../cite.html', second.links)
        self.assertIn('./3.html', second.links)
        self.assertIn('../../index.html#' + self.anchors[20], second.links)
        self.assertIn('../passage/' + self.anchors[20] + '.json', second.links)
        # An entry points at the source ID, never its HTML citation fragment or
        # a mutable viewer URL. JSON keeps Core's canonical shape exactly.
        record = json.loads((self.read / 'cite/passage' / (self.anchors[20] + '.json')).read_text())
        self.assertEqual({'schema': 'guavajam.source-citation.v1', 'hash_algorithm': 'sha1',
                          'revision': self.sha, 'manuscript': 'content/manuscript.md',
                          'anchor': self.anchors[20]}, record)
        view = json.loads((self.read / 'VIEW.json').read_text())
        for name, digest in view['files'].items():
            self.assertEqual(hashlib.sha256((self.read / name).read_bytes()).hexdigest(), digest)
        self.assertEqual(43 + 1, len(list(self.read.rglob('passage/*.json'))))

    def test_supporting_quotation_and_document_without_anchors(self):
        self.build()
        html = (self.read / 'supporting/note.cite.html').read_text()
        self.assertIn('Evidence &amp; records</cite>. In <cite>Coastal &lt;notes&gt;</cite>', html)
        self.assertIn('Quoted passage', html)
        self.assertIn('id="citation:42"', html)
        self.assertIn('href="note.html#42"', html)
        cited = json.loads((self.read / 'supporting/note.cite/passage/42.json').read_text())
        self.assertEqual('content/supporting/note.md', cited['document'])
        self.assertEqual('42', cited['anchor'])
        empty = (self.read / 'supporting/empty.cite.html').read_text()
        self.assertIn('Cite this document as a whole', empty)
        self.assertEqual([], Markup(empty).entries)
        self.assertIn('href="empty.citation.json" download', empty)

    def test_deep_sqlite_page_uses_fixed_scope_and_only_its_static_slice(self):
        self.build(download={'url': 'work.hash.sqlite.gz', 'encoding': 'gzip', 'size': 1234, 'transfer_size': 567})
        html = (self.read / 'cite/page/3.html').read_text()
        self.assertIn('data-directory="citations" data-lazy="interaction"', html)
        self.assertIn('data-root="../../../../../"', html)
        self.assertIn('data-worker="../../../../../../../search-worker.js"', html)
        self.assertIn('name="edition" value="' + self.sha + '"', html)
        self.assertIn('name="document" value="content/manuscript.md"', html)
        self.assertIn('name="kind" value="passages"', html)
        self.assertNotIn('citation:z-opening', html)
        self.assertEqual(3, len(Markup(html).entries))

    def test_reader_insertion_preserves_source_and_ignores_generated_ids(self):
        html = ('<html><body><h1 id="main">Source &amp; title '
                '<a href="#main" class="guavajam-permalink">¶</a></h1>\n'
                '<p id="generated">Footnote <a href="#generated" class="guavajam-permalink">¶</a></p>'
                '</body></html>')
        result = add_passage_links(html, 'supporting/note.html', [{'anchor': 'main', 'position': 20}])
        expected = ('<a class="guavajam-passage-cite" lang="en" '
                    'href="note.cite/page/2.html#citation:main" '
                    'aria-label="Cite committed source passage main">Cite</a>')
        self.assertEqual(html, result.replace(expected, ''))
        self.assertEqual(1, result.count('class="guavajam-passage-cite"'))
        with self.assertRaisesRegex(CatalogError, 'lost a declared source permalink'):
            add_passage_links(html, 'index.html', [{'anchor': 'missing', 'position': 0}])
        with self.assertRaisesRegex(CatalogError, 'duplicate generated source permalink'):
            add_passage_links(html.replace('</h1>', '<a href="#main" class="guavajam-permalink">¶</a></h1>'),
                              'index.html', [{'anchor': 'main', 'position': 0}])


if __name__ == '__main__':
    unittest.main()
