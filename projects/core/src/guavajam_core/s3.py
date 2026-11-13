"""Minimal authenticated S3 reads for an exact Backblaze object version."""
import hashlib,hmac,datetime,re,urllib.request,urllib.error
from urllib.parse import urlsplit,quote
from .asset_policy import AssetError


def headers(endpoint,bucket,key,version,key_id,secret,*,method='HEAD',range_value=None,now=None):
    uri=urlsplit(endpoint)
    match=re.fullmatch(r's3\.([a-z0-9-]+)\.backblazeb2\.com',uri.netloc)
    if uri.scheme!='https' or not match or uri.path not in {'','/'} or uri.query or uri.fragment:raise AssetError('invalid B2 S3 endpoint')
    if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9.-]{1,99}',bucket) or not re.fullmatch(r'sha256/(?:[0-9a-f]{64}\.[a-z0-9]+|\.pins/[0-9a-f]{64}\.[a-z0-9]+\.json)',key) or not re.fullmatch('[A-Za-z0-9_-]{1,256}',version):raise AssetError('invalid fixed S3 object identity')
    instant=now or datetime.datetime.now(datetime.timezone.utc);stamp=instant.strftime('%Y%m%dT%H%M%SZ');day=stamp[:8];region=match[1]
    path='/'+quote(bucket,safe='')+'/'+quote(key,safe='/');query='versionId='+quote(version,safe='')
    digest=hashlib.sha256(b'').hexdigest()
    canonical_headers='host:'+uri.netloc+'\nx-amz-content-sha256:'+digest+'\nx-amz-date:'+stamp+'\n'
    signed='host;x-amz-content-sha256;x-amz-date';canonical='\n'.join([method,path,query,canonical_headers,signed,digest])
    scope=day+'/'+region+'/s3/aws4_request';message='AWS4-HMAC-SHA256\n'+stamp+'\n'+scope+'\n'+hashlib.sha256(canonical.encode()).hexdigest()
    def sign(key,data):return hmac.new(key,data.encode(),hashlib.sha256).digest()
    signing=sign(sign(sign(sign(('AWS4'+secret).encode(),day),region),'s3'),'aws4_request')
    signature=hmac.new(signing,message.encode(),hashlib.sha256).hexdigest()
    result={'Host':uri.netloc,'X-Amz-Content-Sha256':digest,'X-Amz-Date':stamp,'Authorization':'AWS4-HMAC-SHA256 Credential='+key_id+'/'+scope+', SignedHeaders='+signed+', Signature='+signature,'Accept-Encoding':'identity'}
    if range_value:result['Range']=range_value
    return endpoint.rstrip('/')+path+'?'+query,result


def response(endpoint,bucket,key,version,key_id,secret,*,method='HEAD',range_value=None):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    url,authorization=headers(endpoint,bucket,key,version,key_id,secret,method=method,range_value=range_value)
    try:return urllib.request.build_opener(NoRedirect()).open(urllib.request.Request(url,method=method,headers=authorization),timeout=45)
    except urllib.error.HTTPError as exc:raise AssetError('fixed-version B2 read failed (HTTP '+str(exc.code)+'); details omitted') from None
    except OSError:raise AssetError('fixed-version B2 read failed; details omitted') from None
