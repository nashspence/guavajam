"""Deployment plans are read-only; ownership checks precede private binding changes."""
import tempfile,json,unittest,sys
from pathlib import Path
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from deploy_asset_gateway import deploy
from asset_policy import AssetError
class DeploymentTests(unittest.TestCase):
    def test_plan_needs_no_credentials_and_performs_no_api_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'asset-policy.json').write_text('{"origin":"https://assets.example.org"}')
            with patch('deploy_asset_gateway.cf_request',side_effect=AssertionError('plan used API')),patch.dict('os.environ',{},clear=True):deploy(root)
    def test_another_workers_custom_domain_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'asset-policy.json').write_text('{"origin":"https://assets.example.org"}')
            with patch('cloudflare.cloudflare_values',return_value=('a'*32,'example-token')),patch('asset_storage.credentials',return_value={}),patch('asset_storage.B2'),patch('deploy_asset_gateway.cf_request',return_value=[{'hostname':'assets.example.org','service':'unrelated-worker'}]),patch('deploy_asset_gateway.upload_worker') as upload:
                with self.assertRaisesRegex(AssetError,'another Worker'):deploy(root,apply=True)
                upload.assert_not_called()
