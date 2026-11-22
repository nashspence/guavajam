"""Document titles and source quotations retain native reading semantics."""
from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
import unittest

from a_guavajam_static_website_generator.renderer import render
from a_guavajam_static_website_generator.site import build_site
from guavajam_test_support.discovery_fixture import Publisher


class Structure(HTMLParser):
    """Record element ancestry and heading text without a browser dependency."""
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
            'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, html):
        super().__init__()
        self.nodes = []
        self.stack = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = {'tag': tag, 'attrs': dict(attrs), 'ancestors': tuple(self.stack), 'text': ''}
        self.nodes.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]['tag'] == tag:
                del self.stack[index:]
                break

    def handle_data(self, text):
        for node in self.stack:
            if node['tag'] in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
                node['text'] += text

    def tags(self, tag):
        return [node for node in self.nodes if node['tag'] == tag]

    def classes(self, name):
        return [node for node in self.nodes if name in node['attrs'].get('class', '').split()]


class DocumentSemanticsTests(unittest.TestCase):
    def test_signed_draft_and_site_readers_have_one_real_title_and_keep_source_quotations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            publisher = Publisher(root)
            publisher.source('Research & records', '''A readable opening.

> A quoted observation.

<a id="guavajam-main"></a>
## A source-owned landmark name

This source anchor must remain addressable.

<a id="guavajam-citation-title"></a>
## A source-owned title name

This source identity must not become a duplicate generated ID.''')
            supporting = publisher.repo / 'content/supporting/note.md'
            supporting.parent.mkdir()
            supporting.write_text('---\ntitle: Supporting evidence\nabstract: A literal <supporting> summary.\n---\n\n'
                                  '<a name="guavajam-main"></a>\n\nA source claim.\n')
            manuscript_source = (publisher.repo / 'content/manuscript.md').read_bytes()
            supporting_source = supporting.read_bytes()
            revision = publisher.publish('Review document semantics')
            signed, draft, site = (root / name for name in ('signed', 'draft', 'site'))
            render(publisher.repo, revision, signed, **publisher.trust)
            render(publisher.repo, revision, draft, preview=True)
            build_site(publisher.repo, site, **publisher.trust)
            published = site / 'editions' / revision / 'read'

            for mode, directory in (('signed', signed), ('draft', draft), ('site', published)):
                for relative, title in (('index.html', 'Research & records'),
                                        ('supporting/note.html', 'Supporting evidence')):
                    with self.subTest(mode=mode, document=relative):
                        page = Structure((directory / relative).read_text())
                        self.assertEqual(1, len(page.tags('main')))
                        self.assertEqual(1, len(page.classes('guavajam-edition')))
                        self.assertEqual(1, len(page.tags('h1')))
                        heading = page.tags('h1')[0]
                        article = page.classes('guavajam-edition')[0]
                        self.assertIn(article, heading['ancestors'])
                        self.assertIn(page.tags('main')[0], heading['ancestors'])
                        self.assertEqual(title, heading['text'])
                        self.assertFalse(any(node['tag'] == 'p' for node in heading['ancestors']))
                        identifiers = [node['attrs']['id'] for node in page.nodes if node['attrs'].get('id')]
                        self.assertEqual(len(identifiers), len(set(identifiers)))
                        skip = page.classes('guavajam-skip')[0]
                        self.assertEqual('#' + page.tags('main')[0]['attrs']['id'], skip['attrs']['href'])
                        citations = page.classes('guavajam-citation')
                        self.assertEqual(1, len(citations))
                        self.assertIn(citations[0], heading['ancestors'])
                        for citation in citations:
                            self.assertEqual('div', citation['tag'])
                            self.assertEqual('group', citation['attrs'].get('role'))
                            self.assertEqual('Git source citation', citation['attrs'].get('aria-label'))
                            self.assertIn(article, citation['ancestors'])
                            links = [node for node in page.tags('a') if citation in node['ancestors']]
                            self.assertEqual(1, len(links))
                            self.assertIn('download', links[0]['attrs'])
                        divider, = page.classes('guavajam-document-divider')
                        self.assertEqual('hr', divider['tag'])
                        self.assertIn(article, divider['ancestors'])
                        self.assertNotIn('id', divider['attrs'])
                        self.assertLess(page.nodes.index(citations[0]), page.nodes.index(divider))
                        for abstract in page.classes('guavajam-abstract'):
                            self.assertLess(page.nodes.index(abstract), page.nodes.index(divider))
                        source_claim = next(node for node in page.nodes if node['attrs'].get('id') == 'guavajam-main')
                        self.assertLess(page.nodes.index(divider), page.nodes.index(source_claim))
                        if relative == 'index.html':
                            self.assertEqual(title, heading['text'])
                            self.assertNotIn('id', heading['attrs'])
                            self.assertIn(citations[0], heading['ancestors'])
                            self.assertIn('guavajam-main', identifiers)
                            self.assertIn('guavajam-citation-title', identifiers)
                            self.assertEqual(1, len(page.tags('blockquote')))
                            self.assertNotIn(citations[0], page.tags('blockquote')[0]['ancestors'])
                        else:
                            self.assertNotIn('id', heading['attrs'])
                            self.assertIn('guavajam-main', identifiers)
                            self.assertEqual(1, len(page.classes('guavajam-citation-title')))
                            self.assertEqual(1, len(page.classes('guavajam-abstract')))
                            self.assertIn('A literal &lt;supporting&gt; summary.', (directory / relative).read_text())
                            self.assertEqual('Supporting evidence', next(node['attrs']['content'] for node in page.tags('meta') if node['attrs'].get('name') == 'citation_title'))
                            cited = json.loads((directory / 'supporting/note.citation.json').read_text())
                            self.assertEqual('content/supporting/note.md', cited['document'])
                            self.assertEqual(supporting_source, (directory / 'supporting/note.gfm').read_bytes())

            self.assertEqual(manuscript_source, (published / 'source.gfm').read_bytes())
            self.assertEqual(supporting_source, (published / 'supporting/note.gfm').read_bytes())


if __name__ == '__main__':
    unittest.main()
