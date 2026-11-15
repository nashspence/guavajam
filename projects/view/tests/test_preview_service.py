"""Actual local-branch preview refresh, private session and last-good behavior."""
import os
from pathlib import Path
import socket
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from guavajam.preview_service import Preview, session_key, settings
from guavajam_test_support.discovery_fixture import Publisher


class PreviewServiceTests(unittest.TestCase):
    def test_local_commits_refresh_without_push_signature_or_asset_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); publisher=Publisher(root)
            publisher.git('switch','-c','draft')
            token_file=root/'session.key'; token_file.write_text('e'*64); token_file.chmod(0o600)
            state=root/'state'; state.mkdir()
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
            env={'GUAVAJAM_PREVIEW_ORIGIN':'https://preview.publisher.test','GUAVAJAM_PREVIEW_PORT':str(port),
                 'GUAVAJAM_PREVIEW_REPO':str(publisher.repo),'GUAVAJAM_PREVIEW_REF':'refs/heads/draft',
                 'GUAVAJAM_PREVIEW_WORK':'field-notes','GUAVAJAM_PREVIEW_TOKEN_FILE':str(token_file)}
            preview=Preview(state,env)
            try:
                with patch('guavajam.preview_service.credentials',side_effect=AssertionError('no asset credentials needed')):
                    self.assertEqual(preview.refresh(),'updated')
                first=preview.revision; output=preview.output
                self.assertEqual(preview.refresh(),'unchanged')
                target='http://127.0.0.1:'+str(port)+'/works/field-notes/preview/index.html'
                req=urllib.request.Request(target,headers={'Host':'preview.publisher.test','Cookie':'guavajam_preview='+'e'*64})
                with urllib.request.urlopen(req) as response:
                    self.assertIn(b'PRIVATE DRAFT PREVIEW',response.read())
                with self.assertRaises(urllib.error.HTTPError):
                    urllib.request.urlopen(urllib.request.Request(target,headers={'Host':'preview.publisher.test'}))
                publisher.source('field-notes','Field Notes','A privately revised observation.')
                publisher.git('add','-A');publisher.git('commit','-qm','Private draft change')
                self.assertEqual(preview.refresh(),'updated');self.assertNotEqual(first,preview.revision)
                self.assertFalse(output.exists())
                # An invalid candidate cannot displace a successfully qualified preview.
                (publisher.repo/'works/field-notes/manuscript.md').write_text('invalid manuscript')
                publisher.git('add','-A');publisher.git('commit','-qm','Invalid private candidate')
                previous=preview.revision
                with self.assertRaises(ValueError):preview.refresh()
                self.assertEqual(previous,preview.revision)
                with urllib.request.urlopen(req) as response:self.assertIn(b'privately revised',response.read())
            finally:preview.close()

    def test_hosted_mode_requires_fixed_https_and_private_key_file(self):
        for origin in ['http://preview.example','https://preview.example/','https://user@preview.example','https://preview.example?x=1']:
            with self.subTest(origin=origin),self.assertRaises(ValueError):settings({'GUAVAJAM_PREVIEW_ORIGIN':origin})
        with tempfile.TemporaryDirectory() as directory:
            key=Path(directory)/'key';key.write_text('a'*64);key.chmod(0o644)
            with self.assertRaises(ValueError):session_key(key)
            key.chmod(0o600);self.assertEqual(session_key(key),'a'*64)
            link=Path(directory)/'link';link.symlink_to(key)
            with self.assertRaises(OSError):session_key(link)


if __name__=='__main__':unittest.main()
