"""Fixed versions, retention readback and sidecar-aware garbage collection."""
import contextlib,hashlib,io,sys,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from asset_policy import AssetError
from asset_versions import pin_key
from gateway_index import Index
from manage_assets import candidates
RAW=b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><circle cx="5" cy="5" r="4"/></svg>\n'
KEY='sha256/'+hashlib.sha256(RAW).hexdigest()+'.svg'
class Store:
    bucket_id='bucket';bucket_name='test-bucket';s3_endpoint='https://s3.us-west-004.backblazeb2.com';key_id='example';key='example'
    def __init__(self):self.rows=[];self.bytes={};self.upload(KEY,RAW,'image/svg+xml')
    def versions(self,prefix='sha256/'):return sorted([dict(r) for r in self.rows if r['fileName'].startswith(prefix)],key=lambda r:(r['fileName'],-r['uploadTimestamp']))
    def upload(self,key,raw,mime,**kwargs):
        version='version_'+str(len(self.rows));row={'bucketId':self.bucket_id,'fileName':key,'fileId':version,'contentLength':len(raw),'contentType':mime,'uploadTimestamp':len(self.rows),'action':'upload','fileRetention':{'isClientAuthorizedToRead':True,'value':{'mode':None,'retainUntilTimestamp':None}}};self.rows.append(row);self.bytes[version]=raw;return row
    def info(self,version):return next(r for r in self.rows if r['fileId']==version)
    def retain(self,key,version,mode,until):self.info(version)['fileRetention']['value']={'mode':mode,'retainUntilTimestamp':until}
    @contextlib.contextmanager
    def read(self,endpoint,bucket,key,version,key_id,secret,**kwargs):
        row=self.info(version);raw=self.bytes[version]
        class Response(io.BytesIO):
            status=200
            def getheader(self,name,default=None):return {'Content-Type':row['contentType'],'Content-Length':str(len(raw)),'x-amz-version-id':version}.get(name,default)
        with Response(raw) as response:yield response
class PinTests(unittest.TestCase):
    def setUp(self):
        self.store=Store();self.index=Index(self.store)
        self.enterContext(patch('gateway_index.response',side_effect=self.store.read));self.enterContext(patch('asset_versions.response',side_effect=self.store.read))
    def test_exact_version_and_sidecar_are_verified_and_locked_on_readback(self):
        until=int(time.time()*1000)+100000
        value=pin_key(self.store,self.index,KEY,retain_until=until,mode='governance')
        self.assertEqual('version_0',value['version_id']);self.assertEqual(until,value['retain_until']);self.assertEqual(value,self.index.get(KEY))
        self.assertTrue(all(r['fileRetention']['value']['retainUntilTimestamp']==until for r in self.store.rows))
        self.assertEqual({},candidates(self.store.versions(),set(),time.time()))
    def test_pin_cannot_switch_to_a_replacement_version(self):
        first=pin_key(self.store,self.index,KEY)
        replacement=self.store.upload(KEY,b'wrong bytes','image/svg+xml')
        with self.assertRaisesRegex(AssetError,'different fixed version'):pin_key(self.store,self.index,KEY,version=replacement['fileId'])
        self.assertEqual(first,pin_key(self.store,self.index,KEY))
    def test_a_false_hash_or_retention_readback_cannot_be_pinned(self):
        self.store.bytes['version_0']=b'wrong bytes'
        with self.assertRaisesRegex(AssetError,'SHA-256'):pin_key(self.store,self.index,KEY)
        self.assertIsNone(self.index.get(KEY))
    def test_expired_or_unreferenced_assets_collect_their_pin_sidecars_together(self):
        pin_key(self.store,self.index,KEY)
        planned=candidates(self.store.versions(),set(),time.time()+40*86400)
        self.assertEqual({KEY},set(planned));self.assertEqual(2,len(planned[KEY]))
        self.assertEqual({},candidates(self.store.versions(),{hashlib.sha256(RAW).hexdigest()},time.time()+40*86400))
