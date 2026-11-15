import json
from pathlib import Path
import tempfile
import unittest
from guavajam_core.local_assets import initialize


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
