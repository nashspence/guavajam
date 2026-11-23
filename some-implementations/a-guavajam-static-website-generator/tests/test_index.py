"""Two independently signed repositories, one public directory, no publisher code execution."""
import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import yaml
from a_guavajam_static_website_generator.index import prepare_source, read_index, read_trust, update_index
from guavajam_test_support.discovery_fixture import Publisher


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.a = Publisher(self.root / 'a'); self.b = Publisher(self.root / 'b')
        self.urls = ['https://publisher.example/a.git', 'https://publisher.example/b.git']
        self.index = self.root / 'index.yaml'; self.trust = self.root / 'trust.yaml'
        self.output = self.root / 'collection'
        self.sources = dict(zip(self.urls, [self.a.repo, self.b.repo]))
        self.policies = {url: {'allowed_signers': str(p.allowed_signers)} for url, p in zip(self.urls, [self.a, self.b])}
        self.write_index(self.urls); self.write_trust()
        mocked = patch('a_guavajam_static_website_generator.index.prepare_source', side_effect=lambda _, url: self.sources[url])
        mocked.start(); self.addCleanup(mocked.stop)

    def write_index(self, urls):
        self.index.write_text(yaml.safe_dump({'repositories': urls}))

    def write_trust(self):
        self.trust.write_text(yaml.safe_dump(self.policies))

    def update(self, **options):
        return update_index(self.index, self.trust, self.output, **options)

    def selected(self):
        return os.readlink(self.output / 'current')

    def test_collection_origin_changes_regenerate_cached_work_metadata(self):
        from test_site_origin import link_icons
        self.update(site_origin='https://github.com')
        source = next((self.output / 'current/sources').glob('*/editions/*/metadata.html'))
        urls = link_icons(source.read_text())
        generator = 'https://github.com/nashspence/guavajam'
        self.assertFalse(urls[generator])
        self.assertFalse(urls['source.bundle'])
        self.assertFalse(any(url in urls for url in self.urls))
        selected = self.selected()
        self.assertTrue(self.update(site_origin='https://different.example')['changed'])
        self.assertNotEqual(selected, self.selected())
        self.assertTrue(link_icons(source.read_text())[generator])
        self.assertFalse(self.update(site_origin='https://DIFFERENT.example:443/')['changed'])

    def test_two_repositories_keep_distinct_histories_and_noop_reverifies(self):
        self.b.source('A Different Work', 'Independent source material.')
        self.b.publish('Explain a separate work')
        self.assertEqual('updated', self.update()['status'])
        entries = json.loads((self.output / 'current/INDEX.json').read_text())['repositories']
        self.assertEqual(self.urls, [e['repository'] for e in entries])
        self.assertEqual(2, len({e['path'] for e in entries}))
        counts = [len(json.loads((self.output / 'current' / e['path'] / 'CATALOG.json').read_text())['editions']) for e in entries]
        self.assertEqual([1, 2], counts)
        self.assertEqual('unchanged', self.update()['status'])
        self.policies[self.urls[1]] = {'allowed_signers': str(self.a.allowed_signers)}
        self.write_trust(); selected = self.selected()
        with self.assertRaises(ValueError): self.update()
        self.assertEqual(selected, self.selected())

    def test_one_bad_repository_prevents_any_collection_switch(self):
        self.update(); selected = self.selected()
        self.a.source('Field Notes', 'A signed improvement.'); self.a.publish('Improve first work')
        self.b.source('Field Notes', 'Unsigned second work.')
        self.b.git('add', 'content'); self.b.git('commit', '-qm', 'Unsigned source')
        with self.assertRaises(ValueError): self.update()
        self.assertEqual(selected, self.selected())

    def test_removal_keeps_private_baseline_for_later_reintroduction(self):
        self.update()
        self.write_index([self.urls[0]]); self.update()
        first = self.b.revisions[0]
        self.b.git('reset', '--hard', first + '^')
        self.b.source('Replacement', 'Replacement history.'); self.b.publish('Rewrite history')
        self.write_index(self.urls); selected = self.selected()
        with self.assertRaises(ValueError): self.update()
        self.assertEqual(selected, self.selected())

    def test_index_never_supplies_or_substitutes_signer_trust(self):
        self.index.write_text(yaml.safe_dump({'repositories': self.urls, 'allowed_signers': str(self.a.allowed_signers)}))
        with self.assertRaises(ValueError): self.update()
        self.write_index(self.urls); del self.policies[self.urls[1]]; self.write_trust()
        with self.assertRaises(ValueError): self.update()
        self.assertFalse((self.output / 'current').exists())

    def test_every_branch_of_input_is_bounded_and_uses_plain_https_repositories(self):
        for url in ['file:///private/repo', 'http://publisher.example/repo', 'ext::sh command',
                    'https://user:password@publisher.example/repo', 'https://publisher.example/repo?token=secret']:
            with self.subTest(url=url):
                self.write_index([url])
                with self.assertRaises(ValueError): read_index(self.index)
        for raw in ['repositories: []\nrepositories: []\n', 'repositories: &x [*x]\n', 'repositories: !!python/object:object {}\n']:
            self.index.write_text(raw)
            with self.assertRaises(ValueError): read_index(self.index)
        self.write_index([self.urls[0], self.urls[0]])
        with self.assertRaises(ValueError): read_index(self.index)

    def test_same_commit_mirrors_can_be_listed_without_identity_collision(self):
        self.sources[self.urls[1]] = self.a.repo
        self.policies[self.urls[1]] = dict(self.policies[self.urls[0]]); self.write_trust()
        self.update()
        entries = json.loads((self.output / 'current/INDEX.json').read_text())['repositories']
        self.assertEqual(entries[0]['tip'], entries[1]['tip'])
        self.assertNotEqual(entries[0]['path'], entries[1]['path'])

    def test_missing_or_tampered_retained_generation_never_resets_history(self):
        self.update(); (self.output / 'current/INDEX.json').write_text('{}')
        with self.assertRaises(ValueError): self.update()
        (self.output / 'current').unlink()
        with self.assertRaises(ValueError): self.update()


