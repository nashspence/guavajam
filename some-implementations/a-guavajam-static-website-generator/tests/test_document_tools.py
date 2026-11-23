"""A document's own tools and exact, recoverable edition source downloads."""
from pathlib import Path
import hashlib
import json
import re
import subprocess
import tempfile
import unittest

from guavajam_test_support.discovery_fixture import Publisher
from guavajam.publication_commit import verify
from a_guavajam_static_website_generator.icons import finish_html
from a_guavajam_static_website_generator.site import build_site
from test_icons import Tags


class DocumentToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.publisher = Publisher(self.root / 'publisher')
        supporting = self.publisher.repo / 'content/supporting'; supporting.mkdir()
        (supporting / 'note.md').write_text("""---
title: Evidence note
abstract: Independent observations supporting the manuscript.
metadata:
  Scope: '<script>literal evidence</script>'
  Stations:
    - North & South
    - Estuary
---

<a id="metadata"></a>

<a id="section-overview"></a>

## Overview

<a id="overview-text"></a>

Independent observations.

<a id="section-detail"></a>

### Detailed observations

<a id="detail-text"></a>

The source evidence.

<a id="section-method"></a>

## Method

<a id="method-text"></a>

A repeatable procedure.

<a id="section-closing"></a>

## Closing

<a id="closing-text"></a>

A bounded conclusion.
""")
        (supporting / 'plain.md').write_text('---\ntitle: Plain note\n---\n\n<a id="observation"></a>\n\nAn unsectioned observation.\n')
        self.revision = self.publisher.publish('Add reviewed supporting evidence')
        self.publisher.git('switch', '-c', 'private-draft')
        (self.publisher.repo / 'draft.txt').write_text('A draft outside the selected edition.')
        self.publisher.git('add', 'draft.txt'); self.publisher.git('commit', '-qm', 'Private draft')
        self.private = self.publisher.git('rev-parse', 'HEAD')
        self.publisher.git('switch', 'main')
        self.before = self.publisher.git('show-ref')
        self.output = self.root / 'site'
        build_site(self.publisher.repo, self.output, **self.publisher.trust,
                   repository='https://publication.example/work.git')
        self.edition = self.output / 'editions' / self.revision
        self.read = self.edition / 'read'

    def test_supporting_menu_uses_the_documents_own_contents_metadata_and_source(self):
        html = (self.read / 'supporting/note.html').read_text()
        menu = re.search(r'<nav class="guavajam-actions guavajam-version-menu" aria-label="Document links"[^>]*>(.*?)</nav>', html, re.S)
        self.assertIsNotNone(menu)
        links = [attrs for tag, attrs in Tags(menu[1]).tags if tag == 'a']
        self.assertEqual(['note.contents.html', 'note.metadata.html', 'note.gfm'], [link['href'] for link in links])
        self.assertEqual(['Contents', 'Metadata', 'Download (text/markdown)'], [link['aria-label'] for link in links])
        self.assertIn('download', links[-1])
        self.assertLess(menu.start(), html.index('<div class="guavajam-citation"'))
        self.assertNotIn('class="guavajam-contents"', html)
        for link in links:
            self.assertTrue((self.read / 'supporting' / link['href']).is_file())
        self.assertEqual(self.publisher.git('show', self.revision + ':content/supporting/note.md') + '\n',
                         (self.read / 'supporting/note.gfm').read_text())

    def test_supporting_contents_uses_local_anchors_and_nested_outline_rows(self):
        html = (self.read / 'supporting/note.contents.html').read_text()
        hrefs = [attrs.get('href') for tag, attrs in Tags(html).tags if tag == 'a']
        for anchor in ('section-overview', 'section-detail', 'section-method', 'section-closing'):
            self.assertIn('note.html#' + anchor, hrefs)
        self.assertNotIn('index.html#opening', hrefs)
        self.assertIn('Overview</p><ol>', html)
        self.assertIn('<h2>Table of Contents</h2>', html)
        self.assertIn('guavajam-scrollline', html)
        self.assertIn('aria-label="Evidence note"', html)
        empty = (self.read / 'supporting/plain.contents.html').read_text()
        self.assertIn('No individually titled sections were declared in this document.', empty)
        self.assertNotIn('<script', html)

    def test_document_metadata_keeps_literal_fields_separate_from_edition_provenance(self):
        html = (self.read / 'supporting/note.metadata.html').read_text()
        for text in ('Evidence note', 'Independent observations supporting the manuscript.',
                     '&lt;script&gt;literal evidence&lt;/script&gt;', 'North &amp; South', 'Estuary',
                     self.revision, 'content/supporting/note.md'):
            self.assertIn(text, html)
        self.assertNotIn('<script>', html)
        self.assertIn('href="note.citation.json" download', html)
        self.assertIn('href="../../SOURCE.json" download', html)
        self.assertIn('href="../../metadata.html"', html)
        self.assertIn('href="../../source.bundle" download', html)
        self.assertIn('href="note.html"', html)
        self.assertIn('No document metadata declared.', (self.read / 'supporting/plain.metadata.html').read_text())

    def test_repository_download_restores_the_exact_edition_without_private_branches(self):
        for revision in self.publisher.revisions:
            edition = self.output / 'editions' / revision
            html = (edition / 'metadata.html').read_text()
            self.assertIn('href="source.bundle" download', html)
            self.assertNotIn('href="https://publication.example/work.git"', html)
            self.assertIn('https://publication.example/work.git', html)
            restored = self.root / ('restored-' + revision)
            subprocess.run(['git', 'clone', '--bare', str(edition / 'source.bundle'), str(restored)],
                           check=True, capture_output=True)
            tip = subprocess.check_output(['git', '--git-dir', str(restored), 'rev-parse', 'HEAD'], text=True).strip()
            self.assertEqual(revision, tip)
            self.assertEqual(revision, verify(restored, revision, **self.publisher.trust, online_media=False)['revision'])
            self.assertNotEqual(0, subprocess.run(['git', '--git-dir', str(restored), 'cat-file', '-e', self.private], capture_output=True).returncode)
        self.assertEqual(self.before, self.publisher.git('show-ref'))
        self.assertEqual('', self.publisher.git('status', '--porcelain'))

    def test_generated_document_pages_are_in_the_hashed_view_inventory(self):
        view = json.loads((self.read / 'VIEW.json').read_text())
        for name in ('supporting/note.contents.html', 'supporting/note.metadata.html',
                     'supporting/plain.contents.html', 'supporting/plain.metadata.html'):
            self.assertEqual(hashlib.sha256((self.read / name).read_bytes()).hexdigest(), view['files'][name])



class MetadataLinkStyleTests(unittest.TestCase):
    def test_standalone_metadata_links_have_navigation_spacing_without_prose_underlines(self):
        html = finish_html('<div class="guavajam-metadata-field"><dt>Source</dt><dd>'
                           '<a href="source.bundle" download>Git bundle</a></dd></div>'
                           '<p>See <a href="note.html">the supporting evidence</a> for context.</p>')
        field, paragraph = html.split('<p>', 1)
        self.assertIn('class="guavajam-icon-label"', field)
        self.assertNotIn('guavajam-text-link', field)
        self.assertIn('class="guavajam-text-link"', paragraph)


if __name__ == '__main__':
    unittest.main()
