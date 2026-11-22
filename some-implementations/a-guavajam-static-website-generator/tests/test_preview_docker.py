"""Publisher-facing one-command Docker whole-site preview is a read-only generator launcher."""
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch, Mock

from a_guavajam_static_website_generator.preview_docker import run, safe_file, image, source_ref
from guavajam.asset_policy import AssetError


class DockerPreviewTests(unittest.TestCase):
    def test_launcher_mounts_only_source_and_separate_private_read_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            repo=root/'publication';repo.mkdir()
            trust=root/'allowed-signers';trust.write_text('external authority\n')
            reader=root/'readonly.env';reader.write_text('S3_ACCESS_KEY_ID=dummy\n');reader.chmod(0o600)
            snapshot=[]
            def invoked(args, **kwargs):
                self.assertIn('docker', args[0]);self.assertEqual('run',args[1]);snapshot.extend(args)
                return Mock(returncode=0)
            with patch.dict(os.environ,{'GUAVAJAM_SSH_ALLOWED_SIGNERS':str(trust),
                                       'GUAVAJAM_TRUSTED_GPG_FINGERPRINTS':'','S3_ENV':str(reader)}),\
                 patch('a_guavajam_static_website_generator.preview_docker.image',return_value=('preview@sha256:'+'a'*64,None)),\
                 patch('a_guavajam_static_website_generator.preview_docker.source_ref',return_value='refs/heads/work/notes'),\
                 patch('a_guavajam_static_website_generator.preview_docker.subprocess.run', side_effect=invoked):
                self.assertEqual(0,run(repo,port=8082,site_origin='https://publication.example'))
            args=' '.join(snapshot)
            self.assertIn('127.0.0.1:8082:8080',args)
            self.assertIn('refs/heads/work/notes',args)
            self.assertNotIn('GUAVAJAM_PREVIEW_WORK=',args)
            self.assertNotIn('GUAVAJAM_PREVIEW_PUBLISHED=',args)
            self.assertIn('GUAVAJAM_PREVIEW_ORIGIN=http://127.0.0.1:8082',args)
            self.assertIn('GUAVAJAM_SITE_ORIGIN=https://publication.example',args)
            self.assertIn('src='+str(repo)+',dst=/source,readonly',args)
            self.assertIn('src='+str(reader)+',dst=/run/secrets/readonly.env,readonly',args)
            self.assertNotIn('/trust/allowed_signers',args)
            self.assertIn('--read-only',snapshot)
            self.assertIn('--cap-drop',snapshot)
            self.assertNotIn('docker.sock',args)
            self.assertNotIn('S3_RELEASE_ENV',args)

    def test_first_preview_does_not_request_or_mount_signer_or_storage_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); repo = root / 'source'; repo.mkdir()
            with patch.dict(os.environ, {'PATH': os.environ['PATH']}, clear=True), \
                 patch('a_guavajam_static_website_generator.preview_docker.image', return_value=('preview@sha256:'+'a'*64, None)), \
                 patch('a_guavajam_static_website_generator.preview_docker.source_ref', return_value='refs/heads/draft'), \
                 patch('a_guavajam_static_website_generator.preview_docker.safe_file', side_effect=AssertionError('unneeded private input')), \
                 patch('a_guavajam_static_website_generator.preview_docker.subprocess.run', return_value=Mock(returncode=0)) as invoked, \
                 patch('builtins.print'):
                self.assertEqual(0, run(repo))
            argv = invoked.call_args.args[0]
            mounts = [argv[i+1] for i, arg in enumerate(argv) if arg == '--mount']
            self.assertEqual({'/source', '/state', '/run/secrets/session.key'},
                             {value.split('dst=')[1].split(',')[0] for value in mounts})

    def test_ref_requires_a_branch_not_a_tag_or_detached_head(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ['HEAD', 'work/notes', '../danger']:
                with patch('a_guavajam_static_website_generator.preview_docker.command',return_value=Mock(stdout=name)):
                    if name=='work/notes':self.assertEqual('refs/heads/work/notes',source_ref(directory))
                    elif name=='../danger':
                        with self.assertRaises(AssetError):source_ref(directory)
                    # HEAD is a valid branch name per git, but callers normally
                    # cannot obtain it as a symbolic ref unless named that way.

    def test_rejects_credentials_inside_source_and_non_owner_only_private_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);repo=root/'source';repo.mkdir()
            bad=repo/'readonly.env';bad.write_text('private')
            with self.assertRaises(AssetError):safe_file(repo,bad,owner_only=True)
            outside=root/'readonly.env';outside.write_text('private');outside.chmod(0o644)
            with self.assertRaises(AssetError):safe_file(repo,outside,owner_only=True)
            outside.chmod(0o600)
            self.assertEqual(outside,safe_file(repo,outside,owner_only=True))

    def test_image_cannot_be_unpinned_or_floating(self):
        for name in ['guavajam:latest','guavajam-preview:local','https://example.com/script']:
            with patch.dict(os.environ,{'GUAVAJAM_PREVIEW_IMAGE':name}):
                with self.assertRaises(AssetError):image()
        with patch.dict(os.environ,{'GUAVAJAM_PREVIEW_IMAGE':'registry.example/preview@sha256:'+'b'*64}):
            self.assertEqual('registry.example/preview@sha256:'+'b'*64,image()[0])


if __name__=='__main__':unittest.main()
