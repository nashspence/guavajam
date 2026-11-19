"""Real signed publisher startup and persistent service behavior."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from guavajam_test_support.discovery_fixture import Publisher
from guavajam.service import healthy, poll, prepare_source, source_url


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.publisher = Publisher(self.root)
        self.revision = self.publisher.publish()
        self.state = self.root / 'state'; self.state.mkdir()
        self.keys = self.root / 'publisher.asc'
        self.keys.write_bytes(subprocess.check_output(['gpg', '--batch', '--armor', '--export', self.publisher.fingerprint], env=self.publisher.env))
        self.env = {'GUAVAJAM_SOURCE_URL': self.publisher.repo.as_uri(),
                    'GUAVAJAM_GPG_PUBLIC_KEYS': str(self.keys),
                    'GUAVAJAM_TRUSTED_GPG_FINGERPRINTS': self.publisher.fingerprint}
        env = patch.dict(os.environ, {'GNUPGHOME': str(self.root / 'verification')})
        env.start(); self.addCleanup(env.stop)

    def test_fresh_clone_noop_new_signed_edition_and_restart_reuse_persistent_state(self):
        self.assertEqual('updated', poll(self.state, self.env)['status'])
        selected = os.readlink(self.state / 'site/current')
        self.assertEqual('unchanged', poll(self.state, self.env)['status'])
        self.assertEqual(selected, os.readlink(self.state / 'site/current'))
        self.publisher.source('field-notes', 'Field Notes', 'Revised observation.')
        revision = self.publisher.publish('2026-11-15')
        self.assertEqual('updated', poll(self.state, self.env)['status'])
        self.assertIn(revision, (self.state / 'site/current/CATALOG.json').read_text())
        self.assertEqual('unchanged', poll(self.state, self.env)['status'])
        self.assertFalse((self.state / 'source/guavajam.json').exists(), 'source clone never checks out publisher code')

    def test_failed_signature_and_changed_source_keep_last_good_generation(self):
        poll(self.state, self.env)
        selected = os.readlink(self.state / 'site/current')
        with self.assertRaises(ValueError):poll(self.state, {**self.env, 'GUAVAJAM_TRUSTED_GPG_FINGERPRINTS': 'F' * 40})
        self.assertEqual(selected, os.readlink(self.state / 'site/current'))
        with self.assertRaises(ValueError):prepare_source(self.state, 'https://other.example/repository.git', 'refs/heads/main')
        self.assertEqual(selected, os.readlink(self.state / 'site/current'))

    def test_health_requires_recent_success_and_current_generation(self):
        poll(self.state, self.env)
        health = self.state / 'health.json'
        health.write_text(json.dumps({'schema': 'guavajam.service.v1', 'passed': True, 'checked_at': time.time()}))
        self.assertTrue(healthy(self.state, 300))
        health.write_text(json.dumps({'schema': 'guavajam.service.v1', 'passed': False, 'checked_at': time.time()}))
        self.assertFalse(healthy(self.state, 300))
        health.write_text(json.dumps({'schema': 'guavajam.service.v1', 'passed': True, 'checked_at': time.time() - 1000}))
        self.assertFalse(healthy(self.state, 300))

    def test_source_configuration_never_embeds_web_credentials_or_command_protocols(self):
        for value in ['https://user:secret@example.test/repo', 'https://user@example.test/repo',
                      'ext::sh -c command', '-invalid', 'http://example.test/repo', 'file://other/repo']:
            with self.subTest(value=value), self.assertRaises(ValueError):source_url(value)
        for value in ['https://example.test/repo', 'git@example.test:owner/repo.git', 'ssh://git@example.test/owner/repo.git']:
            self.assertEqual(value, source_url(value))
