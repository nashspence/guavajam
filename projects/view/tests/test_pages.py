"""Static Pages artifacts retain independently verified history outside public HTML."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import yaml

from guavajam.index import build_pages, read_trust
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
        self.previous.write_text(json.dumps({'schema': 'guavajam.index-checkpoint.v1', 'catalogs': {}}))
        fake = patch('guavajam.index.prepare_source', side_effect=lambda _root, _url: self.publisher.repo)
        fake.start(); self.addCleanup(fake.stop)

    def build(self, suffix):
        output = self.root / ('site-' + suffix)
        next_state = self.root / ('state-' + suffix + '.json')
        build_pages(self.index, self.trust, output, self.previous, next_state)
        return output, next_state

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
