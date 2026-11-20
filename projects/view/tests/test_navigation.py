"""Native navigation and search share exact source order and fragment identity."""
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
import unittest

from guavajam.renderer import render
from guavajam_core.supporting_gfm import parse_gfm_record
from guavajam_test_support.discovery_fixture import Draft


SUPPORT = '''---
Source: "<script>literal metadata</script>"
---

<a name="metadata"></a>

<a name="document"></a>

# Unlinked supporting evidence

<a name="author-outline"></a>

## Source author outline

<a name="observation"></a>

An authorial observation.

<a name="reply" data-guavajam-speaker="responder"></a>

> <a name="quoted-outline"></a>
>
> ## A quoted outline
>
> <a name="quoted-text"></a>
>
> Quotientwave is the responder's observation.
>
> <a name="quoted-detail"></a>
>
> ### A quoted detail
>
> <a name="quoted-detail-text"></a>
>
> A nested quoted observation.

— Example correspondent

<a name="author-continuation"></a>

### Source author continuation

<a name="resumed"></a>

The author resumes the previous outline.

<a name="author-closing"></a>

## Source author closing

<a name="closing-text"></a>

Concluding evidence.
'''


class Contents(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.active = False
        self.depth = 0
        self.links = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'nav' and 'guavajam-contents' in attrs.get('class', '').split():
            self.active = True
        if self.active and tag == 'ul':
            self.depth += 1
        if self.active and tag == 'a':
            self.links.append((attrs['href'], self.depth))

    def handle_endtag(self, tag):
        if self.active and tag == 'ul':
            self.depth -= 1
        if tag == 'nav':
            self.active = False


class NavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.source = Draft(cls.root / 'draft', commit=False)
        cls.source.source('Navigation study', 'An eco**logy** observation.\n\nA second paragraph.\n\n'
                          '```python\nprint("literal code")\n```\n')
        manuscript = cls.source.repo / 'content/manuscript.md'
        with manuscript.open('a') as stream:
            stream.write('\n<a id="detail"></a>\n### Detail\n\nNested evidence.\n\n'
                         '<a id="deep"></a>\n#### Measurement\n\nDeepest observation.\n\n'
                         '<a id="closing"></a>\n## Closing\n\nFinal observation.\n')
        supporting = cls.source.repo / 'content/supporting'
        supporting.mkdir()
        (supporting / 'unlinked.md').write_text(SUPPORT)
        cls.source.git('add', 'content')
        cls.source.git('commit', '-qm', 'Write navigation fixture')
        cls.revision = cls.source.git('rev-parse', 'HEAD')
        cls.output = cls.root / 'read'
        cls.count = render(cls.source.repo, cls.revision, cls.output, preview=True)
        cls.nav = json.loads((cls.output / 'NAV.json').read_text())
        cls.view = json.loads((cls.output / 'VIEW.json').read_text())

    def test_nested_contents_and_index_records_share_source_order(self):
        document = self.nav['documents']['content/manuscript.md']
        self.assertEqual(['opening', 'detail', 'deep', 'closing'],
                         [record['anchor'] for record in document['anchors']])
        self.assertEqual([None, 'opening', 'detail', None],
                         [record['parent'] for record in document['anchors']])
        self.assertEqual([2, 3, 4, 2], [record['level'] for record in document['anchors']])
        self.assertEqual(list(range(4)), [record['position'] for record in document['anchors']])
        self.assertEqual([('#opening', 1), ('#detail', 2), ('#deep', 3), ('#closing', 1)],
                         Contents((self.output / 'index.html').read_text()).links)
        first = document['anchors'][0]['text']
        self.assertIn('ecology', first)
        self.assertIn('observation. A second paragraph.', first)
        self.assertIn('print("literal code")', first)
        self.assertNotIn('Nested evidence', first)
        self.assertIn('Nested evidence', document['anchors'][1]['text'])
        self.assertNotIn('Deepest observation', document['anchors'][1]['text'])
        for generated in ('Contents', '¶', 'UNSIGNED PREVIEW', 'Canonical source', 'Example Author'):
            self.assertNotIn(generated, document['text'])

    def test_quotation_outline_never_changes_author_outline(self):
        document = self.nav['documents']['content/supporting/unlinked.md']
        records = {record['anchor']: record for record in document['anchors']}
        _, positions = parse_gfm_record(SUPPORT)
        self.assertEqual([key for key, _ in sorted(positions.items(), key=lambda pair: pair[1])],
                         list(records))
        self.assertEqual('quotation:reply', records['quoted-outline']['scope'])
        self.assertEqual('reply', records['quoted-outline']['parent'])
        self.assertEqual('quoted-outline', records['quoted-detail']['parent'])
        self.assertEqual('quoted-outline', records['quoted-text']['parent'])
        self.assertEqual('author-outline', records['author-continuation']['parent'])
        self.assertEqual('document', records['author-continuation']['scope'])
        self.assertEqual('passage', records['reply']['kind'])
        self.assertIn('Quotientwave', records['reply']['text'])
        self.assertIn('Quotientwave', records['quoted-outline']['text'])
        self.assertNotIn('Quotientwave', records['author-outline']['text'])
        self.assertIn('Example correspondent', document['text'])
        self.assertIn('<script>literal metadata</script>', records['metadata']['text'])
        self.assertEqual([('#author-outline', 1), ('#author-continuation', 2), ('#author-closing', 1)],
                         Contents((self.output / 'supporting/unlinked.html').read_text()).links)

    def test_standalone_directory_reaches_unlinked_documents_and_hashes_inventory(self):
        self.assertEqual(2, self.count)
        self.assertEqual('guavajam.navigation.v1', self.nav['schema'])
        self.assertEqual(self.view['canonical'], self.nav['canonical'])
        self.assertTrue(self.nav['preview'])
        for path, document in self.nav['documents'].items():
            self.assertEqual(hashlib.sha256((self.source.repo / path).read_bytes()).hexdigest(),
                             document['source_sha256'])
        directory = (self.output / 'documents.html').read_text()
        self.assertIn('href="supporting/unlinked.html"', directory)
        self.assertIn('href="supporting/unlinked.html#author-continuation"', directory)
        self.assertNotIn('href="supporting/unlinked.html#quoted-outline"', directory)
        note = (self.output / 'supporting/unlinked.html').read_text()
        self.assertIn('href="../index.html"', note)
        self.assertIn('href="../documents.html"', note)
        self.assertIn('href="documents.html"', (self.output / 'index.html').read_text())
        for name, digest in self.view['files'].items():
            self.assertEqual(digest, hashlib.sha256((self.output / name).read_bytes()).hexdigest())
        self.assertEqual({'index.html', 'supporting/unlinked.html', 'documents.html', 'NAV.json'},
                         set(self.view['files']))
        for name in ('index.html', 'supporting/unlinked.html', 'documents.html'):
            html = (self.output / name).read_text()
            self.assertIn('script-src &#x27;none&#x27;', html)
            self.assertNotIn('<script', html)


if __name__ == '__main__':
    unittest.main(verbosity=2)
