"""Atomic periodic updates preserve trusted source history and old publications."""
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from guavajam_test_support.discovery_fixture import Publisher
from a_guavajam_static_website_generator.update import update, locked_root, atomic
from a_guavajam_static_website_generator.citation_routes import citation_href, passage_json
from a_guavajam_static_website_generator.renderer import render


class PeriodicUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.p = Publisher(self.root)
        self.output = self.root / 'deployed'; self.revision = self.p.revisions[0]

    def update(self, **options):
        options.setdefault('no_fetch', True)
        return update(self.p.repo, self.output, **self.p.trust, **options)

    def selected(self):
        return os.readlink(self.output / 'current')

    def revise(self, message='Revise the observation'):
        self.p.source('Field Notes', message)
        return self.p.publish(message)

    def test_first_build_noop_and_new_edition_render_once_each(self):
        with patch('a_guavajam_static_website_generator.view_cache.render', wraps=render) as called:
            self.assertTrue(self.update()['changed']); self.assertEqual(1, called.call_count)
            selected = self.selected()
            self.assertFalse(self.update()['changed']); self.assertEqual(1, called.call_count)
            self.assertEqual(selected, self.selected())
            sha = self.revise()
            self.assertTrue(self.update()['changed']); self.assertEqual(2, called.call_count)
        self.assertTrue((self.output / 'current/editions' / self.revision / 'read/source.gfm').is_file())
        self.assertTrue((self.output / 'current/editions' / sha / 'read/source.gfm').is_file())

    def test_origin_change_rebuilds_cached_views_and_equivalent_origin_is_a_noop(self):
        from test_site_origin import link_icons
        revision = self.revise('[Local](https://publication.example/work).')
        self.update(site_origin='https://publication.example')
        read = self.output / 'current/editions' / revision / 'read'
        source = (read / 'source.gfm').read_bytes()
        citation = (read / 'source.citation.json').read_bytes()
        self.assertFalse(link_icons((read / 'index.html').read_text())['https://publication.example/work'])
        selected = self.selected()
        self.assertTrue(self.update(site_origin='https://other.example')['changed'])
        self.assertNotEqual(selected, self.selected())
        self.assertTrue(link_icons((read / 'index.html').read_text())['https://publication.example/work'])
        self.assertFalse(self.update(site_origin='https://OTHER.example:443/')['changed'])
        self.assertEqual(source, (read / 'source.gfm').read_bytes())
        self.assertEqual(citation, (read / 'source.citation.json').read_bytes())
        selected = self.selected()
        with self.assertRaises(ValueError): self.update(site_origin='https://user@other.example')
        self.assertEqual(selected, self.selected())

    def test_housekeeping_advances_checkpoint_without_new_edition_or_render(self):
        self.update()
        (self.p.repo / 'README.md').write_text('Changed tooling notes.\n')
        self.p.git('add', 'README.md'); self.p.git('commit', '-qm', 'Housekeeping')
        with patch('a_guavajam_static_website_generator.view_cache.render', side_effect=AssertionError('reuse edition')):
            self.update()
        catalog = json.loads((self.output / 'current/CATALOG.json').read_text())
        self.assertEqual(1, len(catalog['editions']))
        self.assertEqual(self.p.git('rev-parse', 'HEAD'), catalog['tip'])

    def test_reused_render_gets_fresh_citations_without_polluting_private_cache(self):
        self.update()
        read = self.output / 'current/editions' / self.revision / 'read'
        paths = ('index.html', 'contents.html', 'passages.html', passage_json('index.html', 'opening'),
                 'source.gfm', 'source.citation.json')
        def without_generator_time(data):
            return re.sub(rb'<footer>.*?</footer>', b'<footer>generated</footer>', data, flags=re.S)
        expected = {name: without_generator_time((read / name).read_bytes()) for name in paths}
        cache = next((self.output / '.cache').glob('*/*/index.html')).parent
        self.assertNotIn('guavajam-passage-cite', (cache / 'index.html').read_text())
        self.assertFalse((cache / 'passages.html').exists())
        self.assertFalse((cache / 'passages').exists())
        self.assertNotIn('passages.html', json.loads((cache / 'VIEW.json').read_text())['files'])

        # A new catalog checkpoint rebuilds the site from the same immutable,
        # undecorated render. Citation decoration must run once on that copy.
        (self.p.repo / 'README.md').write_text('Tooling-only checkpoint.\n')
        self.p.git('add', 'README.md'); self.p.git('commit', '-qm', 'Housekeeping')
        with patch('a_guavajam_static_website_generator.view_cache.render', side_effect=AssertionError('reuse edition')):
            self.assertTrue(self.update()['changed'])
        self.assertEqual(expected, {name: without_generator_time((read / name).read_bytes()) for name in paths})
        html = (read / 'index.html').read_text()
        self.assertEqual(1, html.count('href="' + citation_href('content/manuscript.md', 0, 'opening') + '"'))
        self.assertNotIn('<script', html)
        canonical = json.loads((read / passage_json('index.html', 'opening')).read_text())
        self.assertEqual(self.revision, canonical['revision'])
        self.assertEqual('opening', canonical['anchor'])
        view = json.loads((read / 'VIEW.json').read_text())
        for name in paths:
            self.assertEqual(hashlib.sha256((read / name).read_bytes()).hexdigest(), view['files'][name])
        self.assertNotIn('guavajam-passage-cite', (cache / 'index.html').read_text())
        self.assertFalse((cache / 'passages.html').exists())

    def test_authentication_is_repeated_even_on_noop(self):
        self.update(); selected = self.selected()
        with self.assertRaises(ValueError):
            update(self.p.repo, self.output, no_fetch=True, fingerprints=['0' * 40])
        self.assertEqual(selected, self.selected())

    def test_unsigned_content_and_rewrites_keep_previous_generation(self):
        self.update(); selected = self.selected()
        self.p.source('Field Notes', 'Unsigned content.')
        self.p.git('add', 'content'); self.p.git('commit', '-qm', 'Unsigned')
        with self.assertRaises(ValueError): self.update()
        self.assertEqual(selected, self.selected())
        self.p.git('reset', '--hard', self.revision + '^')
        self.revise('Replacement history')
        with self.assertRaises(ValueError): self.update()
        self.assertEqual(selected, self.selected())

    def test_fetch_is_data_only_and_refuses_remote_rewind(self):
        remote = self.root / 'origin.git'
        subprocess.run(['git', 'clone', '--bare', '--quiet', str(self.p.repo), str(remote)], check=True)
        self.p.git('remote', 'add', 'origin', str(remote))
        checkout = self.p.git('rev-parse', 'HEAD')
        self.update(no_fetch=False)
        self.assertEqual(checkout, self.p.git('rev-parse', 'HEAD'))
        selected = self.selected()
        subprocess.run(['git', '--git-dir=' + str(remote), 'update-ref', 'refs/heads/main', self.revision + '^'], check=True)
        with self.assertRaises(ValueError): self.update(no_fetch=False)
        self.assertEqual(selected, self.selected())

    def test_lock_prevents_overlapping_generation_builds(self):
        with locked_root(self.output, self.p.repo.resolve()):
            self.assertEqual('busy', self.update()['status'])
        self.assertFalse((self.output / 'current').exists())

    def test_private_assets_and_failed_builds_never_switch(self):
        self.update(); selected = self.selected(); self.revise()
        with patch('a_guavajam_static_website_generator.update.verify_public_delivery', side_effect=ValueError('private asset')):
            with self.assertRaises(ValueError): self.update()
        self.assertEqual(selected, self.selected())
        with patch('a_guavajam_static_website_generator.update.build_site', side_effect=ValueError('failed rendering')):
            with self.assertRaises(ValueError): self.update()
        self.assertEqual(selected, self.selected())
        self.assertEqual([], list(self.output.glob('.staging-*')))

    def test_renderer_identity_tracks_packaged_artwork_not_python_bytecode(self):
        from a_guavajam_static_website_generator.update import renderer_identity
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); view = root / 'view'; core = root / 'core'
            view.mkdir(); core.mkdir()
            for path in [view / '__init__.py', core / '__init__.py']:
                path.write_text('# fixture module\n')
            with (patch('a_guavajam_static_website_generator.update.__file__', str(view / 'update.py')),
                  patch('guavajam.__file__', str(core / '__init__.py')),
                  patch('a_guavajam_static_website_generator.update.subprocess.run', return_value=SimpleNamespace(stdout=b'pandoc 3.12.1'))):
                previous = renderer_identity()
                for name in ['assets/lucide.json','assets/lucide-LICENSE.txt','templates/media.yml']:
                    path = view / name; path.parent.mkdir(exist_ok=True)
                    path.write_text('packaged rendering resource')
                    current = renderer_identity(); self.assertNotEqual(previous,current)
                    previous = current; path.write_text('updated rendering resource')
                    current = renderer_identity(); self.assertNotEqual(previous,current)
                    previous = current
                cache = view / '__pycache__'; cache.mkdir(); (cache / 'update.cpython-312.pyc').write_bytes(b'cache')
                self.assertEqual(previous,renderer_identity())

    def test_changed_renderer_and_explicit_rebuild_invalidate_cache(self):
        self.update()
        with patch('a_guavajam_static_website_generator.update.renderer_identity', return_value='new-renderer'), patch('a_guavajam_static_website_generator.view_cache.render', wraps=render) as called:
            self.assertTrue(self.update()['changed']); self.assertEqual(1, called.call_count)
        with patch('a_guavajam_static_website_generator.view_cache.render', wraps=render) as called:
            self.assertTrue(self.update(rebuild=True)['changed']); self.assertEqual(1, called.call_count)

    def test_unmanaged_output_and_missing_baseline_cannot_be_overwritten(self):
        self.output.mkdir(); (self.output / 'index.html').write_text('Existing site')
        with self.assertRaises(ValueError): self.update()
        self.assertEqual('Existing site', (self.output / 'index.html').read_text())
        shutil.rmtree(self.output); self.update(); (self.output / 'current').unlink()
        with self.assertRaisesRegex(ValueError, 'baseline'): self.update()

    def test_post_switch_state_failure_cannot_erase_history(self):
        def fail_last(path, data):
            if path.name == '.last.json': raise OSError('state failure')
            return atomic(path, data)
        with patch('a_guavajam_static_website_generator.update.atomic', side_effect=fail_last):
            with self.assertRaises(OSError): self.update()
        self.assertTrue((self.output / 'current').is_symlink())
        selected = self.selected()
        self.p.git('reset', '--hard', self.revision + '^')
        with self.assertRaises(ValueError): self.update()
        self.assertEqual(selected, self.selected())

    def test_cache_tampering_fails_closed_and_rebuild_recovers(self):
        self.update(); selected = self.selected()
        cached = next((self.output / '.cache').glob('*/*/index.html'))
        cached.write_text('Tampered')
        self.revise()
        with self.assertRaisesRegex(ValueError, 'corrupt view cache'): self.update()
        self.assertEqual(selected, self.selected())
        self.assertTrue(self.update(rebuild=True)['changed'])
