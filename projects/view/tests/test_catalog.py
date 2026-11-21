"""Exact signed editions rendered from Git, with immutable downloads and citations."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from guavajam.site import build_site
from guavajam_core.publication_protocol import discover
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
        self.assertIn('href="editions/' + second + '/verify.html"', landing)

        self.assertIn('href="editions/index.html">History</a>', landing)
        self.assertNotIn('>v1</a>', landing)
        history = (self.output / 'editions/index.html').read_text()
        self.assertIn('>v1</a>', history)
        self.assertIn('>v2</a>', history)
        self.assertIn('href="' + first + '/read/index.html"', history)
        self.assertIn('<blockquote class="guavajam-citation"', landing)
        self.assertIn('href="editions/' + second + '/read/documents.html">Documents</a>', landing)
        edition = (self.output / 'editions' / second / 'verify.html').read_text()
        self.assertIn('Explain why &lt;script&gt;', edition)
        self.assertNotIn('<script>', edition)
        self.assertIn('2025-01-01', edition)
        self.assertNotIn('class="guavajam-byline"', edition)
        self.assertNotIn('<section class="guavajam-directory"', edition)
        self.assertIn('href="SOURCE.json">Verification record (JSON)</a>', edition)
        self.assertIn('href="CITATION.json" download', edition)
        self.assertNotIn('href="read/documents.html">Documents</a>', edition)
        reader = (self.output / 'editions' / second / 'read/index.html').read_text()
        self.assertIn('href="documents.html">Documents</a>', reader)
        self.assertIn('href="../verify.html">Verify</a>', reader)


    def test_downloads_and_citations_are_exact_and_supporting_links_work(self):
        supporting = self.publisher.repo / 'content/supporting'; supporting.mkdir()
        (supporting / 'note.md').write_text('<a name="document"></a>\n\n# Note\n\n<a name="claim"></a>\n\nA source claim.\n')
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
