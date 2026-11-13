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

from guavajam.catalog import CatalogError, compare, doi_url, encoded, read_catalog, validate
from guavajam.renderer import RenderError
from guavajam.site import build_site
from catalog_fixture import command, make_catalog_fixture


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
            self.assertIn('Publication navigation', html)
        for name, digest in manifest['files'].items():
            self.assertEqual(digest, hashlib.sha256((self.output / name).read_bytes()).hexdigest(), name)
        self.assertEqual(set(manifest['files']) | {'SITE.json'},
                         {str(p.relative_to(self.output)) for p in self.output.rglob('*') if p.is_file()})
        second = self.root / 'repeat'
        build_site(self.fixture['repo'], self.path, second, initial_catalog=True, fingerprints=[self.fixture['fingerprint']])
        self.assertEqual({str(p.relative_to(self.output)): p.read_bytes() for p in self.output.rglob('*') if p.is_file()},
                         {str(p.relative_to(second)): p.read_bytes() for p in second.rglob('*') if p.is_file()})

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

    def test_unavailable_and_withdrawn_records_have_no_reading_or_source_outputs(self):
        self.catalog['works'][0]['editions'][0].update(status='withdrawn', notices=[{'date': '2026-11-02', 'text': 'Withdrawn. Original identity retained.'}])
        self.build()
        for root in (self.edition_root(0), self.edition_root(2, 'other-work')):
            html = self.page(root + '/index.html')
            self.assertIn('retained bibliographic record', html)
            self.assertFalse((self.output / root / 'read').exists())
            self.assertNotIn('>Read this edition<', html)
            self.assertTrue((self.output / root / 'citation.json').exists())
        sitemap = self.page('sitemap.xml')
        self.assertIn(self.fixture['revisions'][0], sitemap)
        self.assertNotIn(self.edition_root(0) + '/read/', sitemap)

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
                '-m', 'Press-Publication: 1\nPress-Work: works/field-notes/manuscript.md')
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
        fixtures = Path(__file__).parent / 'fixtures'
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
