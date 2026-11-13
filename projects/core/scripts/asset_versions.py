"""Verify and pin exact stored versions, optionally extending explicit retention."""
from pathlib import Path
import hashlib,io,json,os,re,time
from asset_policy import AssetError,response_chunks
from gateway_index import Index
from verify_media_sources import MIME as MEDIA_MIME
from asset_policy import MIME
from s3 import response


def pin_key(store,index,key,*,version=None,retain_until=None,mode=None):
    if not re.fullmatch(r'sha256/[0-9a-f]{64}\.(png|jpg|jpeg|webp|svg|bin|mp3|mp4|vtt)',key):raise AssetError('invalid asset key')
    previous=index.get(key)
    if previous:
        if version and version!=previous['version_id']:raise AssetError('asset URL already has a different fixed version')
        version=previous['version_id']
    if version is None:
        records=[v for v in store.versions() if v['fileName']==key]
        if not records or records[0]['action']!='upload':raise AssetError('asset has no visible uploaded version to pin')
        version=records[0]['fileId']
    info=store.info(version)
    digest,extension=key.removeprefix('sha256/').split('.')
    mime={**MIME,**MEDIA_MIME,'bin':'application/octet-stream'}[extension]
    maximum=20*1024**3 if extension in {'mp3','mp4'} else 10*1024*1024
    if info.get('fileId')!=version or info.get('bucketId')!=store.bucket_id or info.get('fileName')!=key or type(info.get('contentLength')) is not int or not 0<=info['contentLength']<=maximum or (info['contentLength']==0 and extension!='bin'):
        raise AssetError('stored version is outside its exact asset identity or byte budget')
    if info.get('contentType','').partition(';')[0]!=mime:raise AssetError('stored version MIME differs')
    saved=io.BytesIO() if extension not in {'mp3','mp4'} else None
    with response(store.s3_endpoint,store.bucket_name,key,version,store.key_id,store.key,method='GET') as received:
        if received.status!=200 or received.getheader('x-amz-version-id')!=version:raise AssetError('S3 returned a different version')
        total,hasher=0,hashlib.sha256()
        for chunk in response_chunks(received,maximum):
            total+=len(chunk);hasher.update(chunk)
            if saved is not None:saved.write(chunk)
        if total!=info['contentLength'] or hasher.hexdigest()!=digest:raise AssetError('stored exact-version SHA-256 differs from its asset URL')
    from media_formats import inspect
    if saved is None:
        from media_ranges import VerifiedRangeFile
        def private_range(_url,_mime,*,timeout,range_value):
            return response(store.s3_endpoint,store.bucket_name,key,version,store.key_id,store.key,method='GET',range_value=range_value)
        with VerifiedRangeFile(key,mime,total,version,private_range,version_header='x-amz-version-id') as probe:inspect(probe,extension)
    elif extension!='bin':inspect(saved,extension)
    else:
        from asset_policy import verify_bytes
        verify_bytes(saved.getvalue(),'bin',mime)
    retention=info.get('fileRetention',{})
    if not retention.get('isClientAuthorizedToRead'):raise AssetError('retention readback requires readFileRetentions')
    current=retention.get('value') or {};until=current.get('retainUntilTimestamp')
    if until is not None and (type(until) is not int or until<=0):raise AssetError('invalid retention readback')
    if previous and previous['retain_until'] and (not until or until<previous['retain_until']):raise AssetError('stored retention is shorter than the pinned commitment')
    if retain_until is not None:
        if type(retain_until) is not int or retain_until<=int(time.time()*1000) or mode not in {'governance','compliance'}:raise AssetError('supply an explicit future RETAIN_UNTIL and RETENTION_MODE')
        if current.get('mode') and current['mode']!=mode:raise AssetError('retention mode differs; never weaken an existing retention policy')
        if not until or until<retain_until:
            store.retain(key,version,mode,retain_until)
            info=store.info(version);retention=info.get('fileRetention',{});current=retention.get('value') or {};until=current.get('retainUntilTimestamp')
            if info.get('fileId')!=version or not retention.get('isClientAuthorizedToRead') or type(until) is not int:raise AssetError('invalid retention readback')
        if current.get('mode')!=mode or until<retain_until:raise AssetError('Object Lock retention readback differs')
    value={'schema':'press.asset.v1','sha256':digest,'extension':extension,'version_id':version,
           'byte_count':total,'content_type':mime,'retain_until':until}
    index.put(key,value,retention_mode=mode)
    return value


def main():
    import argparse
    from asset_storage import B2,credentials
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('key');parser.add_argument('--retain-until',type=int);parser.add_argument('--retention-mode',choices=['governance','compliance'])
    args=parser.parse_args()
    try:
        store=B2(credentials(Path(__file__).resolve().parents[1]),operation='pin')
        index=Index(store)
        result=pin_key(store,index,args.key,retain_until=args.retain_until,mode=args.retention_mode)
        print(json.dumps(result,sort_keys=True));return 0
    except (AssetError,OSError,ValueError) as exc:
        print('Asset pinning failed: '+str(exc),file=__import__('sys').stderr);return 1
if __name__=='__main__':raise SystemExit(main())
