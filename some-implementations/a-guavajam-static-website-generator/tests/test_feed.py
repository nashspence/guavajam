"""Atom entry identity, collection event continuity, and script-free discoverability."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from a_guavajam_static_website_generator.feed import (
    ATOM, advance_collection, validate_state, write_collection_feed, write_work_feed)
from a_guavajam_static_website_generator.directory_pages import search_listing_open
from a_guavajam_static_website_generator.site import page
from a_guavajam_static_website_generator.update import digest

NS = {'a': ATOM}


def nodes(path):
    return ET.parse(path).getroot().findall('a:entry', NS)


def category(entry):
    return entry.find('a:category', NS).get('term')


def work_catalog(*shas):
    return {'schema': 'guavajam.catalog.v1', 'hash_algorithm': 'sha1',
            'tip': shas[-1], 'editions': [{'revision': sha, 'tree': 'c' * 40,
                                         'published': '2025-01-01', 'message': f'Edition {i} & <details>'}
                                        for i, sha in enumerate(shas, 1)]}


class CollectionFeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.a, self.b = 'https://example.org/a.git', 'https://example.org/b.git'
        self.first, self.second, self.third = 'a' * 40, 'b' * 40, 'c' * 40

    def build(self, works):
        """Mimic only already-verified generated metadata: no SSH fixture needed."""
        entries = []
        for url, title, catalog in works:
            relative = 'sources/' + digest(url.encode())
            output = self.root / relative
            output.mkdir(parents=True, exist_ok=True)
            (output / 'CATALOG.json').write_text(json.dumps(catalog))
            (output / 'SITE.json').write_text(json.dumps({'title': title}))
            for edition in catalog['editions']:
                read = output / 'editions' / edition['revision'] / 'read'
                read.mkdir(parents=True, exist_ok=True)
                (read / 'VIEW.json').write_text(json.dumps({'facts': {'title': title}}))
            entries.append({'repository': url, 'path': relative, 'tip': catalog['tip']})
        return entries

    def transition(self, before, catalogs, works):
        entries = self.build(works)
        with patch.dict(os.environ, {'SOURCE_DATE_EPOCH': '1767225600'}):
            state = advance_collection(before, catalogs, entries, self.root)
        write_collection_feed(self.root, state)
        return state, nodes(self.root / 'feed.atom')

    def test_add_upgrade_remove_readd_without_history_replay(self):
        one = work_catalog(self.first, self.second)
        state, entries = self.transition(None, {}, [(self.a, 'Field & Notes', one)])
        self.assertEqual(['work-added'], [category(e) for e in entries])
        self.assertEqual(2, state['events'][0]['version'])
        self.assertEqual('Work added: Field & Notes', entries[0].findtext('a:title', namespaces=NS))
        self.assertTrue(entries[0].find('a:link', NS).get('href').endswith('/index.html'))
        first_id = entries[0].findtext('a:id', namespaces=NS)
        feed_id = ET.parse(self.root / 'feed.atom').getroot().findtext('a:id', namespaces=NS)

        # Multiple new editions between polls produce separate events in Git order.
        updated = work_catalog(self.first, self.second, self.third, 'd' * 40)
        state2, entries = self.transition(state, {self.a: one}, [(self.a, 'Revised <Notes>', updated)])
        self.assertEqual(['new-edition', 'new-edition', 'work-added'], [category(e) for e in entries])
        self.assertIn('Edition 4 & <details>', entries[0].findtext('a:summary', namespaces=NS))
        self.assertIn('/editions/' + 'd' * 40 + '/read/index.html', entries[0].find('a:link', NS).get('href'))
        self.assertEqual(first_id, entries[-1].findtext('a:id', namespaces=NS))
        self.assertEqual(feed_id, ET.parse(self.root / 'feed.atom').getroot().findtext('a:id', namespaces=NS))
        ids = [e.findtext('a:id', namespaces=NS) for e in entries]
        self.assertEqual(len(ids), len(set(ids)))

        unchanged, same_entries = self.transition(state2, {self.a: updated}, [(self.a, 'Revised <Notes>', updated)])
        self.assertEqual(state2, unchanged)
        self.assertEqual(ids, [e.findtext('a:id', namespaces=NS) for e in same_entries])
        removed, entries = self.transition(unchanged, {self.a: updated}, [])
        self.assertEqual('work-removed', category(entries[0]))
        self.assertEqual(self.a, entries[0].find('a:link', NS).get('href'))
        self.assertIn('not been retracted', entries[0].findtext('a:summary', namespaces=NS))
        self.assertEqual(4, len(removed['events']))
        readded, entries = self.transition(removed, {self.a: updated}, [(self.a, 'Revised <Notes>', updated)])
        self.assertEqual('work-added', category(entries[0]))
        self.assertEqual(5, len(readded['events']))
        self.assertEqual(2, sum(category(e) == 'work-added' for e in entries))
        self.assertEqual(2, sum(category(e) == 'new-edition' for e in entries))
        self.assertNotEqual(first_id, entries[0].findtext('a:id', namespaces=NS))

    def test_added_works_do_not_replay_preexisting_editions_even_when_other_work_updates(self):
        one = work_catalog(self.first)
        before, _ = self.transition(None, {}, [(self.a, 'First', one)])
        two = work_catalog(self.first, self.second)
        many = work_catalog(self.first, self.second, self.third)
        after, entries = self.transition(before, {self.a: one},
                                         [(self.a, 'First', two), (self.b, 'Second', many)])
        self.assertEqual(['work-added', 'new-edition', 'work-added'], [category(e) for e in entries])
        self.assertEqual(3, after['events'][-1]['version'])
        self.assertEqual(1, sum(e['kind'] == 'new-edition' for e in after['events']))

    def test_legacy_checkpoint_migration_suppresses_historical_events(self):
        one = work_catalog(self.first, self.second)
        state, entries = self.transition(None, {self.a: one}, [(self.a, 'Old work', one)])
        self.assertEqual([], entries)
        self.assertEqual([self.a], state['members'])
        newer = work_catalog(self.first, self.second, self.third)
        later, entries = self.transition(state, {self.a: one}, [(self.a, 'Old work', newer)])
        self.assertEqual(['new-edition'], [category(e) for e in entries])
        self.assertEqual(1, len(later['events']))

    def test_event_log_is_bounded_and_atom_document_is_parseable(self):
        state, _ = self.transition(None, {}, [])
        for i in range(220):
            url = f'https://example.org/work-{i}.git'
            state, _ = self.transition(state, {}, [(url, 'Name', work_catalog(self.first))])
            state, _ = self.transition(state, {}, [])
        self.assertLessEqual(len(state['events']), 200)
        self.assertEqual(50, len(nodes(self.root / 'feed.atom')))
        self.assertEqual(50, len({e.findtext('a:id', namespaces=NS) for e in nodes(self.root / 'feed.atom')}))

    def test_invalid_retained_feed_state_rejected(self):
        state, _ = self.transition(None, {}, [(self.a, 'Initial', work_catalog(self.first))])
        for mutate in [lambda d: d['members'].append(self.a),
                       lambda d: d['events'][0].update(kind='unknown'),
                       lambda d: d['events'][0].update(observed='not-a-date'),
                       lambda d: d['events'][0].update(id='urn:uuid:not-an-id'),
                       lambda d: d['events'][0].update(revision=self.first)]:
            invalid = copy.deepcopy(state)
            mutate(invalid)
            with self.assertRaises(ValueError):
                validate_state(invalid)


class WorkFeedTests(unittest.TestCase):
    def test_real_git_commit_timestamps_without_authorizing_new_editions(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'; repo.mkdir()
            subprocess.run(['git', 'init', '-q', '-b', 'main'], cwd=repo, check=True)
            subprocess.run(['git', 'config', 'user.name', 'Publisher'], cwd=repo, check=True)
            subprocess.run(['git', 'config', 'user.email', 'publisher@example.org'], cwd=repo, check=True)
            shas = []
            for i, (date, message) in enumerate([('2025-01-02T03:04:05+00:00', 'Initial'),
                                                  ('2024-12-30T00:00:00+00:00', 'Revised & <clarified>\x01')]):
                (repo / 'content.txt').write_text(str(i))
                subprocess.run(['git', 'add', 'content.txt'], cwd=repo, check=True)
                subprocess.run(['git', 'commit', '-qm', message], cwd=repo, check=True,
                               env={**os.environ, 'GIT_COMMITTER_DATE': date, 'GIT_AUTHOR_DATE': date})
                shas.append(subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip())
            site = Path(temp) / 'site'; site.mkdir()
            catalog = work_catalog(*shas)
            catalog['editions'][-1]['message'] = 'Revised & <clarified>\x01'
            for sha in shas:
                view = site / 'editions' / sha / 'read'
                view.mkdir(parents=True)
                (view / 'VIEW.json').write_text(json.dumps({'facts': {'title': 'A & B', 'author': 'Writer'}}))
            write_work_feed(site, repo, catalog)
            feed = ET.parse(site / 'feed.atom').getroot()
            events = nodes(site / 'feed.atom')
            self.assertEqual(2, len(events))
            self.assertEqual(['A & B — v2', 'A & B — v1'], [e.findtext('a:title', namespaces=NS) for e in events])
            self.assertEqual('2024-12-30T00:00:00Z', events[0].findtext('a:published', namespaces=NS))
            self.assertEqual('2025-01-02T03:04:05Z', events[1].findtext('a:published', namespaces=NS))
            self.assertIn('Revised & <clarified>', events[0].findtext('a:summary', namespaces=NS))
            self.assertIn('\ufffd', events[0].findtext('a:summary', namespaces=NS))
            self.assertEqual('Writer', events[0].findtext('a:author/a:name', namespaces=NS))
            before = [e.findtext('a:id', namespaces=NS) for e in events]
            write_work_feed(site, repo, catalog)
            self.assertEqual(before, [e.findtext('a:id', namespaces=NS) for e in nodes(site / 'feed.atom')])
            self.assertEqual(feed.findtext('a:id', namespaces=NS), ET.parse(site / 'feed.atom').getroot().findtext('a:id', namespaces=NS))

    def test_heading_icon_and_head_autodiscovery_are_direct_links_without_js(self):
        for title, label in [('Works', 'collection updates'), ('Versions', 'work editions')]:
            body = search_listing_open(title, 2, feed_href='feed.atom', feed_title='Subscribe to ' + label)
            html = page(title, body, feed_href='feed.atom', feed_title=label)
            self.assertIn('guavajam-icon-rss', html)
            self.assertIn('aria-label="Subscribe to ' + label + '"', html)
            self.assertIn('class="guavajam-feed-link" href="feed.atom"', html)
            self.assertIn('type="application/atom+xml"', html)
            self.assertIn('<link rel="alternate"', html)
            self.assertEqual(1, html.count('<h1'))
            self.assertNotIn('<script', html)
            self.assertLess(html.index('data-static-heading='), html.index('class="guavajam-feed-link"'))


if __name__ == '__main__':
    unittest.main()


class CollectionPipelineTests(unittest.TestCase):
    """Exercise both deployment paths without requiring SSH fixture binaries."""

    def setUp(self):
        import yaml
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.url = 'https://example.org/work.git'
        self.index_file = self.root / 'index.yaml'
        self.trust_file = self.root / 'trust.yaml'
        self.catalog = work_catalog('a' * 40)
        self.repo = self.root / 'source'; self.repo.mkdir()
        self.index_file.write_text(yaml.safe_dump({'repositories': [self.url]}))
        self.trust_file.write_text(yaml.safe_dump({self.url: {'fingerprints': ['f' * 40]}}))
        self.output = self.root / 'generated'
        self.previous = self.root / 'previous.json'
        self.previous.write_text(json.dumps({'schema': 'guavajam.index-checkpoint.v1', 'catalogs': {}}))

        def build(_repo, target, **options):
            target.mkdir(parents=True)
            (target / 'CATALOG.json').write_text(json.dumps(self.catalog))
            (target / 'SITE.json').write_text(json.dumps({'title': 'Verified work'}))
            for edition in self.catalog['editions']:
                read = target / 'editions' / edition['revision'] / 'read'
                read.mkdir(parents=True)
                (read / 'VIEW.json').write_text(json.dumps({'facts': {'title': 'Verified work'}}))
            return {'title': 'Verified work'}

        def index(site, _entries):
            (Path(site) / 'index.sqlite').write_bytes(b'test index')

        def discover(_repo, **_options):
            return copy.deepcopy(self.catalog), {'publications': [e['revision'] for e in self.catalog['editions']]}

        namespace = 'a_guavajam_static_website_generator.index.'
        for name, stub in [('prepare_source', lambda _root, _url: self.repo),
                           ('discover', discover),
                           ('verify_public_delivery', lambda *_a, **_kw: None),
                           ('build_site', build),
                           ('emit_browser_assets', lambda _path: False),
                           ('create_index', index),
                           ('homepage', lambda *_a, **_kw: '<html></html>'),
                           ('renderer_identity', lambda: 'test-renderer')]:
            mocked = patch(namespace + name, side_effect=stub)
            mocked.start(); self.addCleanup(mocked.stop)

    def test_pages_checkpoint_stores_events_outside_public_html(self):
        from a_guavajam_static_website_generator.index import build_pages
        def run(name):
            site, state = self.root / ('pages-' + name), self.root / ('state-' + name + '.json')
            build_pages(self.index_file, self.trust_file, site, self.previous, state)
            self.previous.write_bytes(state.read_bytes())
            return site, json.loads(state.read_text())
        one, state = run('one')
        self.assertEqual('work-added', category(nodes(one / 'feed.atom')[0]))
        self.assertFalse((one / 'STATE.json').exists())
        self.assertEqual(1, len(state['feed']['events']))
        self.assertEqual(1, len(state['catalogs']))
        self.catalog = work_catalog('a' * 40, 'b' * 40)
        two, state = run('two')
        self.assertEqual(['new-edition', 'work-added'], [category(e) for e in nodes(two / 'feed.atom')])
        self.index_file.write_text('repositories: []\n')
        three, state = run('three')
        self.assertEqual('work-removed', category(nodes(three / 'feed.atom')[0]))
        self.assertIn(self.url, state['catalogs'])  # continuity across removal
        self.index_file.write_text('repositories: [https://example.org/work.git]\n')
        four, state = run('four')
        self.assertEqual('work-added', category(nodes(four / 'feed.atom')[0]))
        self.assertEqual(4, len(state['feed']['events']))

    def test_server_switch_preserves_event_log_and_rejects_failed_verification(self):
        from a_guavajam_static_website_generator.index import update_index
        def run():
            result = update_index(self.index_file, self.trust_file, self.output)
            site = self.output / 'current'
            return result, site, json.loads((site.resolve().parent / 'STATE.json').read_text())
        result, site, first = run()
        self.assertEqual('updated', result['status'])
        self.assertEqual('work-added', category(nodes(site / 'feed.atom')[0]))
        identity = first['feed']['id']
        self.assertEqual('unchanged', run()[0]['status'])
        self.catalog = work_catalog('a' * 40, 'b' * 40)
        result, site, second = run()
        self.assertEqual(identity, second['feed']['id'])
        self.assertEqual(['new-edition', 'work-added'], [category(e) for e in nodes(site / 'feed.atom')])
        before = os.readlink(site)
        with patch('a_guavajam_static_website_generator.index.verify_public_delivery',
                   side_effect=ValueError('new source not public')):
            self.catalog = work_catalog('a' * 40, 'b' * 40, 'c' * 40)
            with self.assertRaisesRegex(ValueError, 'new source not public'):
                run()
        self.assertEqual(before, os.readlink(site))
        self.assertEqual(2, len(nodes(site / 'feed.atom')))
