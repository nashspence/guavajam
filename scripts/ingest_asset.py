"""Bounded original-byte media ingestion, exact-version pinning and public readback."""
from pathlib import Path
import hashlib,json,os,stat
from asset_policy import AssetError,load_policy
from asset_storage import LEASE_PREFIX,PREFIX
from asset_versions import pin_key
from verify_media_sources import MIME,MAX_MEDIA_BYTES,verify_one
from media_formats import inspect


def upload_media(root,path,store,index,*,verifier=verify_one):
    path=Path(path)
    if path.suffix not in {'.mp3','.mp4','.vtt'}:raise AssetError('typed media upload requires MP3, MP4 or WebVTT')
    policy=load_policy(root)
    if policy is None:raise AssetError('no approved asset origin configured')
    extension=path.suffix[1:];limit=MAX_MEDIA_BYTES if extension in {'mp3','mp4'} else 10*1024*1024
    if path.resolve()!=path.absolute():raise AssetError('media input must be a regular file without symlinks')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as source:
        before=os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=limit:raise AssetError('media input exceeds its byte budget or is not a regular file')
        inspect(source,extension)
        source.seek(0);hasher=hashlib.sha256()
        while chunk:=source.read(65536):hasher.update(chunk)
        after=os.fstat(source.fileno())
        if (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):raise AssetError('media changed during validation')
    key=PREFIX+hasher.hexdigest()+'.'+extension;url=policy.origin+'/'+key
    store.upload(LEASE_PREFIX+key[len(PREFIX):],json.dumps({'schema':'press.asset-lease.v1','key':key}).encode(),'application/json',cache_control='no-store')
    existing=[v for v in store.versions(key) if v['fileName']==key]
    if existing:
        if existing[0]['action']!='upload':raise AssetError('stored media is hidden or unfinished; inspect before restoring it')
        state='Already stored'
    else:
        if before.st_size<=10*1024*1024:store.upload(key,path.read_bytes(),MIME[extension])
        else:store.upload_file(key,path,MIME[extension])
        state='Uploaded'
    pin_key(store,index,key)
    if verifier(url,policy.origin)!=before.st_size:raise AssetError('public media byte count differs from the original')
    return state,url
