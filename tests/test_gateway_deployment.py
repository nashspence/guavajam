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
from deploy_record_gateway import deploy as deploy_records, target_base
from identifier_schema import TAG

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


class RecordDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root / 'edition-origin.txt').write_text('https://press.publisher.test\n')
        (self.root / 'records').mkdir()
        (self.root / 'records/worker.js').write_text('export default {};\n')
        self.values = {'CLOUDFLARE_API_TOKEN': 'test-token', 'CLOUDFLARE_ACCOUNT_ID': 'a' * 32,
                       'CLOUDFLARE_ZONE_ID': 'b' * 32, 'RECORD_WORKER': '', 'RECORD_TARGET': ''}

    def test_plan_is_read_only_and_uses_committed_origin(self):
        output = io.StringIO()
        with patch.dict(os.environ, self.values), patch('deploy_record_gateway.cf_request') as request, \
             patch('deploy_record_gateway.upload_worker') as upload, contextlib.redirect_stdout(output):
            deploy_records(self.root)
        request.assert_not_called()
        upload.assert_not_called()
        self.assertIn('https://press.publisher.test', output.getvalue())

    def test_apply_preserves_host_and_uses_the_single_tag_grammar(self):
        for target in ('https://github.com/owner/press/releases/tag', 'https://restored.publisher.test'):
            with self.subTest(target=target), patch.dict(os.environ, {**self.values, 'RECORD_TARGET': target}), \
                 patch('deploy_record_gateway.cf_request', side_effect=[[], None, {}]) as request, \
                 patch('deploy_record_gateway.upload_worker') as upload, contextlib.redirect_stdout(io.StringIO()):
                deploy_records(self.root, apply=True)
            bindings = {b['name']: b['text'] for b in upload.call_args.args[-1]}
            self.assertEqual(bindings, {'RECORD_HOST': 'press.publisher.test', 'RECORD_TARGET_BASE': target,
                                        'RECORD_TAG_PATTERN': TAG.pattern})
            self.assertEqual(request.call_args_list[0].args[1], 'domains?hostname=press.publisher.test')
            domain = json.loads(request.call_args.kwargs['body'])
            self.assertEqual(domain['hostname'], 'press.publisher.test')
            self.assertEqual(domain['service'], 'press-records')

    def test_another_workers_hostname_is_never_overwritten(self):
        for result in ([{'hostname': 'press.publisher.test', 'service': 'other-worker'}],
                       []):
            values = [result] if result else [[], {'bindings': [{'name': 'RECORD_HOST', 'text': 'other.publisher.test'}]}]
            with self.subTest(result=result), patch.dict(os.environ, self.values), \
                 patch('deploy_record_gateway.cf_request', side_effect=values), \
                 patch('deploy_record_gateway.upload_worker') as upload, self.assertRaises(AssetError):
                deploy_records(self.root, apply=True)
            upload.assert_not_called()

    def test_operator_target_cannot_contain_credentials_or_ambiguous_paths(self):
        for target in ('http://archive.test', 'https://user@archive.test', 'https://archive.test:443',
                       'https://archive.test/x?key=1', 'https://archive.test/x#key',
                       'https://archive.test/x/../y', 'https://archive.test/%2e', 'https://archive.test/'):
            with self.subTest(target=target), self.assertRaises(AssetError):
                target_base(target)
