"""Search results contain issued source documents, never navigation or drafts.

Robots must be able to crawl auxiliary pages to observe noindex and discover
document links: https://developers.google.com/search/docs/crawling-indexing/block-indexing
"""
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from a_guavajam_static_website_generator.renderer import render
from a_guavajam_static_website_generator.search_index import create_index, homepage
from a_guavajam_static_website_generator.site import build_site
from catalog_fixture import make_catalog_fixture


class Robots(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.in_head = False
        self.directives = []
        self.links = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'head':
            self.in_head = True
        if tag == 'meta' and attrs.get('name', '').lower() == 'robots':
            self.directives.append((self.in_head, attrs.get('content')))
        if tag == 'a' and attrs.get('href'):
            self.links.append(attrs)

    def handle_endtag(self, tag):
        if tag == 'head':
            self.in_head = False


class IndexingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.fixture = make_catalog_fixture(cls.root / 'publisher')
        cls.site = cls.root / 'collection'
        cls.work = cls.site / 'sources/field-notes'
        build_site(cls.fixture['repo'], cls.work, repository='https://example.org/field-notes.git',
                   search_assets='../../', **cls.fixture['trust'])

    def assert_policy(self, path, *, indexable):
        parsed = Robots(path.read_text('utf-8'))
        self.assertEqual([(True, 'index, follow' if indexable else 'noindex, follow')],
                         parsed.directives, str(path))
        # Keeping links ordinary lets robots discover the actual reading pages.
        self.assertFalse(any('nofollow' in link.get('rel', '').split()
                             for link in parsed.links), str(path))
        return parsed

    def assert_inventory(self, root, filename):
        for relative, digest in json.loads((root / filename).read_text())['files'].items():
            self.assertEqual(digest, hashlib.sha256((root / relative).read_bytes()).hexdigest(),
                             relative)

    def test_every_signed_edition_indexes_only_its_source_documents(self):
        expected = set()
        for revision in self.fixture['revisions']:
            read = self.work / 'editions' / revision / 'read'
            view = json.loads((read / 'VIEW.json').read_text())
            expected.update(read / details['view'] for details in view['documents'].values())
            self.assert_inventory(read, 'VIEW.json')
        # Historical manuscripts remain distinct citable documents; the policy
        # is not a latest-version filter or a redirect to the newest edition.
        self.assertEqual(3, len(expected))
        pages = set(self.work.rglob('*.html'))
        self.assertTrue(expected.issubset(pages))
        self.assertTrue(any('/passages/page/' in path.as_posix() for path in pages))
        for path in sorted(pages):
            with self.subTest(page=str(path.relative_to(self.work))):
                self.assert_policy(path, indexable=path in expected)
        self.assert_inventory(self.work, 'SITE.json')
        self.assertFalse((self.site / 'robots.txt').exists())
        self.assertFalse((self.work / 'robots.txt').exists())

    def test_standalone_render_excludes_its_directory_and_all_unsigned_previews(self):
        revision = self.fixture['revisions'][-1]
        for preview in (False, True):
            output = self.root / ('preview' if preview else 'standalone')
            render(self.fixture['repo'], revision, output, preview=preview,
                   **({} if preview else self.fixture['trust']))
            view = json.loads((output / 'VIEW.json').read_text())
            documents = {output / details['view'] for details in view['documents'].values()}
            for path in output.rglob('*.html'):
                with self.subTest(preview=preview, page=path.name):
                    self.assert_policy(path, indexable=not preview and path in documents)
            self.assert_policy(output / 'documents.html', indexable=False)
            self.assert_inventory(output, 'VIEW.json')

    def test_collection_and_paginated_search_surfaces_are_explicitly_noindex(self):
        # Two distinct collection entries let the normal page generator exercise
        # pagination without adding unrelated publication-history fixtures.
        duplicate = self.site / 'sources/second-work'
        shutil.copytree(self.work, duplicate)
        tip = self.fixture['revisions'][-1]
        create_index(self.site, [
            {'repository': 'https://example.org/field-notes.git',
             'path': 'sources/field-notes', 'tip': tip},
            {'repository': 'https://example.org/second-work.git',
             'path': 'sources/second-work', 'tip': tip}])
        with patch('a_guavajam_static_website_generator.directory_pages.PAGE_SIZE', 1):
            for interactive in (False, True):
                (self.site / 'index.html').write_text(homepage(self.site, interactive=interactive))
                for path in (self.site / 'index.html', self.site / 'page/2.html'):
                    with self.subTest(interactive=interactive, page=path.name):
                        parsed = self.assert_policy(path, indexable=False)
                        self.assertTrue(any('/read/index.html' in link['href'] for link in parsed.links))


if __name__ == '__main__':
    unittest.main()
