"""Exact signed editions rendered from Git, with immutable downloads and citations."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from a_guavajam_static_website_generator.site import build_site
from guavajam.publication_protocol import discover
from guavajam_test_support.discovery_fixture import Publisher


class CatalogBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.publisher = Publisher(self.root)
        self.output = self.root / 'site'

    def build(self, output=None, **options):
        return build_site(self.publisher.repo, output or self.output,
                          **self.publisher.trust, **options)

    def test_reader_and_draft_share_literal_abstract(self):
        import re
        import yaml
        from a_guavajam_static_website_generator.renderer import render
        from a_guavajam_static_website_generator.presentation import abstract_html
        path = self.publisher.repo / 'content/manuscript.md'
        _, header, body = path.read_text().split('---\n', 2)
        facts = yaml.safe_load(header)
        facts['abstract'] = 'Literal <claim> & *emphasis*.'
        path.write_text('---\n' + yaml.safe_dump(facts, allow_unicode=True, sort_keys=False) + '---\n' + body)
        revision = self.publisher.publish('Review the abstract')
        self.build()
        draft = self.root / 'draft'
        render(self.publisher.repo, revision, draft, preview=True)
        pattern = r'<div class="guavajam-abstract"[^>]*>.*?</div>'
        for page in [self.output / 'editions' / revision / 'read/index.html', draft / 'index.html']:
            markup = page.read_text()
            blocks = re.findall(pattern, markup, re.S)
            self.assertEqual([abstract_html(facts['abstract'])], blocks)
            self.assertEqual(1, markup.count('<hr class="guavajam-document-divider">'))
            self.assertLess(markup.index(blocks[0]), markup.index('<hr class="guavajam-document-divider">'))
            self.assertLess(markup.index('<hr class="guavajam-document-divider">'), markup.index('id="opening"'))
            self.assertNotIn('<claim>', markup)
            if page != draft / 'index.html':
                label = 'Version links'
                self.assertLess(markup.index('aria-label="' + label + '"'), markup.index('<div class="guavajam-citation'))
                self.assertLess(markup.index('<div class="guavajam-citation'), markup.index(blocks[0]))

    def test_latest_comes_from_history_and_message_is_plain_text(self):
        first = self.publisher.revisions[0]
        self.publisher.source('Revised Field Notes', 'A revised observation.')
        second = self.publisher.publish('Correct <a> heading\n\nExplain why <script> is literal.', date='2025-01-01T10:00:00Z')
        manifest = self.build()
        self.assertEqual([first, second], manifest['publications'])
        landing = (self.output / 'index.html').read_text()
        self.assertIn('Revised Field Notes', landing)
        self.assertIn('editions/' + second + '/read/index.html', landing)
        self.assertNotIn('Latest edition', landing)
        self.assertNotIn('href="editions/' + second + '/metadata.html"', landing)

        self.assertIn('<h1 aria-current="page">Revised Field Notes</h1>', landing)
        self.assertIn('data-static-heading="Versions"', landing)
        history = (self.output / 'index.html').read_text()
        self.assertIn('>v1</a>', history)
        self.assertIn('>v2</a>', history)
        self.assertIn('href="editions/' + first + '/read/index.html"', history)
        self.assertLess(history.index('>v2</a>'), history.index('>v1</a>'))
        self.assertNotIn('>Documents</a>', landing)
        self.assertIn('href="editions/' + second + '/metadata.html#provenance">Metadata</a>', history)
        edition = (self.output / 'editions' / second / 'metadata.html').read_text()
        self.assertIn('Explain why &lt;script&gt;', edition)
        self.assertNotIn('<script>', edition)
        self.assertIn('2025-01-01', edition)
        self.assertNotIn('class="guavajam-byline"', edition)
        self.assertNotIn('<section class="guavajam-directory"', edition)
        self.assertIn('href="SOURCE.json">SOURCE.json</a>', edition)
        self.assertIn('href="CITATION.json" download', edition)
        self.assertNotIn('href="read/documents.html">Documents</a>', edition)
        reader = (self.output / 'editions' / second / 'read/index.html').read_text()
        self.assertIn('href="documents.html">Documents</a>', reader)
        self.assertIn('href="../metadata.html">Metadata</a>', reader)
        self.assertNotIn('href="../verify.html"', reader)
        self.assertNotIn('href="../license.html"', reader)
        self.assertIn('href="passages.html">Passages</a>', reader)


    def test_manuscript_body_does_not_repeat_the_top_citation(self):
        import re
        self.publisher.source('Unique manuscript title', 'Body begins with the observation.')
        sha = self.publisher.publish('Review metadata-only manuscript')
        self.build()
        reader = self.output / 'editions' / sha / 'read'
        html = (reader / 'index.html').read_text()
        body = html.split('<body>', 1)[1]
        citations = re.findall(r'<div class="guavajam-citation".*?</a></div>', body, re.S)
        self.assertEqual(1, len(citations))
        self.assertIn('Example Author. <h1 class="guavajam-citation-title"><cite>Unique manuscript title</cite></h1>.', citations[0])
        remainder = body.replace(citations[0], '')
        article = re.search(r'<article.*?</article>', body, re.S).group()
        self.assertEqual(1, article.count('Unique manuscript title'))
        self.assertIn(citations[0], article)
        self.assertIn('index.html">Unique manuscript title</a>', remainder)
        self.assertNotIn('Example Author', remainder)
        self.assertEqual(1, body.count('<h1'))
        self.assertLess(body.index('<article'), body.index(citations[0]))
        raw = (reader / 'source.gfm').read_text()
        self.assertEqual(1, raw.count('Unique manuscript title'))
        self.assertEqual(1, raw.count('Example Author'))
        self.assertIn('## Opening', raw)
        self.assertIn('name="citation_title" content="Unique manuscript title"', html)

    def test_downloads_and_citations_are_exact_and_supporting_links_work(self):
        supporting = self.publisher.repo / 'content/supporting'; supporting.mkdir()
        (supporting / 'note.md').write_text('---\ntitle: Note\n---\n\n<a name="claim"></a>\n\nA source claim.\n')
        self.publisher.source('Field Notes', 'See [the claim](supporting/%6eote.md#cl%61im) and [the opening](manuscript.md#opening).')
        sha = self.publisher.publish('Add supporting evidence')
        self.build()
        reader = self.output / 'editions' / sha / 'read'
        for source, download in [('content/manuscript.md', 'source.gfm'), ('content/supporting/note.md', 'supporting/note.gfm')]:
            self.assertEqual(self.publisher.git('show', sha + ':' + source), (reader / download).read_text().strip())
            cited = json.loads((reader / download).with_suffix('.citation.json').read_text())
            self.assertEqual(sha, cited['revision'])
            self.assertEqual('content/manuscript.md', cited['manuscript'])
        self.assertIn('supporting/note.html#claim', (reader / 'index.html').read_text())
        self.assertIn('id="claim"', (reader / 'supporting/note.html').read_text())
        self.assertIn('index.html#opening', (reader / 'index.html').read_text())

    def test_supplemental_code_is_an_exact_passive_download(self):
        raw = b'<html><script>throw new Error("must never execute")</script></html>\n'
        (self.publisher.repo / 'content/data set.html').write_bytes(raw)
        self.publisher.source('Field Notes', 'Download [the supporting code](data%20set.html#section).')
        sha = self.publisher.publish('Add supplemental code')
        self.build()
        reader = self.output / 'editions' / sha / 'read'
        files = list((reader / 'downloads').glob('*.bin'))
        self.assertEqual(1, len(files)); self.assertEqual(raw, files[0].read_bytes())
        self.assertIn('downloads/' + files[0].name, (reader / 'index.html').read_text())
        self.assertFalse(list(reader.rglob('data set.html')))
        self.assertNotIn('#section', (reader / 'index.html').read_text())

    def test_mutable_checkout_and_makefile_never_define_the_view(self):
        (self.publisher.repo / 'Makefile').write_text('all:\n\ttouch SHOULD_NOT_EXIST\n')
        (self.publisher.repo / 'content/manuscript.md').write_text('uncommitted content')
        self.build()
        self.assertIn('Field Notes', (self.output / 'index.html').read_text())
        self.assertFalse((self.publisher.repo / 'SHOULD_NOT_EXIST').exists())

    def test_failed_authentication_or_content_never_leaves_partial_output(self):
        with self.assertRaises(ValueError):
            build_site(self.publisher.repo, self.output, fingerprints=['0' * 40])
        self.assertFalse(self.output.exists())
        self.publisher.source('Field Notes', '<script>unsafe</script>')
        self.publisher.publish('Invalid candidate')
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse(self.output.exists())

    def test_unsigned_content_and_housekeeping_have_distinct_meanings(self):
        (self.publisher.repo / 'README.md').write_text('Tooling change.\n')
        self.publisher.git('add', 'README.md'); self.publisher.git('commit', '-qm', 'Update authoring notes')
        self.assertEqual(1, len(self.build()['publications']))
        self.publisher.source('Field Notes', 'Untrusted change.')
        self.publisher.git('add', 'content'); self.publisher.git('commit', '-qm', 'Unsigned change')
        with self.assertRaises(ValueError):
            self.build(output=self.root / 'unsafe')

    def test_previous_catalog_prevents_rewrite_and_output_must_be_new(self):
        previous, _ = discover(self.publisher.repo, **self.publisher.trust)
        self.build()
        with self.assertRaises(ValueError):
            self.build()
        self.publisher.git('reset', '--hard', 'HEAD^')
        self.publisher.source('Replacement', 'Different history.')
        self.publisher.publish('Replace old edition')
        with self.assertRaises(ValueError):
            self.build(output=self.root / 'replacement', previous=previous)
        self.assertFalse((self.root / 'replacement').exists())
