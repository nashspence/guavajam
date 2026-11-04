"""Deployment plans and Custom Domain API requests; no external writes in tests."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from asset_policy import AssetError
from deploy_asset_gateway import deploy

class DeploymentTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        (self.root/'asset-policy.json').write_text(json.dumps({'origin':'https://assets.publisher.test'}))
        (self.root/'storage').mkdir()
        (self.root/'storage/worker.js').write_text('export default {};\n')
        self.values={'CLOUDFLARE_API_TOKEN':'test-token','CLOUDFLARE_ACCOUNT_ID':'a'*32,
                     'CLOUDFLARE_ZONE_ID':'b'*32,'B2_PUBLIC_BASE':'https://f000.backblazeb2.com/file/example.bucket'}

    def test_plan_accepts_dotted_bucket_and_performs_no_api_calls(self):
        with patch.dict(os.environ,self.values), patch('deploy_asset_gateway.cf_request') as request, contextlib.redirect_stdout(io.StringIO()):
            deploy(self.root)
            request.assert_not_called()

    def test_fixed_b2_base_cannot_include_query_credentials_port_or_other_host(self):
        for base in ['http://f000.backblazeb2.com/file/bucket','https://evil.test/file/bucket',
                     'https://user@f000.backblazeb2.com/file/bucket','https://f000.backblazeb2.com:443/file/bucket',
                     self.values['B2_PUBLIC_BASE']+'?x=1','https://f000.backblazeb2.com/file/../bucket']:
            with self.subTest(base=base), patch.dict(os.environ,{**self.values,'B2_PUBLIC_BASE':base}), self.assertRaises(AssetError):
                deploy(self.root)

    def test_apply_attaches_custom_domain_to_worker(self):
        with patch.dict(os.environ,self.values), patch('deploy_asset_gateway.cf_request',side_effect=[[],None,{},{}]) as request, contextlib.redirect_stdout(io.StringIO()):
            deploy(self.root,apply=True)
        call=request.call_args_list[-1]
        self.assertEqual('domains',call.args[1])
        data=json.loads(call.kwargs['body'])
        self.assertEqual('assets.publisher.test',data['hostname'])
        self.assertEqual('press-assets',data['service'])
        self.assertEqual('PUT',call.kwargs['method'])
