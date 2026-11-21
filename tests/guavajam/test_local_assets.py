from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from guavajam.local_assets import initialize, main


class LocalAssetConfigurationTests(unittest.TestCase):
    def test_creation_separates_roles_and_preserves_existing_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = initialize(Path(temporary) / 'private')
            self.assertEqual(0o700, path.stat().st_mode & 0o777)
            for file in path.iterdir():self.assertEqual(0o600, file.stat().st_mode & 0o777)
            identities = json.loads((path / 's3-identities.json').read_text())['identities']
            self.assertEqual(['Read:publication-assets'], identities[1]['actions'])
            self.assertNotEqual(identities[0]['credentials'], identities[1]['credentials'])
            saved = {p.name: p.read_bytes() for p in path.iterdir()}
            with self.assertRaises(ValueError):initialize(path)
            self.assertEqual(saved, {p.name: p.read_bytes() for p in path.iterdir()})

    def test_staging_and_release_have_distinct_object_prefix_authority(self):
        from guavajam.local_assets import role_actions
        import fnmatch
        roles = role_actions('publication-assets')
        def can_write(role, key):
            return any(fnmatch.fnmatchcase('publication-assets/' + key, action[6:])
                       for action in roles[role] if action.startswith('Write:'))
        for digit in '0123456789abcdef':
            self.assertTrue(can_write('publisher', 'sha256/' + digit * 64 + '.mp4'))
        self.assertTrue(can_write('publisher', 'sha256/.pins/file.json'))
        self.assertTrue(can_write('publisher', 'sha256/.leases/file'))
        self.assertFalse(can_write('publisher', 'released/sha256/file.json'))
        self.assertTrue(can_write('release', 'released/sha256/file.json'))
        self.assertFalse(can_write('release', 'sha256/' + 'a'*64 + '.mp4'))
        self.assertFalse(can_write('release', 'sha256/.pins/file.json'))
        self.assertFalse(any(a.startswith('Write:') for a in roles['delivery']))

    def test_initializer_cli_never_prints_credentials_on_success_or_refusal(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'private'
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(output):
                self.assertEqual(0, main([str(path), '--bucket', 'custom-publication']))
                self.assertEqual(1, main([str(path), '--bucket', 'custom-publication']))
            identities = json.loads((path / 's3-identities.json').read_text())['identities']
            secrets = [value for identity in identities for credential in identity['credentials']
                       for value in credential.values()]
            self.assertTrue(all(value not in output.getvalue() for value in secrets))
            self.assertEqual(['Read:custom-publication'], identities[1]['actions'])
