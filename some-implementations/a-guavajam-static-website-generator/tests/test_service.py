"""Service wiring retains collection authority and reports bounded health."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from a_guavajam_static_website_generator.service import healthy, poll


class ServiceTests(unittest.TestCase):
    def test_service_passes_only_explicit_index_and_external_trust(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {'GUAVAJAM_INDEX_URL': 'https://publisher.example/index.yaml', 'GUAVAJAM_TRUST_FILE': '/trust/publishers.yaml'}
            with patch('a_guavajam_static_website_generator.index.update_index', return_value={'status': 'updated'}) as called:
                self.assertEqual('updated', poll(root, env)['status'])
                called.assert_called_once_with(env['GUAVAJAM_INDEX_URL'], env['GUAVAJAM_TRUST_FILE'], root / 'site')
            with self.assertRaises(ValueError): poll(root, {})

    def test_health_requires_recent_success_and_current_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'site/current').mkdir(parents=True)
            (root / 'site/current/index.html').write_text('Publication index')
            for passed, checked, expected in [(True, time.time(), True), (False, time.time(), False), (True, time.time() - 1000, False)]:
                (root / 'health.json').write_text(json.dumps({'schema': 'guavajam.service.v1', 'passed': passed, 'checked_at': checked}))
                self.assertEqual(expected, healthy(root, 300))
