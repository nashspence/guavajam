"""Private branch previews share rendering, preserve source bytes and carry no authority."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from guavajam.preview import build_preview
from guavajam.update import verify_public_delivery
from guavajam_core.asset_policy import AssetError
from guavajam_core.asset_versions import pin_key
from guavajam_core.gateway_index import Index
from guavajam_test_support.discovery_fixture import Publisher
from guavajam_test_support.storage_fixture import MemoryStore, RAW, KEY


@unittest.skipUnless(shutil.which('gpg') and shutil.which('pandoc'), 'Git/GPG/Pandoc fixture')
class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.publisher = Publisher(self.root)
        self.enterContext(patch.dict(os.environ, {'GNUPGHOME': self.publisher.env['GNUPGHOME']}))
        self.output = self.root/'preview'
        self.store = MemoryStore(seed=True)
        pin_key(self.store, Index(self.store), KEY)

    def source(self, assets=True):
        (self.publisher.repo/'asset-policy.json').write_text('{"origin":"https://assets.example.org"}\n')
        body = 'Unpublished observation.'
        if assets:
            url = 'https://assets.example.org/'+KEY
            body += '\n\n![Diagram][diagram]\n\n[diagram]: '+url+'\n\n```text\n'+url+'\n```'
        self.publisher.source('field-notes', 'Field Notes', body)
        self.publisher.git('add', '-A'); self.publisher.git('commit', '-qm', 'Unpublished change')
        return self.publisher.git('rev-parse', 'HEAD')

    def preview(self, revision, *, work='field-notes', published='2026-11-15', label=None, output=None):
        return build_preview(self.publisher.repo, revision, work, output or self.output,
                             fingerprints=[self.publisher.fingerprint], store=self.store,
                             published=published, label=label)

    def test_whole_site_unsigned_includes_current_edition_and_proposed_revision(self):
        published = self.publisher.publish('2026-11-01')
        self.publisher.git('switch', '-c', 'work/new-edition')
        sha = self.source()
        before = (self.publisher.repo/'works/field-notes/manuscript.md').read_bytes()
        uploads = len(self.store.uploads)
        with patch('guavajam_core.asset_policy.public_addresses', side_effect=AssertionError('public access before release')):
            manifest = self.preview('refs/heads/work/new-edition')
        self.assertEqual(sha, manifest['revision'])
        self.assertFalse(manifest['candidate_signed'])
        self.assertTrue(manifest['provisional_identity'])
        self.assertFalse(manifest['publication'])
        self.assertEqual({KEY}, set(manifest['assets']))
        self.assertEqual(uploads, len(self.store.uploads))
        self.assertEqual(before, (self.publisher.repo/'works/field-notes/manuscript.md').read_bytes())
        self.assertEqual(sha, self.publisher.git('rev-parse', 'HEAD'))
        from guavajam.site_views import edition_root
        draft = edition_root('field-notes', sha)
        original = edition_root('field-notes', published)
        self.assertTrue((self.output/original/'read/index.html').is_file())
        self.assertTrue((self.output/draft/'read/index.html').is_file())
        self.assertTrue((self.output/draft/'read/cite.html').is_file())
        self.assertTrue((self.output/draft/'read/source.gfm').is_file())
        self.assertTrue((self.output/draft/'citation.json').is_file())
        homepage = (self.output/'index.html').read_text()
        workpage = (self.output/'works/field-notes/index.html').read_text()
        self.assertIn('field-notes', homepage)
        self.assertIn('Proposed (unsigned)', workpage)
        self.assertIn(published, workpage)
        self.assertIn(sha, workpage)
        self.assertIn('Provisional citation preview', (self.output/draft/'index.html').read_text())
        self.assertIn(draft, (self.output/'sitemap.xml').read_text())
        self.assertIn(draft, (self.output/'feed.xml').read_text())
        html = (self.output/draft/'read/index.html').read_text()
        self.assertIn('src="https://assets.example.org/'+KEY+'"', html)
        self.assertNotIn('/_assets/', html)
        self.assertIn('https://assets.example.org/'+KEY, html) # Literal code remains text.
        self.assertIn('UNSIGNED PREVIEW', html)
        self.assertEqual(before, (self.output/draft/'read/source.gfm').read_bytes())
        self.assertEqual(0o700, self.output.stat().st_mode & 0o777)
        self.assertNotIn('PREVIEW.json', manifest['files'])
        for name, digest in manifest['files'].items():
            self.assertEqual(digest, hashlib.sha256((self.output/name).read_bytes()).hexdigest())

    def test_first_edition_is_in_full_site_not_a_separate_preview_page(self):
        self.publisher.git('switch', '-c', 'work/new-title')
        self.publisher.source('new-title', 'New Title', 'A proposed first edition.')
        self.publisher.git('add', '-A'); self.publisher.git('commit', '-qm', 'New private manuscript')
        sha = self.publisher.git('rev-parse', 'HEAD')
        manifest = self.preview(sha, work='new-title')
        catalog = json.loads((self.output/'CATALOG.json').read_text())
        self.assertEqual(['new-title'], [w['slug'] for w in catalog['works']])
        self.assertIn('New Title', (self.output/'index.html').read_text())
        self.assertIn('Proposed', (self.output/'works/new-title/index.html').read_text())
        self.assertTrue((self.output/'works/new-title/editions'/sha/'read/index.html').exists())
        self.assertFalse((self.output/'works/new-title/preview/index.html').exists())
        self.assertFalse((self.output/'SITE.json').exists())
        self.assertFalse(manifest['candidate_signed'])

    def test_signed_candidate_is_exact_unmodified_production_site_bytes(self):
        from guavajam_core.publication_protocol import discover
        from guavajam.site import build_site
        published = self.publisher.publish('2026-11-01')
        self.publisher.git('switch', '-c', 'work/revised')
        self.source(assets=False)
        edition = self.publisher.publish('2026-11-15')
        manifest = self.preview(edition, published=None)
        self.assertTrue(manifest['candidate_signed'])
        self.assertFalse(manifest['provisional_identity'])
        catalog, _ = discover(self.publisher.repo, ref=edition, fingerprints=[self.publisher.fingerprint])
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            catalog_path = directory/'catalog.json'; catalog_path.write_bytes(json.dumps(catalog).encode())
            actual = directory/'production'
            build_site(self.publisher.repo, catalog_path, actual,
                       fingerprints=[self.publisher.fingerprint], initial_catalog=True)
            names = {p.relative_to(actual).as_posix() for p in actual.rglob('*') if p.is_file()}
            self.assertEqual(names, set(manifest['files']))
            for name in names:
                self.assertEqual((self.output/name).read_bytes(), (actual/name).read_bytes(), name)
        self.assertIn(published, (self.output/'works/field-notes/index.html').read_text())
        self.assertIn(edition, (self.output/'works/field-notes/index.html').read_text())

    def test_explicit_signed_work_recommendation_is_not_silently_overwritten(self):
        original = self.publisher.publish('2026-11-01')
        self.publisher.settings['works'] = {'field-notes': {'recommended_revision': original}}
        self.publisher.amend_settings()
        self.publisher.git('switch', '-c', 'work/another')
        sha = self.source(assets=False)
        self.preview(sha)
        catalog = json.loads((self.output/'CATALOG.json').read_text())
        self.assertEqual(original, catalog['works'][0]['recommended_revision'])
        self.assertIn(sha, [e['revision'] for e in catalog['works'][0]['editions']])

    def test_unreviewed_publisher_site_change_cannot_be_previewed_as_approved(self):
        self.publisher.git('switch', '-c', 'work/unreviewed-admin')
        sha = self.source(assets=False)
        (self.publisher.repo/'press-site.json').write_text('{"schema":"press.site.v1","publisher":"Unreviewed","base_url":"https://publisher.example"}\n')
        self.publisher.git('add', '-A');self.publisher.git('commit', '-qm', 'Unsigned administrative change')
        with self.assertRaises(ValueError):
            self.preview('refs/heads/work/unreviewed-admin')
        self.assertFalse(self.output.exists())

    def test_private_output_rejects_stale_base_missing_authority_and_corruption(self):
        self.publisher.git('switch', '-c', 'work/revised')
        sha = self.source()
        with self.assertRaises(AssetError):
            self.preview(sha, output=self.publisher.repo/'preview')
        with self.assertRaisesRegex(ValueError, 'publisher signing policy'):
            build_preview(self.publisher.repo, sha, 'field-notes', self.output, store=self.store,
                          published='2026-11-15')
        with self.assertRaisesRegex(ValueError, 'PUBLISHED'):
            build_preview(self.publisher.repo, sha, 'field-notes', self.output, store=self.store,
                          fingerprints=[self.publisher.fingerprint])
        self.store.bytes[(KEY, 'version_0')] = b'!'*len(RAW)
        with self.assertRaises(AssetError):
            self.preview(sha)
        self.assertFalse(self.output.exists())

    def test_preview_rejects_branch_not_descended_from_reviewed_base(self):
        self.publisher.git('switch', '-c', 'work/private')
        self.source(assets=False)
        self.publisher.git('switch', 'main')
        self.publisher.source('field-notes', 'Field Notes', 'An unrelated main change.')
        self.publisher.git('add', '-A'); self.publisher.git('commit', '-qm', 'Main advanced')
        with self.assertRaises(ValueError):
            self.preview('refs/heads/work/private')


class ReadinessTests(unittest.TestCase):
    def test_old_generation_receipts_without_public_readiness_are_rejected(self):
        from guavajam.update import current
        from guavajam_core.catalog import CatalogError
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = 'generations/' + 'a'*32 + '/site'
            site = root / name
            site.mkdir(parents=True)
            (site / 'CATALOG.json').write_text('{}')
            (root / 'current').symlink_to(name)
            receipt = {'schema': 'guavajam.update.v1', 'catalog_sha256': hashlib.sha256(b'{}').hexdigest()}
            for flag in [None, False, 'true']:
                (site.parent / 'STATE.json').write_text(json.dumps({**receipt, 'public_delivery_verified': flag}))
                with self.assertRaises(CatalogError):
                    current(root)
            (site.parent / 'STATE.json').write_text(json.dumps({**receipt, 'public_delivery_verified': True}))
            self.assertEqual(site, current(root)[0])

    def test_only_new_or_newly_readable_editions_need_public_qualification(self):
        previous = {'works': [{'editions': [{'revision': 'a'*40, 'status': 'available'},
                                           {'revision': 'b'*40, 'status': 'unavailable'}]}]}
        catalog = {'works': [{'editions': [{'revision': 'a'*40, 'status': 'available'},
                                          {'revision': 'b'*40, 'status': 'available'},
                                          {'revision': 'c'*40, 'status': 'withdrawn'},
                                          {'revision': 'd'*40, 'status': 'unavailable'}]}]}
        with patch('guavajam_core.publication_commit.verify') as verify:
            verify_public_delivery(Path('.'), catalog, previous=previous, fingerprints=['F'*40])
            self.assertEqual(['b'*40, 'c'*40], [call.args[1] for call in verify.call_args_list])
            self.assertTrue(all(call.kwargs['online_media'] for call in verify.call_args_list))
            self.assertFalse(any('store' in call.kwargs for call in verify.call_args_list))
        with patch('guavajam_core.publication_commit.verify', side_effect=AssetError('not released')):
            with self.assertRaises(AssetError):
                verify_public_delivery(Path('.'), catalog, fingerprints=['F'*40])


if __name__ == '__main__':
    unittest.main()
