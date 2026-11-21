"""Bounded codec probing of the object representation whose full SHA-256 was verified."""
from collections import OrderedDict
import io
from .asset_policy import AssetError,response_chunks
from .object_store import valid_etag

BLOCK=1024*1024
CACHE_BLOCKS=4
MAX_PROBE_BYTES=64*1024*1024

class VerifiedRangeFile(io.RawIOBase):
    def __init__(self,url,mime,size,version,response_factory,*,version_header="ETag"):
        super().__init__()
        if not valid_etag(version):raise AssetError('codec probing requires a strong pinned ETag')
        self.url=url;self.mime=mime;self.size=size;self.version=version;self.factory=response_factory
        self.version_header=version_header
        self.position=0;self.cache=OrderedDict();self.fetched=0
    def readable(self):return True
    def seekable(self):return True
    def tell(self):return self.position
    def seek(self,offset,whence=io.SEEK_SET):
        if whence==0x10000:return self.size
        position=offset if whence==io.SEEK_SET else self.position+offset if whence==io.SEEK_CUR else self.size+offset if whence==io.SEEK_END else -1
        if position<0:raise AssetError('invalid media probe seek')
        self.position=position;return position
    def block(self,number):
        if number in self.cache:self.cache.move_to_end(number);return self.cache[number]
        start=number*BLOCK;end=min(self.size-1,start+BLOCK-1);length=end-start+1
        if not valid_etag(self.version) or self.fetched+length>MAX_PROBE_BYTES:raise AssetError('codec probing requires a pinned ETag and bounded range reads')
        with self.factory(self.url,self.mime,timeout=60,range_value=f'bytes={start}-{end}') as response:
            if (response.status!=206 or response.getheader('Content-Type','').partition(';')[0].strip()!=self.mime
                or response.getheader('Content-Range')!=f'bytes {start}-{end}/{self.size}'
                or response.getheader(self.version_header)!=self.version):
                raise AssetError('codec probe returned a different representation or byte range')
            raw=b''.join(response_chunks(response,length))
        if len(raw)!=length:raise AssetError('codec probe is truncated')
        self.fetched+=length;self.cache[number]=raw
        while len(self.cache)>CACHE_BLOCKS:self.cache.popitem(last=False)
        return raw
    def read(self,size=-1):
        if size<0:raise AssetError('unbounded codec reads are prohibited')
        if size==0 or self.position>=self.size:return b''
        number,offset=divmod(self.position,BLOCK);raw=self.block(number)
        result=raw[offset:offset+min(size,self.size-self.position)];self.position+=len(result);return result
    def readinto(self,buffer):
        raw=self.read(len(buffer));buffer[:len(raw)]=raw;return len(raw)
