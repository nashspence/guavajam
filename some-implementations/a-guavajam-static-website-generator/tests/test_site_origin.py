"""Deployment origins classify links without changing source or publication identity."""
from contextlib import redirect_stdout
from html.parser import HTMLParser
from io import StringIO
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest

from guavajam_test_support.discovery_fixture import Publisher
from a_guavajam_static_website_generator.icons import normalize_site_origin
from a_guavajam_static_website_generator.renderer import main
from a_guavajam_static_website_generator.site import build_site


def link_icons(html):
    class Links(HTMLParser):
        def __init__(self):
            super().__init__(); self.links = {}; self.current = None
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'a':
                self.current = attrs.get('href'); self.links[self.current] = []
            if tag == 'svg' and self.current is not None:
                self.links[self.current].append(attrs.get('class', ''))
        def handle_endtag(self, tag):
            if tag == 'a': self.current = None
    parser = Links(); parser.feed(html)
    return {href: any('guavajam-icon-external-link' in icon for icon in icons)
            for href, icons in parser.links.items()}


class SiteOriginTests(unittest.TestCase):
    def test_origin_is_optional_normalized_and_has_no_url_credentials_or_path(self):
        self.assertIsNone(normalize_site_origin(None))
        for value, expected in [('https://SITE.test:443/', 'https://site.test'),
                                ('http://localhost:8080', 'http://localhost:8080'),
                                ('https://[::1]:8443', 'https://[::1]:8443'),
                                ('https://bücher.example', 'https://xn--bcher-kva.example')]:
            self.assertEqual(expected, normalize_site_origin(value))
        for value in ['', '//site.test', 'file:///tmp/site', 'https://user@site.test',
                      'https://user:password@site.test', 'https://site.test/path',
                      'https://site.test/?query=yes', 'https://site.test/#part',
                      'https://site.test:99999', 'https://site.test:invalid',
                      'https://site.test\n', 'https://site.test\\path']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_site_origin(value)

    def test_every_generation_command_accepts_the_optional_origin(self):
        for command in ['render', 'site', 'index', 'update', 'preview', 'preview-docker']:
            output = StringIO()
            with self.subTest(command=command), redirect_stdout(output), self.assertRaises(SystemExit) as result:
                main([command, '--help'])
            self.assertEqual(0, result.exception.code)
            self.assertIn('--site-origin', output.getvalue())

    def test_make_targets_forward_origin_as_one_optional_argument(self):
        app = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / 'capture.py'
            capture.write_text('import json, sys; print(json.dumps(sys.argv[1:]))')
            for target in ['render', 'site', 'index', 'update', 'preview', 'preview-docker']:
                for origin in [None, 'https://publication.example:8443']:
                    env = {**os.environ, 'REPO': 'source', 'REV': 'a' * 40, 'OUTPUT': 'output',
                           'SSH_ALLOWED_SIGNERS': 'allowed-signers', 'SOURCE_REF': 'refs/heads/draft',
                           'INDEX': 'index.yaml', 'TRUST': 'trust.yaml', 'PREVIEW': '0'}
                    env.pop('SITE_ORIGIN', None)
                    if origin is not None: env['SITE_ORIGIN'] = origin
                    with self.subTest(target=target, origin=origin):
                        result = subprocess.run(['make', '--no-print-directory', target,
                                                 'PYTHON=' + sys.executable + ' ' + str(capture)],
                                                cwd=app, env=env, capture_output=True, text=True, check=True)
                        args = json.loads(result.stdout.strip())
                        if origin is None:
                            self.assertNotIn('--site-origin', args)
                        else:
                            self.assertEqual(1, args.count('--site-origin'))
                            self.assertEqual(origin, args[args.index('--site-origin') + 1])

    def test_complete_site_and_cache_follow_origin_without_changing_citations_or_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); publisher = Publisher(root / 'publisher')
            publisher.source('Linked work', '[Local](https://publication.example/work) and [Remote](https://other.example/work).')
            supporting = publisher.repo / 'content/supporting'; supporting.mkdir()
            (supporting / 'note.md').write_text('---\ntitle: Note\n---\n\n<a id="evidence"></a>\n\n[Local](https://publication.example/note).\n')
            revision = publisher.publish('Add linked content')
            snapshots = []
            for number, origin in enumerate(['https://publication.example', 'https://other.example', 'https://PUBLICATION.example:443/']):
                output = root / str(number)
                build_site(publisher.repo, output, **publisher.trust, site_origin=origin,
                           repository='https://publication.example/work.git', view_cache=root / 'cache')
                read = output / 'editions' / revision / 'read'
                expected_external = number == 1
                for name, href in [('index.html', 'https://publication.example/work'),
                                   ('supporting/note.html', 'https://publication.example/note')]:
                    self.assertEqual(expected_external, link_icons((read / name).read_text())[href])
                self.assertFalse(link_icons((read.parent / 'metadata.html').read_text())['source.bundle'])
                self.assertEqual(not expected_external, link_icons((read / 'index.html').read_text())['https://other.example/work'])
                view = json.loads((read / 'VIEW.json').read_text())
                for name, digest in view['files'].items():
                    self.assertEqual(digest, hashlib.sha256((read / name).read_bytes()).hexdigest())
                snapshots.append([(read / name).read_bytes() for name in
                                  ['source.gfm', 'source.citation.json', 'supporting/note.gfm', 'supporting/note.citation.json']])
            self.assertEqual(snapshots[0], snapshots[1]); self.assertEqual(snapshots[0], snapshots[2])

    def test_generation_cli_forwards_origin_to_the_actual_renderer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); publisher = Publisher(root / 'publisher')
            publisher.source('Linked work', '[Local](https://publication.example/work).')
            revision = publisher.publish('Add link')
            for command, trust in [('render', ['--ssh-allowed-signers', str(publisher.allowed_signers)]),
                                   ('preview', [])]:
                output = root / command
                selection = ['--revision', revision] if command == 'render' else ['--ref', revision]
                with redirect_stdout(StringIO()):
                    self.assertEqual(0, main([command, '--repo', str(publisher.repo), '--output', str(output),
                                              *selection, *trust, '--site-origin', 'https://publication.example']))
                self.assertFalse(link_icons((output / 'index.html').read_text())['https://publication.example/work'])
