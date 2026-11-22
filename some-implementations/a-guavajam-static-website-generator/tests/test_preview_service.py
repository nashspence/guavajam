"""Private session authentication and last-good previews of ordinary draft branches."""
from pathlib import Path
import socket
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from a_guavajam_static_website_generator.preview_service import Preview, session_key, settings
from guavajam_test_support.discovery_fixture import Draft


class PreviewServiceTests(unittest.TestCase):
    def test_drafts_refresh_without_signer_configuration_and_failure_keeps_last_good(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); draft = Draft(root)
            state = root / 'state'; state.mkdir()
            token = root / 'session.key'; token.write_text('e' * 64); token.chmod(0o600)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            env = {'GUAVAJAM_PREVIEW_ORIGIN': 'https://preview.publisher.test', 'GUAVAJAM_PREVIEW_PORT': str(port),
                   'GUAVAJAM_PREVIEW_REPO': str(draft.repo), 'GUAVAJAM_PREVIEW_REF': 'refs/heads/draft',
                   'GUAVAJAM_PREVIEW_TOKEN_FILE': str(token), 'GUAVAJAM_SITE_ORIGIN': 'https://publication.example'}
            preview = Preview(state, env)
            def request(authenticated=True):
                headers = {'Host': 'preview.publisher.test'}
                if authenticated: headers['Cookie'] = 'guavajam_preview=' + 'e' * 64
                return urllib.request.Request('http://127.0.0.1:' + str(port) + '/', headers=headers)
            try:
                with patch('a_guavajam_static_website_generator.preview_service.credentials', side_effect=AssertionError('no asset credentials')):
                    self.assertEqual('updated', preview.refresh())
                self.assertEqual('unchanged', preview.refresh())
                first = preview.revision; old_output = preview.output
                with urllib.request.urlopen(request()) as response:
                    self.assertIn(b'Unsigned proposed edition, not issued.', response.read())
                    self.assertEqual('private, no-store', response.getheader('Cache-Control'))
                with self.assertRaises(urllib.error.HTTPError): urllib.request.urlopen(request(False))
                draft.source('Field Notes', 'Revised private observation. [Local](https://publication.example/note).')
                draft.git('add', 'content'); draft.git('commit', '-qm', 'Revise private draft')
                self.assertEqual('updated', preview.refresh()); self.assertNotEqual(first, preview.revision)
                from test_site_origin import link_icons
                self.assertFalse(link_icons((preview.output / 'index.html').read_text())['https://publication.example/note'])
                self.assertFalse(old_output.exists())
                (draft.repo / 'content/manuscript.md').write_text('Invalid manuscript')
                draft.git('add', 'content'); draft.git('commit', '-qm', 'Invalid candidate')
                previous = preview.revision
                with self.assertRaises(ValueError): preview.refresh()
                self.assertEqual(previous, preview.revision)
                with urllib.request.urlopen(request()) as response:
                    self.assertIn(b'Revised private observation', response.read())
            finally:
                preview.close()

    def test_hosted_mode_requires_fixed_https_and_private_key_file(self):
        self.assertEqual('http://127.0.0.1:8082', settings({'GUAVAJAM_PREVIEW_ORIGIN': 'http://127.0.0.1:8082'})[0])
        for origin in ['http://preview.example', 'http://localhost:8082', 'https://preview.example/', 'https://user@preview.example', 'https://preview.example?x=1']:
            with self.subTest(origin=origin), self.assertRaises(ValueError): settings({'GUAVAJAM_PREVIEW_ORIGIN': origin})
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / 'key'; key.write_text('a' * 64); key.chmod(0o644)
            with self.assertRaises(ValueError): session_key(key)
            key.chmod(0o600); self.assertEqual('a' * 64, session_key(key))
            link = Path(directory) / 'link'; link.symlink_to(key)
            with self.assertRaises(OSError): session_key(link)
