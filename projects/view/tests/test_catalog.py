"""Executable catalog convention: signed source, DOI scope and persistent records.

Administrative DOI/date/status facts are supplied explicitly, not inferred from
Git history, a registrar API or a browser location. These tests use real OpenPGP
signatures from a synthetic, differently named source repository.
"""
from __future__ import annotations

import copy
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from guavajam_core.catalog import CatalogError, compare, doi_url, encoded, read_catalog, validate
from guavajam.renderer import RenderError
from guavajam.site import build_site
from catalog_fixture import command, make_catalog_fixture


from guavajam_test_support import FIXTURES, RESOURCES
class Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.ids, self.links, self.meta, self.tags = [], [], {}, []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if tag == 'a':
            self.links.append(attrs)
        if tag == 'meta' and 'name' in attrs:
            self.meta.setdefault(attrs['name'], []).append(attrs.get('content'))


class CatalogSchemaTests(unittest.TestCase):
    def setUp(self):
        self.sha = 'a' * 40
        self.catalog = {'schema': 'guavajam.catalog.v1', 'title': 'Publications',
                        'publisher': 'Publisher', 'base_url': 'https://example.org', 'language': 'en',
                        'works': [{'slug': 'essay', 'metadata_revision': self.sha,
                                   'editions': [{'revision': self.sha, 'published': '2026-11-01', 'status': 'available'}]}]}

    def test_copied_catalog_starter_and_help_are_usable_without_renderer_state(self):
        import sys
        root = Path(__file__).resolve().parents[1]
        example = read_catalog(root / 'examples/catalog.json')
        self.assertEqual([], example['works'])
        help_result = subprocess.run([sys.executable, '-m', 'guavajam', 'site', '--help'],
                                     cwd=root, text=True, capture_output=True)
        self.assertEqual(0, help_result.returncode)
        self.assertIn('--previous-catalog', help_result.stdout)
        self.assertNotIn('--unsigned-preview', help_result.stdout)

    def test_optional_dois_and_empty_registry_are_supported(self):
        validate(self.catalog)
        validate({**self.catalog, 'works': []})
        self.assertEqual('https://doi.org/10.1234/part%25value', doi_url('10.1234/part%value'))

    def test_unknown_fields_unsafe_urls_and_ambiguous_identifiers_fail(self):
        changes = [({'signer': 'self-appointed'}, None), ({'base_url': 'http://example.org'}, None),
                   ({'base_url': 'https://user:secret@example.org'}, None),
                   ({'base_url': 'https://example.org/x?query'}, None),
                   ({'base_url': 'https://example.org/../outside'}, None),
                   ({'source_view': 'https://{path}/blob/{revision}'}, None),
                   ({'source_view': 'https://example.org/{revision}/{path}/{unknown}'}, None),
                   ({'source_view': 'javascript:{revision}{path}'}, None),
                   ({'works': 'not a list'}, None), ({'publisher': 'Bad\nName'}, None),
                   ({'language': ['en']}, None)]
        for root, _ in changes:
            with self.subTest(root=root), self.assertRaises(CatalogError):
                validate({**self.catalog, **root})
        for update in [{'revision': 'HEAD'}, {'revision': 'A' * 40}, {'revision': 'a' * 39},
                       {'published': '2026-02-30'}, {'status': 'withdrawn'}, {'status': {}},
                       {'doi': 'https://doi.org/10.1234/example'}, {'doi': '10.1234/UPPER'},
                       {'notices': [{'date': '2020-01-01', 'text': 'Too early.'}]},
                       {'notices': [{'date': '2026-11-01', 'text': '<script>Literal</script>', 'html': True}]}]:
            candidate = copy.deepcopy(self.catalog)
            candidate['works'][0]['editions'][0].update(update)
            with self.subTest(update=update), self.assertRaises(CatalogError):
                validate(candidate)
        for key in ('metadata_revision', 'recommended_revision'):
            candidate = copy.deepcopy(self.catalog)
            candidate['works'][0][key] = 'b' * 40
            with self.subTest(key=key), self.assertRaises(CatalogError):
                validate(candidate)

    def test_supersession_requires_a_valid_same_work_successor_and_notice(self):
        original = copy.deepcopy(self.catalog)
        first = original['works'][0]['editions'][0]
        for update in ({'status': 'superseded'},
                       {'status': 'superseded', 'notices': [{'date': '2026-11-02', 'text': 'Superseded.'}]},
                       {'successor_revision': 'b' * 40},
                       {'successor_revision': original['works'][0]['editions'][0]['revision']},
                       {'successor_revision': 12}):
            candidate = copy.deepcopy(original)
            candidate['works'][0]['editions'][0].update(update)
            with self.subTest(update=update), self.assertRaises(CatalogError):
                validate(candidate)
        second_revision = 'b' * 40
        expanded = copy.deepcopy(original)
        expanded['works'][0]['editions'].append({
            'revision': second_revision, 'published': '2026-11-02', 'status': 'available'})
        old = expanded['works'][0]['editions'][0]
        old.update(status='superseded', successor_revision=second_revision,
                   notices=[{'date': '2026-11-02', 'text': 'Replaced by successor.'}])
        validate(expanded)
        with self.assertRaisesRegex(CatalogError, 'only withdrawn or superseded'):
            candidate=copy.deepcopy(expanded)
            candidate['works'][0]['editions'][0]['status']='available'
            validate(candidate)
        with self.assertRaisesRegex(CatalogError, 'cycles'):
            candidate=copy.deepcopy(expanded)
            # Same-day successors make each edge valid independently, so this
            # checks the cycle rule rather than a backwards publication date.
            candidate['works'][0]['editions'][1]['published'] = first['published']
            candidate['works'][0]['editions'][1].update(
                status='superseded', successor_revision=original['works'][0]['editions'][0]['revision'],
                notices=[{'date': '2026-11-02', 'text': 'Circular metadata.'}])
            validate(candidate)
        moved=copy.deepcopy(expanded)
        moved['works'][0]['editions'][0]['successor_revision'] = 'c' * 40
        with self.assertRaises(CatalogError): validate(moved)

    def test_successor_chains_validate_every_link_before_traversal(self):
        catalog = copy.deepcopy(self.catalog)
        editions = catalog['works'][0]['editions']
        editions[0].update(status='superseded', successor_revision='b' * 40,
                           notices=[{'date': '2026-11-03', 'text': 'Superseded.'}])
        editions.extend([
            {'revision': 'b' * 40, 'published': '2026-11-02', 'status': 'superseded',
             'successor_revision': 'c' * 40,
             'notices': [{'date': '2026-11-03', 'text': 'Superseded again.'}]},
            {'revision': 'c' * 40, 'published': '2026-11-03', 'status': 'available'},
        ])
        validate(catalog)
        for target in ('d' * 40, [], {}, 12, None):
            candidate = copy.deepcopy(catalog)
            candidate['works'][0]['editions'][1]['successor_revision'] = target
            for reverse in (False, True):
                if reverse:
                    candidate['works'][0]['editions'].reverse()
                with self.subTest(target=target, reverse=reverse), self.assertRaises(CatalogError):
                    validate(candidate)

    def test_successor_changes_require_append_only_notice_and_allow_restoration(self):
        old=copy.deepcopy(self.catalog)
        old['works'][0]['editions'].append({'revision': 'b'*40,'published': '2026-11-02','status':'available'})
        old['works'][0]['editions'][0].update(
            status='superseded', successor_revision='b'*40,
            notices=[{'date':'2026-11-02','text':'Superseded.'}])
        validate(old)
        new=copy.deepcopy(old)
        new['works'][0]['editions'][0].update(status='available')
        new['works'][0]['editions'][0].pop('successor_revision')
        with self.assertRaisesRegex(CatalogError,'appended'):
            compare(old,new)
        new['works'][0]['editions'][0]['notices'].append(
            {'date':'2026-11-03','text':'Restored with an explanatory notice.'})
        compare(old,new)

    def test_duplicate_json_fields_and_nonfinite_values_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'catalog.json'
            for raw in (b'{"schema": 1, "schema": 2}', b'{"number": NaN}', b'\xff', b'x' * (1024 * 1024 + 1)):
                path.write_bytes(raw)
                with self.subTest(raw=raw[:40]), self.assertRaises(CatalogError):
                    read_catalog(path)

    def test_doi_names_and_signed_revisions_cannot_be_shared_between_records(self):
        work = self.catalog['works'][0]
        work['concept_doi'] = '10.1234/work'
        work['editions'][0]['doi'] = '10.1234/work'
        with self.assertRaisesRegex(CatalogError, 'multiple'):
            validate(self.catalog)
        del work['concept_doi']
        self.catalog['works'].append({**copy.deepcopy(work), 'slug': 'different'})
        with self.assertRaisesRegex(CatalogError, 'unique'):
            validate(self.catalog)

    def test_last_deployed_catalog_prevents_rebinding_dropped_pages_and_notice_erasure(self):
        old = copy.deepcopy(self.catalog)
        work = old['works'][0]
        work['concept_doi'] = '10.1234/work'
        work['editions'][0]['doi'] = '10.1234/edition'
        for mutate in [lambda c: c.update(works=[]),
                       lambda c: c['works'][0].pop('concept_doi'),
                       lambda c: c['works'][0]['editions'][0].update(doi='10.1234/other'),
                       lambda c: c['works'][0]['editions'][0].update(published='2026-11-02'),
                       lambda c: c['works'][0].update(slug='different')]:
            new = copy.deepcopy(old)
            mutate(new)
            with self.assertRaises(CatalogError):
                compare(old, new)
        new = copy.deepcopy(old)
        new['works'][0]['editions'][0].update(status='withdrawn', notices=[{'date': '2026-11-02', 'text': 'Withdrawn after review.'}])
        compare(old, new)
        newer = copy.deepcopy(new)
        newer['works'][0]['editions'][0]['notices'][0]['text'] = 'Rewritten history.'
        with self.assertRaisesRegex(CatalogError, 'verbatim'):
            compare(new, newer)
        newer = copy.deepcopy(new)
        newer['works'][0]['editions'][0]['notices'].append({'date': '2026-11-03', 'text': 'Restored with a documented notice.'})
        newer['works'][0]['editions'][0]['status'] = 'available'
        compare(new, newer)
        # Adding a DOI after publication does not require rewriting a signed commit.
        compare(self.catalog, old)


class CatalogBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.fixture = make_catalog_fixture(Path(cls.temp.name) / 'fixture')
        cls.environment = patch.dict(os.environ, {'GNUPGHOME': str(cls.fixture['gpg_home'])})
        cls.environment.start()
        cls.addClassCleanup(cls.environment.stop)

    def setUp(self):
        self.local = tempfile.TemporaryDirectory()
        self.addCleanup(self.local.cleanup)
        self.root = Path(self.local.name)
        self.output = self.root / 'site'
        self.catalog = copy.deepcopy(self.fixture['catalog'])
        self.path = self.root / 'catalog.json'

    def build(self, **kwargs):
        self.path.write_bytes(encoded(self.catalog))
        return build_site(self.fixture['repo'], self.path, self.output,
                          fingerprints=[self.fixture['fingerprint']], **({'initial_catalog': True} | kwargs))

    def page(self, name):
        return (self.output / name).read_text('utf-8')

    def edition_root(self, index=1, slug='field-notes'):
        return 'works/' + slug + '/editions/' + self.fixture['revisions'][index]

    def test_complete_site_is_consistent_portable_and_deterministic(self):
        with patch('socket.create_connection', side_effect=AssertionError('build must not fetch URLs')):
            manifest = self.build()
        self.assertEqual(3, len(manifest['canonical_publications']))
        self.assertTrue(manifest['source_signatures_verified'])
        self.assertFalse(manifest['external_media_verified'])
        self.assertFalse(manifest['doi_registration_verified'])
        index = self.page('index.html')
        self.assertIn('Collected Publications', index)
        self.assertIn('Independent Example Imprint', index)
        self.assertIn('https://doi.org/10.1234/field-notes', index)
        self.assertIn('No work DOI is listed', index)
        self.assertNotIn('citation_doi', index)
        self.assertNotIn('nashspence', index)
        self.assertNotIn('github.com', index)
        for path in self.output.rglob('*.html'):
            html = path.read_text()
            parsed = Page(html)
            self.assertNotIn('script', parsed.tags)
            self.assertEqual(len(parsed.ids), len(set(parsed.ids)), str(path))
            self.assertIn('aria-label="Breadcrumb"', html)
        for name, digest in manifest['files'].items():
            self.assertEqual(digest, hashlib.sha256((self.output / name).read_bytes()).hexdigest(), name)
        self.assertEqual(set(manifest['files']) | {'SITE.json'},
                         {str(p.relative_to(self.output)) for p in self.output.rglob('*') if p.is_file()})
        second = self.root / 'repeat'
        build_site(self.fixture['repo'], self.path, second, initial_catalog=True, fingerprints=[self.fixture['fingerprint']])
        self.assertEqual({str(p.relative_to(self.output)): p.read_bytes() for p in self.output.rglob('*') if p.is_file()},
                         {str(p.relative_to(second)): p.read_bytes() for p in second.rglob('*') if p.is_file()})

    def test_breadcrumbs_are_semantic_specific_and_never_self_links(self):
        self.build()
        rev = self.fixture['revisions'][1]
        root = self.edition_root()
        paths = [
            ('index.html', ['Collected Publications']),
            ('works/field-notes/index.html', ['Publications', 'Field Notes, Revised']),
            (root + '/index.html', ['Publications', 'Field Notes, Revised', 'Revised edition']),
            (root + '/read/index.html', ['Publications', 'Field Notes, Revised', 'Revised edition', 'Reading']),
            (root + '/read/cite.html', ['Publications', 'Field Notes, Revised', 'Revised edition', 'Reading', 'Citations']),
            (root + '/read/supporting/note.cite.html', ['Publications', 'Field Notes, Revised', 'Revised edition', 'Supporting source', 'Citations']),
        ]
        for filename, labels in paths:
            with self.subTest(filename=filename):
                markup = self.page(filename)
                from html.parser import HTMLParser
                class Crumbs(HTMLParser):
                    def __init__(self):
                        super().__init__(); self.inside=False;self.depth=0;self.labels=[];self.links=[];self.current=[];self.ordered=0
                    def handle_starttag(self, tag, attrs):
                        attrs=dict(attrs)
                        if tag == 'nav' and attrs.get('aria-label') == 'Breadcrumb':self.inside=True;self.depth=1
                        elif self.inside:
                            if tag == 'ol':self.ordered+=1
                            if tag == 'a':self.links.append(attrs.get('href'))
                            if attrs.get('aria-current')=='page':self.current.append(tag)
                            if tag not in {'br', 'img', 'hr'}:self.depth+=1
                    def handle_endtag(self,tag):
                        if self.inside:
                            self.depth-=1
                            if self.depth==0:self.inside=False
                    def handle_data(self,data):
                        if self.inside and data.strip() not in {'/'}:self.labels.append(data.strip())
                parsed=Crumbs();parsed.feed(markup)
                self.assertEqual(1,parsed.ordered)
                self.assertEqual(labels,parsed.labels)
                self.assertEqual(['span'],parsed.current)
                self.assertEqual(len(labels)-1,len(parsed.links))
        reader=self.page(root+'/read/index.html')
        self.assertLess(reader.index('itemprop="headline"'),reader.index('Source and citation'))
        self.assertLess(reader.index('Field Notes, Revised</h1>'),reader.index('<div class="press-source-identity">'))
        self.assertEqual(1,reader.count('Git SHA-1 '+rev))
        self.assertNotIn('class="guavajam-source-guide"',reader)

    def test_doi_metadata_has_work_edition_and_supporting_document_scope(self):
        self.build()
        work = Page(self.page('works/field-notes/index.html'))
        root = self.edition_root()
        record = Page(self.page(root + '/index.html'))
        reader = Page(self.page(root + '/read/index.html'))
        supporting = Page(self.page(root + '/read/supporting/note.html'))
        self.assertEqual(['10.1234/field-notes'], work.meta['citation_doi'])
        for parsed in (record, reader):
            self.assertEqual(['10.1234/field-notes.v2'], parsed.meta['citation_doi'])
            self.assertEqual(['2026-11-01'], parsed.meta['citation_publication_date'])
        self.assertNotIn('citation_doi', supporting.meta)
        self.assertEqual(['https://doi.org/10.1234/field-notes.v2'], supporting.meta['DC.relation'])
        self.assertIn('Field Notes, Revised', work.meta['citation_title'])
        old = Page(self.page(self.edition_root(0) + '/index.html'))
        self.assertEqual(['Field Notes'], old.meta['citation_title'])
        self.assertIn('&lt;markup&gt; &amp; Unicode: Élan', self.page(root + '/index.html'))

    def test_partial_catalog_does_not_establish_a_work_publication_date(self):
        work = self.catalog['works'][0]
        older = work['editions'].pop(0)
        self.build()
        partial = Page(self.page('works/field-notes/index.html'))
        self.assertEqual(['Field Notes, Revised'], partial.meta['citation_title'])
        self.assertEqual(['10.1234/field-notes'], partial.meta['citation_doi'])
        for field in ('citation_publication_date', 'DC.date'):
            self.assertNotIn(field, partial.meta)
        baseline = self.output / 'CATALOG.json'
        work['editions'].insert(0, older)
        self.output = self.root / 'expanded'
        self.build(initial_catalog=False, previous_catalog=baseline)
        expanded = Page(self.page('works/field-notes/index.html'))
        self.assertEqual(partial.meta, expanded.meta)
        record = Page(self.page(self.edition_root(0) + '/index.html'))
        self.assertEqual([older['published']], record.meta['citation_publication_date'])

    def test_later_doi_assignment_preserves_publication_dates_and_source_identity(self):
        work = self.catalog['works'][0]
        concept = work.pop('concept_doi')
        identifiers = [edition.pop('doi') for edition in work['editions']]
        self.build()
        before_work = Page(self.page('works/field-notes/index.html'))
        before = [json.loads(self.page(self.edition_root(i) + '/citation.json'))
                  for i in range(2)]
        sources = [(self.output / self.edition_root(i) / 'read/source.gfm').read_bytes()
                   for i in range(2)]
        baseline = self.output / 'CATALOG.json'
        work['concept_doi'] = concept
        for edition, identifier in zip(work['editions'], identifiers):
            edition['doi'] = identifier
        self.output = self.root / 'with-dois'
        self.build(initial_catalog=False, previous_catalog=baseline)
        after_work = Page(self.page('works/field-notes/index.html'))
        self.assertEqual({**before_work.meta, 'citation_doi': [concept],
                          'DC.identifier': [doi_url(concept)]}, after_work.meta)
        for i, identifier in enumerate(identifiers):
            with self.subTest(revision=work['editions'][i]['revision']):
                after = json.loads(self.page(self.edition_root(i) + '/citation.json'))
                self.assertEqual({**before[i], 'bibliography': {
                    **before[i]['bibliography'], 'doi': identifier}}, after)
                record = Page(self.page(self.edition_root(i) + '/index.html'))
                self.assertEqual([before[i]['bibliography']['published']],
                                 record.meta['citation_publication_date'])
                self.assertEqual(sources[i], (self.output / self.edition_root(i)
                                             / 'read/source.gfm').read_bytes())

    def test_citation_journey_uses_full_signed_source_and_reviewed_passages(self):
        self.build()
        root = self.edition_root()
        raw = self.page(root + '/read/index.html')
        parsed = Page(raw)
        self.assertIn('opening', parsed.ids)
        self.assertIn('guavajam-catalog-main', parsed.ids)
        self.assertTrue(any(a.get('href') == 'cite.html#opening' for a in parsed.links))
        self.assertIn('Cite the committed work or passage', raw)
        cite = self.page(root + '/read/cite.html')
        self.assertIn(self.fixture['revisions'][1], cite)
        self.assertIn('works/field-notes/manuscript.md', cite)
        self.assertIn(' · #opening', cite)
        self.assertEqual(len(Page(cite).ids), len(set(Page(cite).ids)))
        self.assertIn('guavajam-catalog-main-view', Page(cite).ids)
        data = json.loads(self.page(root + '/read/supporting/note.cite.json'))
        self.assertEqual('works/field-notes/supporting/note.md', data['canonical']['document'])
        self.assertIn('claim', data['anchors'])
        self.assertNotIn('url', data['canonical'])
        expected = subprocess.check_output(['git', 'show', self.fixture['revisions'][1] + ':works/field-notes/manuscript.md'], cwd=self.fixture['repo'])
        self.assertEqual(expected, (self.output / root / 'read/source.gfm').read_bytes())
        view = json.loads(self.page(root + '/read/VIEW.json'))
        for filename, digest in view['files'].items():
            self.assertEqual(digest, hashlib.sha256((self.output / root / 'read' / filename).read_bytes()).hexdigest())

    def test_withdrawn_keeps_reader_passage_citations_and_original_downloads(self):
        revision = self.fixture['revisions'][0]
        first = self.catalog['works'][0]['editions'][0]
        first.update(status='withdrawn',
                     notices=[{'date': '2026-11-02', 'text': 'Withdrawn after review; historical source remains.'}],
                     successor_revision=self.fixture['revisions'][1])
        manifest = self.build()
        root = self.edition_root(0)
        record = self.page(root + '/index.html')
        view = self.page(root + '/read/index.html')
        note = self.page(root + '/read/supporting/note.html')
        cite = self.page(root + '/read/cite.html')
        note_cite = self.page(root + '/read/supporting/note.cite.html')
        for name, html in [('landing', record), ('reading', view), ('support', note),
                           ('citation', cite), ('support citation', note_cite)]:
            with self.subTest(page=name):
                self.assertIn('Withdrawn edition', html)
                self.assertIn('Withdrawn after review; historical source remains.', html)
                self.assertIn('role="note"', html)
                self.assertIn('View the successor edition record', html)
                self.assertNotIn('<script', html)
        self.assertLess(record.index('Withdrawn edition</h2>'), record.index('>Read this edition</a>'))
        self.assertLess(view.index('Withdrawn edition</h2>'), view.index('First observation.'))
        self.assertLess(view.index('<h1'), view.index('Withdrawn edition</h2>'))
        self.assertIn('Read this edition', record)
        self.assertIn('source.gfm', record)
        self.assertIn('Cite committed passages', record)
        self.assertIn(revision, self.page(root + '/citation.json'))
        self.assertEqual(self.fixture['revisions'][1], first['successor_revision'])
        # Two revisions are retained in Git; compare to the exact *first* tree.
        expected = subprocess.run(['git', 'show', revision + ':works/field-notes/manuscript.md'],
                                  cwd=self.fixture['repo'], capture_output=True, check=True).stdout
        self.assertEqual(expected, (self.output / root / 'read/source.gfm').read_bytes())
        self.assertEqual('withdrawn', json.loads(self.page(root + '/citation.json'))['status'])
        self.assertEqual('withdrawn', json.loads(self.page(root + '/read/cite.json'))['status'])
        self.assertIn(root + '/read/', self.page('sitemap.xml'))
        self.assertIn('Withdrawn', self.page('feed.xml'))
        self.assertIn(root + '/read/index.html', manifest['files'])
        self.assertIn(root + '/read/supporting/note.gfm', manifest['files'])

    def test_superseded_keeps_reader_links_to_exact_reviewed_successor(self):
        first = self.catalog['works'][0]['editions'][0]
        first.update(status='superseded',
                     successor_revision=self.fixture['revisions'][1],
                     notices=[{'date': '2026-11-02', 'text': 'Replaced by the revised signed source.'}])
        manifest = self.build()
        root = self.edition_root(0)
        target = self.edition_root(1) + '/index.html'
        for name in ('index.html','read/index.html','read/cite.html','read/supporting/note.html'):
            page = root + '/' + name
            html = self.page(page)
            self.assertIn('Superseded edition', html)
            self.assertIn('Replaced by the revised signed source.', html)
            self.assertIn('View the successor edition record', html)
            self.assertIn('href="' + __import__('posixpath').relpath(target, __import__('posixpath').dirname(page)) + '"', html)
        self.assertIn(root + '/read/index.html', manifest['files'])
        self.assertIn(root + '/read/', self.page('sitemap.xml'))
        self.assertIn('Superseded', self.page('feed.xml'))

    def test_unavailable_remains_an_explained_tombstone_not_a_withdrawal(self):
        self.build()
        root = self.edition_root(2, 'other-work')
        html = self.page(root + '/index.html')
        self.assertIn('Unavailable edition', html)
        self.assertIn('Reading distribution is unavailable', html)
        self.assertIn('retained bibliographic record', html)
        self.assertNotIn('>Read this edition<', html)
        self.assertFalse((self.output / root / 'read').exists())
        self.assertTrue((self.output / root / 'citation.json').exists())
        self.assertNotIn(root + '/read/', self.page('sitemap.xml'))

    def test_status_transition_deployment_retains_old_reader_and_notice_history(self):
        first = self.catalog['works'][0]['editions'][0]
        previous = self.root / 'prior-catalog.json'
        previous.write_bytes(encoded(self.catalog))
        first.update(status='withdrawn', notices=[{'date': '2026-11-02', 'text': 'Retired from recommendation, not erased.'}])
        self.path.write_bytes(encoded(self.catalog))
        build_site(self.fixture['repo'], self.path, self.output,
                   previous_catalog=previous, fingerprints=[self.fixture['fingerprint']])
        root = self.edition_root(0)
        self.assertIn(root + '/read/index.html', json.loads(self.page('SITE.json'))['files'])
        self.assertIn('Retired from recommendation, not erased.', self.page(root + '/read/index.html'))
        with self.assertRaisesRegex(CatalogError, 'require an appended editorial notice'):
            altered = copy.deepcopy(self.catalog)
            altered['works'][0]['editions'][0]['status'] = 'available'
            compare(self.catalog, altered)

    def test_sitemap_feed_are_valid_date_explicit_and_deployment_prefix_safe(self):
        self.build()
        sitemap = ET.fromstring(self.page('sitemap.xml'))
        urls = [e.text for e in sitemap.iter() if e.tag.endswith('}loc')]
        self.assertTrue(all(url.startswith('https://publisher.example/collection/') for url in urls))
        self.assertFalse(any('cite.html' in url for url in urls))
        feed = ET.fromstring(self.page('feed.xml'))
        ns = {'a': 'http://www.w3.org/2005/Atom'}
        self.assertEqual(3, len(feed.findall('a:entry', ns)))
        self.assertEqual('2026-11-04T00:00:00Z', feed.find('a:updated', ns).text)
        self.assertIn('Unavailable', self.page('feed.xml'))

    def test_previous_catalog_is_required_and_preserved_records_cannot_disappear(self):
        self.path.write_bytes(encoded(self.catalog))
        with self.assertRaisesRegex(CatalogError, 'last deployed'):
            build_site(self.fixture['repo'], self.path, self.output, fingerprints=[self.fixture['fingerprint']])
        self.build()
        previous = self.output / 'CATALOG.json'
        self.catalog['works'][0]['editions'][0]['doi'] = '10.1234/rebound'
        self.path.write_bytes(encoded(self.catalog))
        rejected = self.root / 'rejected'
        with self.assertRaisesRegex(CatalogError, 'rebound'):
            build_site(self.fixture['repo'], self.path, rejected,
                       previous_catalog=previous, fingerprints=[self.fixture['fingerprint']])
        self.assertFalse(rejected.exists())

    def test_unsigned_untrusted_and_wrong_work_publications_fail_atomically(self):
        original = self.fixture['revisions'][0]
        draft = command(self.fixture['repo'], 'git', 'rev-parse', original + '^')
        self.catalog['works'][0]['editions'][0]['revision'] = draft
        with self.assertRaises((RenderError, CatalogError)):
            self.build()
        self.assertFalse(self.output.exists())
        self.catalog = copy.deepcopy(self.fixture['catalog'])
        self.path.write_bytes(encoded(self.catalog))
        with self.assertRaises((RenderError, CatalogError)):
            build_site(self.fixture['repo'], self.path, self.output, fingerprints=['0' * 40], initial_catalog=True)
        self.assertFalse(self.output.exists())
        self.catalog['works'][0]['slug'] = 'unpublished-work'
        with self.assertRaises((RenderError, CatalogError)):
            self.build()
        self.assertFalse(self.output.exists())

    def test_site_limits_symlinks_existing_output_and_read_only_source_are_enforced(self):
        with patch('guavajam.site.MAX_SITE_BYTES', 100), self.assertRaisesRegex(CatalogError, 'exceeds'):
            self.build()
        self.assertFalse(self.output.exists())
        self.output.mkdir()
        (self.output / 'sentinel').write_text('must remain')
        with self.assertRaisesRegex(CatalogError, 'new or empty'):
            self.build()
        self.assertEqual('must remain', (self.output / 'sentinel').read_text())
        self.output = self.root / 'linked'
        self.output.symlink_to(self.root / 'site', target_is_directory=True)
        with self.assertRaisesRegex(CatalogError, 'symlink'):
            self.build()
        self.output = self.fixture['repo'] / 'generated-site'
        with self.assertRaisesRegex(CatalogError, 'read-only'):
            self.build()

    def test_reviewed_text_is_escaped_and_never_interpreted_as_html(self):
        self.catalog['title'] = 'Catalog <script>alert(1)</script>'
        self.catalog['works'][0]['editions'][0]['notices'] = [{'date': '2026-11-01', 'text': '<img src=x onerror=bad()> & explanation'}]
        self.build()
        self.assertIn('&lt;script&gt;', self.page('index.html'))
        self.assertNotIn('<script', self.page('index.html'))
        self.assertIn('&lt;img src=x onerror=bad()&gt;', self.page(self.edition_root(0) + '/index.html'))

    def test_catalog_does_not_discover_or_publish_new_head_commits(self):
        self.catalog['works'] = []
        manifest = self.build()
        self.assertEqual([], manifest['canonical_publications'])
        self.assertIn('No publications have been listed', self.page('index.html'))
        self.assertFalse((self.output / 'feed.xml').exists())
        self.assertFalse((self.output / 'works').exists())

    def clone_variant(self):
        repository = self.root / 'variant'
        command(self.root, 'git', 'clone', '-q', '--no-hardlinks', str(self.fixture['repo']), str(repository))
        for name, value in [('user.name', 'Fixture'), ('user.email', 'fixture@example.org'),
                            ('gpg.format', 'openpgp'), ('user.signingkey', self.fixture['fingerprint'])]:
            command(repository, 'git', 'config', name, value)
        return repository

    def signed_variant(self, repository):
        command(repository, 'git', 'add', '-A')
        command(repository, 'git', 'commit', '-qm', 'Review a changed source fixture')
        command(repository, 'git', 'commit', '--allow-empty', '-S', '-m', 'Publish source fixture',
                '-m', 'Press-Publication: 1\nPress-Work: works/field-notes/manuscript.md\nPress-Published: 2026-11-13')
        revision = command(repository, 'git', 'rev-parse', 'HEAD')
        work = self.catalog['works'][0]
        work['editions'] = [{**work['editions'][1], 'revision': revision}]
        work['metadata_revision'] = revision
        work['recommended_revision'] = revision
        self.catalog['works'] = [work]
        self.path.write_bytes(encoded(self.catalog))
        return revision

    def test_all_reviewed_lantern_passages_keep_their_canonical_targets(self):
        repository = self.clone_variant()
        fixtures = FIXTURES / 'chatgpt'
        for branch in ('current', 'alternate'):
            source = (fixtures / ('lantern-' + branch + '.gfm.md')).read_bytes()
            (repository / 'works/field-notes/supporting' / ('lantern-' + branch + '.md')).write_bytes(source)
        revision = self.signed_variant(repository)
        build_site(repository, self.path, self.output, fingerprints=[self.fixture['fingerprint']], initial_catalog=True)
        root = 'works/field-notes/editions/' + revision + '/read/'
        view = json.loads(self.page(root + 'VIEW.json'))
        for branch in ('current', 'alternate'):
            info = view['documents']['works/field-notes/supporting/lantern-' + branch + '.md']
            self.assertGreater(len(info['anchors']), 100)
            body = Page(self.page(root + info['view']))
            cite = Page(self.page(root + 'supporting/lantern-' + branch + '.cite.html'))
            self.assertTrue(set(info['anchors']) <= set(body.ids))
            self.assertTrue(set(info['anchors']) <= set(cite.ids))
            links = [link for link in body.links if link.get('class') == 'guavajam-passage-cite']
            self.assertEqual(len(info['anchors']), len(links))
            self.assertIn('Supporting source documents', self.page(root.removesuffix('read/') + 'index.html'))

    def test_catalog_rejects_conflicting_signed_concept_doi_without_rewriting_source(self):
        repository = self.clone_variant()
        manuscript = repository / 'works/field-notes/manuscript.md'
        manuscript.write_text(manuscript.read_text().replace('schema_type: ScholarlyArticle',
                              'schema_type: ScholarlyArticle\nidentifiers:\n  concept_doi: 10.1234/another-work'))
        self.signed_variant(repository)
        with self.assertRaisesRegex(CatalogError, 'conflicts'):
            build_site(repository, self.path, self.output, fingerprints=[self.fixture['fingerprint']], initial_catalog=True)
        self.assertFalse(self.output.exists())

    def test_a_mutated_checkout_never_changes_committed_bibliography_or_downloads(self):
        repository = self.clone_variant()
        manuscript = repository / 'works/field-notes/manuscript.md'
        manuscript.write_text('<script>uncommitted mutation</script>')
        self.path.write_bytes(encoded(self.catalog))
        build_site(repository, self.path, self.output, fingerprints=[self.fixture['fingerprint']], initial_catalog=True)
        self.assertNotIn('uncommitted mutation', self.page(self.edition_root() + '/read/source.gfm'))
        self.assertIn('Field Notes, Revised', self.page('works/field-notes/index.html'))

    def test_non_git_build_inputs_cannot_choose_a_signature_verifier_or_repository_path(self):
        for extra in ({'repo': '/arbitrary/repo'}, {'trusted_gpg_fingerprint': 'a' * 40},
                      {'command': 'curl untrusted'}, {'css': '<script>bad</script>'}):
            self.catalog = {**self.fixture['catalog'], **extra}
            with self.subTest(extra=extra), self.assertRaises(CatalogError):
                self.build()
        self.assertFalse(self.output.exists())