class RepositoryTransportTests(unittest.TestCase):
    def test_real_bare_clone_fetch_and_rewind_rejection_with_operator_url_rewrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); publisher = Publisher(root / 'publisher')
            upstream = root / 'upstream.git'
            subprocess.run(['git', 'clone', '--bare', '--quiet', str(publisher.repo), str(upstream)],
                           check=True, capture_output=True)
            state = root / 'state'; state.mkdir()
            url = 'https://publisher.example/work.git'
            environment = {'GIT_CONFIG_SYSTEM': os.devnull, 'GIT_CONFIG_GLOBAL': os.devnull,
                           'GIT_CONFIG_COUNT': '1',
                           'GIT_CONFIG_KEY_0': 'url.' + upstream.as_uri() + '.insteadOf',
                           'GIT_CONFIG_VALUE_0': url}
            with patch.dict(os.environ, environment):
                repo = prepare_source(state, url)
                def git(*args):
                    return subprocess.check_output(['git', *args], cwd=repo, stderr=subprocess.PIPE).decode().strip()
                self.assertEqual('true', git('rev-parse', '--is-bare-repository'))
                self.assertEqual(url, git('config', '--get', 'remote.origin.url'))
                self.assertFalse((repo / 'content').exists(), 'fetching source never checks it out')
                first = publisher.revisions[0]
                self.assertEqual(first, git('rev-parse', 'refs/heads/main'))
                publisher.source('Field Notes', 'A signed fast-forward revision.')
                second = publisher.publish('Advance published source')
                subprocess.run(['git', 'push', '--quiet', str(upstream), 'main'], cwd=publisher.repo,
                               check=True, capture_output=True)
                self.assertEqual(repo, prepare_source(state, url))
                self.assertEqual(second, git('rev-parse', 'refs/heads/main'))
                subprocess.run(['git', '--git-dir=' + str(upstream), 'update-ref', 'refs/heads/main', first],
                               check=True, capture_output=True)
                with self.assertRaises(ValueError): prepare_source(state, url)
                self.assertEqual(second, git('rev-parse', 'refs/heads/main'))
