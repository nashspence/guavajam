"""Deployment plans are read-only; ownership checks precede private binding changes."""
import tempfile,json,unittest,sys
from pathlib import Path
from unittest.mock import patch,Mock
from guavajam_core.deploy_asset_gateway import deploy
from guavajam_core.asset_policy import AssetError

class DeploymentTests(unittest.TestCase):
    def test_plan_needs_no_credentials_and_performs_no_api_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root / 'asset-policy.json').write_text('{"origin":"https://assets.example.org"}')
            with patch('guavajam_core.deploy_asset_gateway.cf_request',side_effect=AssertionError('plan used API')),patch.dict('os.environ',{},clear=True):deploy(root)
    def test_another_workers_custom_domain_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root / 'asset-policy.json').write_text('{"origin":"https://assets.example.org"}')
            with patch('guavajam_core.cloudflare.cloudflare_values',return_value=('a'*32,'example-token')),patch('guavajam_core.asset_storage.credentials',return_value={}),patch('guavajam_core.asset_storage.S3'),patch('guavajam_core.deploy_asset_gateway.cf_request',return_value=[{'hostname':'assets.example.org','service':'unrelated-worker'}]),patch('guavajam_core.deploy_asset_gateway.upload_worker') as upload:
                with self.assertRaisesRegex(AssetError,'another Worker'):deploy(root,apply=True)
                upload.assert_not_called()

    def test_generic_read_only_configuration_deploys_without_storage_api_calls(self):
        from guavajam_test_support.storage_fixture import VALUES
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'asset-policy.json').write_text('{"origin":"https://assets.example.org"}')
            values = {**VALUES, 'S3_ENDPOINT': 'https://objects.example.org:9443',
                      'S3_REGION': 'auto', 'S3_ADDRESSING_STYLE': 'virtual',
                      'S3_SESSION_TOKEN': 'temporary-session'}
            with patch('guavajam_core.cloudflare.cloudflare_values', return_value=('a'*32, 'cf-token')), \
                 patch('guavajam_core.asset_storage.credentials', return_value=values), \
                 patch('guavajam_core.asset_storage.response', side_effect=AssertionError('deployment used storage API')), \
                 patch('guavajam_core.deploy_asset_gateway.cf_request', side_effect=[[], None, {}]), \
                 patch('guavajam_core.deploy_asset_gateway.upload_worker') as upload, \
                 patch.dict('os.environ', {'CLOUDFLARE_ZONE_ID': 'b'*32}, clear=True):
                deploy(root, apply=True)
            bindings = {item['name']: item for item in upload.call_args.args[-1]}
            self.assertEqual(values['S3_ENDPOINT'], bindings['S3_ENDPOINT']['text'])
            self.assertEqual('auto', bindings['S3_REGION']['text'])
            self.assertEqual('virtual', bindings['S3_ADDRESSING_STYLE']['text'])
            for name in ('S3_ACCESS_KEY_ID', 'S3_SECRET_ACCESS_KEY', 'S3_SESSION_TOKEN'):
                self.assertEqual('secret_text', bindings[name]['type'])
            self.assertFalse(any(name.startswith('B2_') for name in bindings))
            self.assertNotIn('S3_OBJECT_LOCK', bindings)
            self.assertNotIn('S3_VERSIONING', bindings)
