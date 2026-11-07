"""Opaque objects are verified files; only the approved image profile renders inline."""
from contextlib import contextmanager
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from asset_policy import (AssetError,AssetPolicy,MAX_BYTES,fetch_asset,verify_bytes,download_bytes)
from build_edition import build_edition
from export_archive import export_archive
from issuance import authorization
from manage_assets import upload,candidates,fingerprints
from prepare_asset import prepare_image
from public_record import write_records
from validate_sources import git_sources,validate_repository
from verify_edition import verify_edition
from verify_source_assets import verify_source_assets
from deploy_asset_gateway import deploy
from test_assets import EditionTests,SVG,URL,ORIGIN
from test_asset_storage import MemoryB2,version,LEASE_PREFIX,OLD,NOW

RAW=b'<!doctype html><script>alert("download only")</script>\x00\xff'
DIGEST=hashlib.sha256(RAW).hexdigest()
KEY='sha256/'+DIGEST+'.bin'
DOWNLOAD=ORIGIN+'/'+KEY


class DownloadPolicyTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root/'asset-policy.json').write_text(json.dumps({'origin':ORIGIN}))

    def test_unknown_content_uploads_as_opaque_original_bytes_and_gets_a_lease(self):
        path=self.root/'unfamiliar.future';path.write_bytes(RAW)
        self.assertEqual((RAW,KEY,'application/octet-stream'),prepare_image(path))
        store=MemoryB2()
        state,url=upload(self.root,path,store,fetcher=lambda *_:(RAW,'application/octet-stream'))
        self.assertEqual(('Uploaded',DOWNLOAD),(state,url))
        self.assertEqual(RAW,store.bytes[KEY])
        self.assertEqual(LEASE_PREFIX+KEY.removeprefix('sha256/'),store.uploads[0][0])
        self.assertEqual('application/octet-stream',store.uploads[1][2])
        self.assertEqual(('Already stored',DOWNLOAD),upload(self.root,path,store,fetcher=lambda *_:(RAW,'application/octet-stream')))
        with self.assertRaises(AssetError):upload(self.root,path,store,fetcher=lambda *_:(RAW+b'changed','application/octet-stream'))

    def test_opaque_empty_and_size_bounds_but_known_image_claims_cannot_fall_back(self):
        path=self.root/'empty';path.write_bytes(b'')
        self.assertEqual('bin',prepare_image(path)[1].rsplit('.',1)[1])
        path.write_bytes(b'x'*(MAX_BYTES+1))
        with self.assertRaises(AssetError):prepare_image(path)
        for name in ('masquerade.png','active.svg','ACTIVE.SVG'):
            path=self.root/name;path.write_bytes(RAW)
            with self.assertRaises(AssetError):prepare_image(path)
        path=self.root/'passive.SVG';path.write_bytes(SVG)
        self.assertTrue(prepare_image(path)[1].endswith('.svg'))
        self.assertEqual('application/octet-stream',verify_bytes(RAW,'bin','text/html'))

    def test_canonical_download_urls_safety_headers_hash_redirect_and_bounds_fail_closed(self):
        policy=AssetPolicy(ORIGIN)
        self.assertEqual((DIGEST,'bin'),policy.parse(DOWNLOAD))
        with self.assertRaises(AssetError):policy.parse_image(DOWNLOAD)
        for bad in (DOWNLOAD+'?',DOWNLOAD+'#x',DOWNLOAD.replace('/sha256/','/%73ha256/'),
                    DOWNLOAD.replace('.bin','.pdf'),DOWNLOAD.replace(DIGEST,DIGEST.upper()),
                    DOWNLOAD.replace('https:','http:'),DOWNLOAD.replace('.test/','.test:443/')):
            with self.assertRaises(AssetError):policy.parse(bad)
        headers={'Content-Type':'application/octet-stream','Content-Disposition':'attachment; filename="object.bin"','X-Content-Type-Options':'nosniff'}
        def response(raw=RAW,status=200,changes=None):
            @contextmanager
            def result(*args,**kwargs):
                stream=io.BytesIO(raw);stream.status=status;stream.getheader={**headers,**(changes or {})}.get
                yield stream
            return result
        with patch('asset_policy.public_response',response()):self.assertEqual((RAW,'application/octet-stream'),fetch_asset(policy,DOWNLOAD))
        for fetch in (response(RAW+b'x'),response(status=302),response(b'x'*(MAX_BYTES+1)),
                      response(changes={'Content-Type':'text/html'}),response(changes={'Content-Disposition':'inline'}),
                      response(changes={'X-Content-Type-Options':''})):
            with patch('asset_policy.public_response',fetch),self.assertRaises(AssetError):fetch_asset(policy,DOWNLOAD)

    def test_binary_retention_and_leases_keep_historical_hash_roots(self):
        items=[version(KEY),version(LEASE_PREFIX+KEY.removeprefix('sha256/'),file_id='lease')]
        self.assertEqual({KEY},set(candidates(items,set(),NOW)))
        historical=fingerprints(('Withdrawn edition retains '+DOWNLOAD).encode())
        self.assertEqual({},candidates(items,historical,NOW))
        items[-1]['uploadTimestamp']=int(NOW*1000)
        self.assertEqual({},candidates(items,set(),NOW))

    def test_explicit_standby_target_keeps_the_same_asset_host_and_validates_base(self):
        (self.root/'storage').mkdir();(self.root/'storage/worker.js').write_text('export default {}')
        env={'CLOUDFLARE_API_TOKEN':'fixture','CLOUDFLARE_ACCOUNT_ID':'a'*32,'CLOUDFLARE_ZONE_ID':'b'*32,
             'ASSET_TARGET':'https://backup.example.org/press'}
        with patch.dict(os.environ,env),patch('deploy_asset_gateway.cf_request',side_effect=[[],None,{}]), \
             patch('deploy_asset_gateway.upload_worker') as worker:
            deploy(self.root,apply=True)
        bindings={v['name']:v['text'] for v in worker.call_args.args[-1]}
        self.assertEqual(ORIGIN.removeprefix('https://'),bindings['ASSET_HOST'])
        self.assertEqual(env['ASSET_TARGET'],bindings['B2_PUBLIC_BASE'])
        for bad in ('http://backup.example.org','https://user@backup.example.org','https://backup.example.org/../private','https://127.0.0.1'):
            with patch.dict(os.environ,{**env,'ASSET_TARGET':bad}),self.assertRaises(AssetError):deploy(self.root)


