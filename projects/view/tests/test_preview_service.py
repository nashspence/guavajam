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
from guavajam_test_support.discovery_fixture import Draft, Publisher


class PreviewServiceTests(unittest.TestCase):
    def test_local_commits_refresh_without_push_signature_or_asset_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); publisher=Publisher(root)
            publisher.publish('2026-11-01')
            publisher.git('switch','-c','draft')
            publisher.source('field-notes','Field Notes','An initial private draft.')
            publisher.git('add','-A');publisher.git('commit','-qm','Initial unsigned draft')
            public=root/'publisher.asc'
            from guavajam_test_support.discovery_fixture import command
            public.write_text(command(publisher.repo,'gpg','--batch','--armor','--export',publisher.fingerprint,env=publisher.env))
            token_file=root/'session.key'; token_file.write_text('e'*64); token_file.chmod(0o600)
            state=root/'state'; state.mkdir()
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
            env={'GUAVAJAM_PREVIEW_ORIGIN':'https://preview.publisher.test','GUAVAJAM_PREVIEW_PORT':str(port),
                 'GUAVAJAM_PREVIEW_REPO':str(publisher.repo),'GUAVAJAM_PREVIEW_REF':'refs/heads/draft',
                 'GUAVAJAM_PREVIEW_WORK':'field-notes','GUAVAJAM_PREVIEW_TOKEN_FILE':str(token_file),
                 'GUAVAJAM_PREVIEW_PUBLISHED':'2026-11-15', 'GUAVAJAM_SSH_ALLOWED_SIGNERS':'',
                 'GUAVAJAM_TRUSTED_GPG_FINGERPRINTS':publisher.fingerprint,
                 'GUAVAJAM_GPG_PUBLIC_KEYS':str(public)}
            preview=Preview(state,env)
            try:
                with patch('guavajam.preview_service.credentials',side_effect=AssertionError('no asset credentials needed')):
                    self.assertEqual(preview.refresh(),'updated')
                first=preview.revision; output=preview.output
                self.assertEqual(preview.refresh(),'unchanged')
                self.assertTrue((state/'baseline.json').is_file())
                target='http://127.0.0.1:'+str(port)+'/works/field-notes/index.html'
                req=urllib.request.Request(target,headers={'Host':'preview.publisher.test','Cookie':'guavajam_preview='+'e'*64})
                with urllib.request.urlopen(req) as response:
                    self.assertIn(b'PRIVATE PROPOSED EDITION',response.read())
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
                reading='http://127.0.0.1:'+str(port)+'/works/field-notes/editions/'+previous+'/read/index.html'
                with urllib.request.urlopen(urllib.request.Request(reading,headers={'Host':'preview.publisher.test','Cookie':'guavajam_preview='+'e'*64})) as response:
                    self.assertIn(b'privately revised',response.read())
                # A persisted baseline rejects lost issued editions after restart.
                checkpoint = preview.baseline['checkpoint']
                preview.close()
                publisher.git('reset', '--hard', checkpoint)
                publisher.git('update-ref', 'refs/heads/main', checkpoint)
                publisher.source('field-notes', 'Field Notes', 'A rewritten unissued draft.')
                publisher.git('add', '-A'); publisher.git('commit', '-qm', 'Discarded an issued edition')
                restarted = Preview(state, env)
                with self.assertRaises(ValueError): restarted.refresh()
                self.assertIsNone(restarted.output)
                # Even removing every declaration-bearing ref cannot downgrade it.
                publisher.git('switch', '--orphan', 'replacement')
                publisher.source('field-notes', 'Field Notes', 'A new unsigned root.')
                publisher.write_settings(); publisher.git('add', '-A'); publisher.git('commit', '-qm', 'Unrelated root')
                publisher.git('branch', '-M', 'draft'); publisher.git('branch', '-D', 'main')
                with self.assertRaises(ValueError): restarted.refresh()
                self.assertIsNone(restarted.output)
            finally:preview.close()

    def test_fresh_repository_refreshes_without_signer_or_asset_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); draft = Draft(root)
            state = root/'state'; state.mkdir()
            token_file = root/'session.key'; token_file.write_text('e'*64); token_file.chmod(0o600)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            env = {'GUAVAJAM_PREVIEW_ORIGIN':'http://127.0.0.1:'+str(port),
                   'GUAVAJAM_PREVIEW_PORT':str(port), 'GUAVAJAM_PREVIEW_REPO':str(draft.repo),
                   'GUAVAJAM_PREVIEW_REF':'refs/heads/draft', 'GUAVAJAM_PREVIEW_WORK':'field-notes',
                   'GUAVAJAM_PREVIEW_TOKEN_FILE':str(token_file), 'GUAVAJAM_PREVIEW_PUBLISHED':'2026-11-16'}
            with patch('guavajam.preview_service.trust', side_effect=AssertionError('no signer input')), \
                 patch('guavajam.preview_service.credentials', side_effect=AssertionError('no asset credentials')):
                preview = Preview(state, env)
                try:
                    self.assertEqual('updated', preview.refresh())
                    first = preview.revision
                    self.assertEqual('unchanged', preview.refresh())
                    draft.source('field-notes','Field Notes','A revised initial private draft.')
                    draft.git('add','-A'); draft.git('commit','-qm','Revise first draft')
                    self.assertEqual('updated', preview.refresh())
                    self.assertNotEqual(first, preview.revision)
                    request = urllib.request.Request('http://127.0.0.1:'+str(port)+'/',
                              headers={'Cookie':'guavajam_preview='+'e'*64})
                    with urllib.request.urlopen(request) as response:
                        self.assertIn(b'publisher settings are provisional', response.read())
                        self.assertEqual('private, no-store', response.getheader('Cache-Control'))
                    self.assertFalse((state/'baseline.json').exists())
                    # A declaration appearing on another branch invalidates the fast path.
                    draft.git('switch', '-c', 'declared')
                    draft.git('commit','--allow-empty','-qm','Untrusted settings\n\nGuavaJam-Repository: 1')
                    draft.git('switch', 'draft')
                    with self.assertRaises(ValueError): preview.refresh()
                finally:
                    preview.close()

    def test_hosted_mode_requires_fixed_https_and_private_key_file(self):
        self.assertEqual('http://127.0.0.1:8082', settings({'GUAVAJAM_PREVIEW_ORIGIN':'http://127.0.0.1:8082'})[0])
        for origin in ['http://preview.example','http://localhost:8082','https://preview.example/','https://user@preview.example','https://preview.example?x=1']:
            with self.subTest(origin=origin),self.assertRaises(ValueError):settings({'GUAVAJAM_PREVIEW_ORIGIN':origin})
        with tempfile.TemporaryDirectory() as directory:
            key=Path(directory)/'key';key.write_text('a'*64);key.chmod(0o644)
            with self.assertRaises(ValueError):session_key(key)
            key.chmod(0o600);self.assertEqual(session_key(key),'a'*64)
            link=Path(directory)/'link';link.symlink_to(key)
            with self.assertRaises(OSError):session_key(link)


if __name__=='__main__':unittest.main()
