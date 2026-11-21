"""Source locators are a bounded, formally attributed static index."""
from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest

from guavajam.locators import write_locators


class Links(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.targets = []
        self.entries = 0
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'a' and 'href' in values:
            self.targets.append(values['href'])
        if tag == 'li' and values == {}:
            self.entries += 1  # breadcrumb li are not significant; count in static body separately


class LocatorsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'sources' / 'fieldwork' / 'editions' / ('a' * 40)
        self.root.mkdir(parents=True)
        self.crumbs = (('../../../../index.html', 'GuavaJam'),
                       ('../../index.html', 'Shoreline <research>'),
                       ('read/index.html', 'v9'))

    def render(self, facts):
        write_locators(self.root, facts=facts, title='Shoreline <research>', ancestors=self.crumbs)

    def test_exact_metadata_escaped_and_no_script(self):
        self.render({'subjects': [
            {'subject': 'Coast & sea', 'subject_scheme': 'Fields <international>',
             'scheme_uri': 'https://example.invalid/schema?x=1&y=2',
             'value_uri': 'urn:example:coast', 'classification_code': 'D4', 'language': 'en'},
            {'subject': 'Coast & sea', 'subject_scheme': 'Local taxonomy'}],
            'keywords': ['Sediments', 'Hydrology & marine']})
        html = (self.root / 'locators.html').read_text()
        self.assertEqual(4, html.count('<li><article>'))
        self.assertEqual(2, html.count('<p class="guavajam-locator-kind">Subject</p>'))
        self.assertEqual(2, html.count('<p class="guavajam-locator-kind">Tag</p>'))
        self.assertIn('<h2>Coast &amp; sea</h2>', html)
        self.assertIn('Coast &amp; sea', html)
        self.assertIn('Fields &lt;international&gt;', html)
        self.assertIn('https://example.invalid/schema?x=1&amp;y=2', html)
        self.assertIn('<dt>Classification code</dt><dd>D4</dd>', html)
        self.assertIn('Hydrology &amp; marine', html)
        self.assertIn('aria-label="Breadcrumb"', html)
        self.assertIn('href="read/index.html">v9</a>', html)
        self.assertNotIn('<script', html)
        self.assertIn("script-src 'none'", html)
        self.assertNotIn('guavajam-search', html.split('</style>', 1)[1])

    def test_single_unpaged_list_preserves_source_order_and_type(self):
        self.render({'subjects': [{'subject': f'Field-{i:02d}', 'value_uri': f'urn:field:{i}'}
                                  for i in range(35)],
                     'keywords': [f'Label-{i:02d}' for i in range(18)]})
        html = (self.root / 'locators.html').read_text()
        self.assertEqual(53, html.count('<li><article>'))
        self.assertLess(html.index('<h2>Field-00</h2>'), html.index('<h2>Label-00</h2>'))
        self.assertEqual(1, html.count('<h1 aria-current="page">Locators</h1>'))
        self.assertNotIn('Directory pages', html)
        self.assertFalse((self.root / 'locators/page/2.html').exists())
        self.assertNotIn('<script', html)
        self.assertIn('href="read/index.html">v9</a>', html)

    def test_empty_declared_locators_have_a_small_human_page(self):
        self.render({'title': 'Shoreline', 'subjects': [], 'keywords': []})
        html = (self.root / 'locators.html').read_text()
        self.assertIn('No subjects or tags were declared', html)
        self.assertNotIn('aria-label="Directory pages"', html)
        self.assertEqual(0, html.count('<li><article>'))


if __name__ == '__main__':
    unittest.main()
