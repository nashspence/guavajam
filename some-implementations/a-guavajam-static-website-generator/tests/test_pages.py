"""Static Pages artifacts retain independently verified history outside public HTML."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import yaml

from a_guavajam_static_website_generator.index import build_pages, read_trust
from guavajam_test_support.discovery_fixture import Publisher


class PagesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.publisher = Publisher(self.root / 'publisher')
        self.index = self.root / 'index.yaml'
        self.trust = self.root / 'trust.yaml'
        self.url = 'https://example.org/work.git'
        self.index.write_text(yaml.safe_dump({'repositories': [self.url]}))
        (self.root / 'trust').mkdir()
        shutil.copyfile(self.publisher.allowed_signers, self.root / 'trust/publisher.allowed-signers')
        self.trust.write_text(yaml.safe_dump({self.url: {'allowed_signers': 'trust/publisher.allowed-signers'}}))
        self.previous = self.root / 'previous.json'
        self.previous.write_text(json.dumps({'schema': 'guavajam.index-checkpoint.v1', 'catalogs': {}, 'feed': None}))
        fake = patch('a_guavajam_static_website_generator.index.prepare_source', side_effect=lambda _root, _url: self.publisher.repo)
        fake.start(); self.addCleanup(fake.stop)

    def build(self, suffix):
        output = self.root / ('site-' + suffix)
        next_state = self.root / ('state-' + suffix + '.json')
        build_pages(self.index, self.trust, output, self.previous, next_state)
        return output, next_state

    def test_pages_artifact_exposes_a_local_repository_download(self):
        from test_site_origin import link_icons
        output = self.root / 'origin-site'
        build_pages(self.index, self.trust, output, self.previous, self.root / 'origin-state.json',
                    site_origin='https://example.org')
        metadata = next((output / 'sources').glob('*/editions/*/metadata.html'))
        self.assertFalse(link_icons(metadata.read_text())['source.bundle'])
        self.assertTrue((metadata.parent / 'source.bundle').is_file())

    def test_static_pages_and_checkpoint_are_separate_and_reverifiable(self):
        policy = read_trust(self.trust)[self.url]
        self.assertEqual(str((self.root / 'trust/publisher.allowed-signers').resolve()), policy['allowed_signers'])
        output, state = self.build('one')
        self.assertTrue((output / 'index.html').is_file())
        self.assertTrue((output / 'INDEX.json').is_file())
        self.assertFalse((output / 'STATE.json').exists())
        self.assertEqual([self.url], list(json.loads(state.read_text())['catalogs']))
        self.publisher.source('A Revision', 'Changed published source.')
        self.publisher.publish('Explain revision')
        self.previous.write_bytes(state.read_bytes())
        output2, state2 = self.build('two')
        catalog = json.loads(state2.read_text())['catalogs'][self.url]
        self.assertEqual(2, len(catalog['editions']))
        self.assertNotEqual((output / 'INDEX.json').read_bytes(), (output2 / 'INDEX.json').read_bytes())

    def test_signed_feeds_resolve_inside_a_deployment_prefix_and_preserve_events(self):
        import xml.etree.ElementTree as ET
        from urllib.parse import urljoin, urlsplit
        ns = {'a': 'http://www.w3.org/2005/Atom'}
        output, state = self.build('feeds')
        origin = 'https://publication.example/library/'
        root = ET.parse(output / 'feed.atom').getroot()
        entries = root.findall('a:entry', ns)
        self.assertEqual(['work-added'], [entry.find('a:category', ns).get('term') for entry in entries])
        identity = root.findtext('a:id', namespaces=ns)
        for entry in entries:
            href = urljoin(origin + 'feed.atom', entry.find('a:link', ns).get('href'))
            self.assertTrue(href.startswith(origin))
            self.assertTrue((output / urlsplit(href).path.removeprefix('/library/')).is_file())
        work = next((output / 'sources').iterdir())
        work_feed = ET.parse(work / 'feed.atom').getroot()
        edition = work_feed.find('a:entry', ns)
        self.assertIsNotNone(edition)
        href = edition.find('a:link', ns).get('href')
        self.assertTrue((work / href).is_file())
        self.assertIn(self.publisher.revisions[0], href)
        event_id = entries[0].findtext('a:id', namespaces=ns)
        self.previous.write_bytes(state.read_bytes())
        self.publisher.source('Second edition', 'New signed observations.')
        revision = self.publisher.publish('Revise the observations')
        updated, _ = self.build('feed-revision')
        feed = ET.parse(updated / 'feed.atom').getroot()
        entries = feed.findall('a:entry', ns)
        self.assertEqual(identity, feed.findtext('a:id', namespaces=ns))
        self.assertEqual(['new-edition', 'work-added'], [entry.find('a:category', ns).get('term') for entry in entries])
        self.assertEqual(event_id, entries[1].findtext('a:id', namespaces=ns))
        self.assertIn(revision, entries[0].find('a:link', ns).get('href'))

    def test_removed_work_remains_in_checkpoint_and_rewrites_are_rejected(self):
        output, state = self.build('one')
        self.index.write_text('repositories: []\n')
        self.previous.write_bytes(state.read_bytes())
        _, state2 = self.build('empty')
        self.assertIn(self.url, json.loads(state2.read_text())['catalogs'])
        self.publisher.git('reset', '--hard', self.publisher.revisions[0] + '^')
        self.publisher.source('Tampered History', 'Rewritten source.')
        self.publisher.publish('Rewrite history')
        self.index.write_text(yaml.safe_dump({'repositories': [self.url]}))
        self.previous.write_bytes(state2.read_bytes())
        with self.assertRaises(ValueError):
            self.build('rewritten')
        self.assertFalse((self.root / 'state-rewritten.json').exists())
        self.assertFalse((self.root / 'site-rewritten').exists())

    def test_missing_or_tampered_state_does_not_reset_history(self):
        self.previous.unlink()
        with self.assertRaises((OSError, ValueError)):
            self.build('missing')
        self.previous.write_text('{}\n')
        with self.assertRaises(ValueError):
            self.build('tampered')
        self.trust.write_text(yaml.safe_dump({self.url: {'allowed_signers': '../publisher/allowed-signers'}}))
        with self.assertRaises(ValueError):
            read_trust(self.trust)


if __name__ == '__main__':
    unittest.main(verbosity=2)
