"""One version metadata tree retains all verified front matter and provenance."""
from pathlib import Path
import tempfile
import unittest

from a_guavajam_static_website_generator.metadata import write_metadata


class MetadataTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'sources' / 'fieldwork' / 'editions' / ('a' * 40)
        self.root.mkdir(parents=True)
        self.crumbs = (('../../../../index.html', 'GuavaJam'),
                       ('../../index.html', 'Shoreline <research>'),
                       ('read/index.html', 'v9'))

    def render(self, facts):
        write_metadata(self.root, facts=facts, title='Shoreline <research>', ancestors=self.crumbs,
                       revision='a' * 40, published='2026-11-21', message='Amend <evidence>\n\nDetails',
                       committer={'name': 'Morgan & Lee', 'email': 'morgan@example.org'},
                       repository='https://example.org/work.git')
        return (self.root / 'metadata.html').read_text()

    def test_one_page_contains_publication_provenance_rights_and_locators(self):
        html = self.render({'title': 'Shoreline <research>', 'author': 'Mara & Lee', 'language': 'en',
                            'rights': 'CC-BY-4.0', 'abstract': 'Precise <claims> & interpretation.',
                            'identifiers': {'doi': '10.1234/example'}, 'subjects': [
            {'subject': 'Coast & sea', 'subject_scheme': 'Fields <international>',
             'scheme_uri': 'https://example.invalid/schema?x=1&y=2',
             'value_uri': 'urn:example:coast', 'classification_code': 'D4', 'language': 'en'},
            {'subject': 'Coast & sea', 'subject_scheme': 'Local taxonomy'}],
            'keywords': ['Sediments', 'Hydrology & marine']})
        for section in ('Publication', 'Provenance', 'Rights', 'Locators'):
            self.assertIn(f'<h2 id="{section.lower()}-heading">{section}</h2>', html)
        self.assertIn('Shoreline &lt;research&gt;', html)
        self.assertIn('Mara &amp; Lee', html)
        self.assertIn('Precise &lt;claims&gt; &amp; interpretation.', html)
        self.assertIn('10.1234/example', html)
        self.assertIn('<dt>Rights</dt><dd>CC-BY-4.0</dd>', html)
        self.assertIn('Git commit</dt><dd>' + 'a' * 40, html)
        self.assertIn('Amend &lt;evidence&gt;', html)
        self.assertIn('href="SOURCE.json" download', html)
        self.assertIn('SOURCE.json <span class="guavajam-link-mime">(application/json)</span>', html)
        self.assertIn('href="CITATION.json" download',html)
        self.assertIn('CITATION.json <span class="guavajam-link-mime">(application/json)</span>',html)
        self.assertEqual(2, html.count('guavajam-icon-file-json'))
        self.assertEqual(2, html.count('type="application/json"'))
        self.assertIn('href="mailto:morgan@example.org"',html)
        self.assertIn('aria-label="Morgan &amp; Lee"',html)
        self.assertIn('>Morgan &amp; Lee</span>',html)
        self.assertIn('guavajam-icon-mail',html)
        self.assertIn('href="source.bundle" download', html)
        self.assertNotIn('href="https://example.org/work.git"', html)
        self.assertEqual(4, html.count('<li><article class="guavajam-metadata-leaf">'))
        self.assertEqual(2, html.count('<p class="guavajam-metadata-kind">Subject</p>'))
        self.assertEqual(2, html.count('<p class="guavajam-metadata-kind">Tag</p>'))
        self.assertIn('<h3>Coast &amp; sea</h3>', html)
        self.assertIn('Fields &lt;international&gt;', html)
        self.assertIn('https://example.invalid/schema?x=1&amp;y=2', html)
        self.assertIn('<dt>Classification code</dt><dd>D4</dd>', html)
        self.assertIn('Hydrology &amp; marine', html)
        self.assertIn('href="read/index.html"', html)
        self.assertIn('aria-label="v9"', html)
        self.assertIn('aria-label="Breadcrumb"', html)
        self.assertIn('<h1 aria-current="page">Metadata</h1>', html)
        self.assertEqual(1, html.count('<h1 '))
        self.assertNotIn('<script', html)
        self.assertIn("script-src 'none'", html)
        for old in ('locators.html', 'verify.html', 'license.html'):
            self.assertFalse((self.root / old).exists())

    def test_locators_are_one_unpaged_list_with_source_order_and_type(self):
        html = self.render({'author': 'Someone', 'subjects': [
            {'subject': f'Field-{i:02d}', 'value_uri': f'urn:field:{i}'} for i in range(35)],
            'keywords': [f'Label-{i:02d}' for i in range(18)]})
        self.assertEqual(53, html.count('<li><article class="guavajam-metadata-leaf">'))
        self.assertLess(html.index('<h3>Field-00</h3>'), html.index('<h3>Label-00</h3>'))
        self.assertNotIn('Directory pages', html)
        self.assertFalse((self.root / 'metadata/page/2.html').exists())

    def test_empty_locators_and_undeclared_rights_have_meaningful_text(self):
        html = self.render({'author': 'Someone', 'subjects': [], 'keywords': []})
        self.assertIn('No subjects or tags declared.', html)
        self.assertIn('No rights statement declared', html)
        self.assertEqual(0, html.count('<li><article class="guavajam-metadata-leaf">'))


if __name__ == '__main__':
    unittest.main()
