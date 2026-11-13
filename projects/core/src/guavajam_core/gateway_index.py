"""Private B2 sidecars pin digest URLs; the existing bucket key owns ingestion and cleanup."""
import json
from .asset_policy import AssetError,response_chunks
from .s3 import response

PIN_PREFIX='sha256/.pins/'


def validate(value,key):
    import re
    match=re.fullmatch(r'sha256/([0-9a-f]{64})\.(png|jpg|jpeg|webp|svg|bin|mp3|mp4|vtt)',key)
    if not isinstance(value,dict) or not match or set(value)!={'schema','sha256','extension','version_id','byte_count','content_type','retain_until'}:raise AssetError('invalid asset-version mapping fields')
    if value['schema']!='press.asset.v1' or value['sha256']!=match[1] or value['extension']!=match[2]:raise AssetError('asset-version mapping identity differs')
    if not isinstance(value['version_id'],str) or not re.fullmatch('[A-Za-z0-9_-]{1,256}',value['version_id']):raise AssetError('invalid fixed B2 version identity')
    limit=20*1024**3 if match[2] in {'mp3','mp4'} else 10*1024*1024
    if type(value['byte_count']) is not int or not 0<=value['byte_count']<=limit or (value['byte_count']==0 and match[2]!='bin'):raise AssetError('invalid asset byte count')
    if value['retain_until'] is not None and (type(value['retain_until']) is not int or value['retain_until']<=0):raise AssetError('invalid asset retention time')
    from .verify_media_sources import MIME as MEDIA_MIME
    from .asset_policy import MIME
    if value['content_type']!={**MIME,**MEDIA_MIME,'bin':'application/octet-stream'}[match[2]]:raise AssetError('asset MIME differs from extension')


class Index:
    def __init__(self,store):self.store=store
    @staticmethod
    def path(key):return PIN_PREFIX+key.removeprefix('sha256/')+'.json'
    def get(self,key):
        name=self.path(key);records=[r for r in self.store.versions(name) if r['fileName']==name]
        if not records or records[0]['action']=='hide':return None
        if records[0]['action']!='upload':raise AssetError('asset-version mapping upload is incomplete')
        with response(self.store.s3_endpoint,self.store.bucket_name,name,records[0]['fileId'],self.store.key_id,self.store.key,method='GET') as received:
            if received.status!=200 or received.getheader('Content-Type','').partition(';')[0]!='application/json':raise AssetError('invalid private asset-version mapping response')
            raw=b''.join(response_chunks(received,8192))
        try:value=json.loads(raw);validate(value,key);return value
        except (ValueError,TypeError):raise AssetError('invalid private asset-version mapping') from None
    def put(self,key,value,*,retention_mode=None):
        validate(value,key);previous=self.get(key)
        if previous and any(previous.get(k)!=value.get(k) for k in ('version_id','sha256','extension','byte_count','content_type')):
            raise AssetError('digest URL is pinned to another version; investigate instead of replacing it')
        if previous==value:return
        name=self.path(key);raw=(json.dumps(value,sort_keys=True)+'\n').encode()
        receipt=self.store.upload(name,raw,'application/json',cache_control='no-store')
        if retention_mode and value['retain_until']:
            self.store.retain(name,receipt['fileId'],retention_mode,value['retain_until'])
            held=self.store.info(receipt['fileId']).get('fileRetention',{})
            lock=held.get('value') or {}
            if not held.get('isClientAuthorizedToRead') or lock.get('mode')!=retention_mode or type(lock.get('retainUntilTimestamp')) is not int or lock['retainUntilTimestamp']<value['retain_until']:raise AssetError('mapping retention readback differs')
        if self.get(key)!=value:raise AssetError('asset-version mapping readback differs')