class DownloadPublicationTests(EditionTests):
    def source(self):
        manuscript=self.repo/'works/sample/manuscript.md'
        directory=manuscript.parent/'supporting';directory.mkdir()
        note=(Path(__file__).resolve().parents[1]/'templates/work/supporting/note.md').read_text()
        (directory/'note.md').write_text(note+'\nA whole-object [download]('+DOWNLOAD+').\n')
        with manuscript.open('a') as stream:
            stream.write('\nRead a [download]('+DOWNLOAD+') and [claim](supporting/note.md#claim).\n')
        self.git('add','works');self.git('commit','-qm','Review image, opaque object and supporting Markdown')
        self.commit=self.git('rev-parse','HEAD')

    def fetch(self,policy,url):
        return (RAW,'application/octet-stream') if url==DOWNLOAD else (SVG,'image/svg+xml')

    def build_downloads(self):
        return build_edition(self.repo,self.commit,self.tag,self.output,fetcher=self.fetch)

    def test_mixed_inline_external_and_supporting_references_build_and_verify_offline(self):
        self.source()
        self.assertEqual([],validate_repository(self.repo,git_sources(self.repo)))
        self.assertEqual(2,verify_source_assets(self.repo,fetcher=self.fetch))
        manifest=self.build_downloads()
        external=[r for r in manifest['assets'] if r.get('role')=='download']
        self.assertEqual(1,len(external));self.assertEqual(DOWNLOAD,external[0]['destination'])
        page=(self.output/manifest['canonical_artifact']).read_text()
        self.assertEqual(2,page.count('href="'+DOWNLOAD+'"'))
        self.assertIn('data:image/svg+xml;base64,',page)
        self.assertNotIn('download only',page)
        with patch('socket.create_connection',side_effect=AssertionError('offline')):
            self.assertEqual(manifest,verify_edition(self.output))
        with self.assertRaises(AssetError):
            list(download_bytes(manifest,fetcher=lambda *_:(RAW+b'changed','application/octet-stream')))
        other=self.base/'repeat'
        build_edition(self.repo,self.commit,self.tag,other,fetcher=self.fetch)
        self.assertEqual({p.name:p.read_bytes() for p in self.output.iterdir()},{p.name:p.read_bytes() for p in other.iterdir()})

    def test_managed_link_syntax_and_inline_bin_reject_before_issuance(self):
        self.source();path=self.repo/'works/sample/manuscript.md';text=path.read_text()
        for target in (DOWNLOAD+'#fragment',DOWNLOAD+'?',DOWNLOAD.replace('.test/','.test:443/')):
            path.write_text(text.replace(DOWNLOAD,target))
            self.assertTrue(validate_repository(self.repo,git_sources(self.repo)))
        path.write_text(text.replace('[download](','![download]('))
        self.assertTrue(validate_repository(self.repo,git_sources(self.repo)))
        path.write_text(text)
        with self.assertRaises(AssetError):self.build_downloads_wrong_digest()

    def build_downloads_wrong_digest(self):
        return build_edition(self.repo,self.commit,self.tag,self.output,fetcher=lambda *_:(RAW,'application/octet-stream'))

    def test_tampered_external_link_is_not_accepted_as_the_reviewed_download(self):
        self.source();manifest=self.build_downloads();name=manifest['canonical_artifact']
        raw=(self.output/name).read_bytes().replace(DOWNLOAD.encode(),DOWNLOAD.replace(DIGEST,'0'*64).encode())
        manifest.update(html_sha256=hashlib.sha256(raw).hexdigest(),html_byte_count=len(raw))
        (self.output/name).write_bytes(raw);(self.output/'provenance.json').write_text(json.dumps(manifest))
        (self.output/'SHA256SUMS').write_text(''.join(hashlib.sha256((self.output/f).read_bytes()).hexdigest()+'  '+f+'\n' for f in (name,'provenance.json')))
        with self.assertRaises(AssetError):verify_edition(self.output)

    def test_archive_carries_download_bytes_and_can_recover_from_saved_objects_without_origin(self):
        self.source();manifest=self.build_downloads()
        package=self.base/'packages'/self.tag;package.parent.mkdir(parents=True)
        import shutil
        shutil.copytree(self.output,package)
        receipt={**authorization(package,manifest),'issuing':True,'published_at':'2026-11-06T12:00:00Z'}
        path=self.repo/'registrations/editions/sample/v1.0.0.json';path.parent.mkdir(parents=True)
        path.write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n');self.git('add','registrations')
        write_records(self.repo,self.tag,package=package)
        notice=self.repo/'notices/sample/v1.0.0/0001.md';notice.parent.mkdir(parents=True)
        notice.write_text('2026-11-06 Withdrawal: Synthetic editorial review.\n')
        self.git('add','notices')
        write_records(self.repo,self.tag)
        self.git('add','records');self.git('commit','-qm','Keep reviewed receipt and pages')
        sha=self.git('rev-parse','HEAD');output=self.base/'export'
        self.assertEqual(1,export_archive(self.repo,sha,self.base/'packages',output,fetcher=self.fetch))
        self.assertEqual(RAW,(output/'public'/KEY).read_bytes())
        self.assertEqual((package/manifest['canonical_artifact']).read_bytes(),(output/'public'/self.tag/'read.html').read_bytes())
        with patch('socket.create_connection',side_effect=AssertionError('offline')):
            self.assertEqual(1,export_archive(self.repo,sha,self.base/'packages',self.base/'offline-export',asset_root=output/'public'))
        from check_backup_archive import check_backup_archive
        base='https://backup.example.org/press'
        @contextmanager
        def response(url,accept,**kwargs):
            self.assertTrue(url.startswith(base+'/'))
            path=output/'public'/url.removeprefix(base+'/')
            if path.is_dir():path=path/'index.html'
            if path.name=='read':path=path.with_name('read.html')
            stream=io.BytesIO(path.read_bytes());stream.status=200
            stream.getheader={'Content-Type':'application/octet-stream' if path.suffix=='.bin' else 'text/html',
                'Content-Disposition':'attachment' if path.suffix=='.bin' else 'inline',
                'X-Content-Type-Options':'nosniff','Cache-Control':'no-transform'}.get
            yield stream
        with patch('backup_package.public_response',response),patch('asset_policy.public_response',response),patch('reader_routes.public_response',response):
            self.assertEqual((1,1),check_backup_archive(self.repo,sha,base,'owner/press'))
            (output/'public'/KEY).write_bytes(RAW+b'changed')
            with self.assertRaises(AssetError):check_backup_archive(self.repo,sha,base,'owner/press')
        (output/'public'/KEY).write_bytes(RAW+b'changed')
        with self.assertRaises(AssetError):export_archive(self.repo,sha,self.base/'packages',self.base/'bad-export',asset_root=output/'public')


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(loader.loadTestsFromName(name,cls) for cls in (DownloadPolicyTests,DownloadPublicationTests)
                             for name in cls.__dict__ if name.startswith('test_'))
