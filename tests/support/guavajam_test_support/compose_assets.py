"""Real local S3/media qualification, run only inside a disposable test container."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from PIL import Image
from guavajam_core.asset_storage import S3, credentials
from guavajam_core.asset_policy import public_response, response_chunks
from guavajam_core.gateway_index import Index
from guavajam_core.s3 import S3Error, response
from .media_fixture import audio, video


def main():
    identities=json.loads(Path('/qualification/identities.json').read_text())['identities']
    publisher,delivery=[x['credentials'][0] for x in identities]
    with tempfile.TemporaryDirectory() as temporary:
        root=Path(temporary)
        subprocess.run(['git','init','-q',str(root)],check=True)
        origin='https://assets.publisher.test'
        (root/'asset-policy.json').write_text(json.dumps({'origin':origin}))
        values={'S3_ENDPOINT':'https://storage.publisher.test','S3_REGION':'us-east-1','S3_BUCKET':'publication-assets','S3_ACCESS_KEY_ID':publisher['accessKey'],'S3_SECRET_ACCESS_KEY':publisher['secretKey']}
        config=root/'.env.assets';config.write_text(''.join(name+'='+value+'\n' for name,value in values.items()));config.chmod(0o600)
        store=S3(credentials(root),operation='inspect')
        assert store.inventory()==[]
        audio(root/'recording.mp3');video(root/'recording.mp4')
        with (root/'recording.mp4').open('ab') as stream:
            size=40*1024*1024
            stream.write((size+8).to_bytes(4,'big')+b'free');stream.write(b'\0'*size)
        Image.new('RGB',(17,13),(40,90,120)).save(root/'image.png')
        (root/'captions.vtt').write_text('WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nA qualification recording.\n')
        keys=[]
        try:
            for name in ['image.png','recording.mp3','recording.mp4','captions.vtt']:
                path=root/name;extension=path.suffix[1:];raw=path.read_bytes();digest=hashlib.sha256(raw).hexdigest()
                key='sha256/'+digest+'.'+extension;keys.append(key)
                result=subprocess.run([sys.executable,'-m','guavajam_core.manage_assets','upload',str(path)],cwd=root,capture_output=True,text=True)
                if result.returncode:
                    # Only fixed CLI error categories may leave this synthetic test.
                    errors=[line for line in result.stderr.splitlines() if line.startswith('ERROR:')]
                    raise RuntimeError('publisher upload failed: '+extension+'; '+(' '.join(errors)[:250]))
                mapping=Index(store).get(key);assert mapping and mapping['byte_count']==len(raw)
                assert mapping['version_id'] is None and mapping['retain_until'] is None
                if extension=='mp4':assert mapping['etag'].endswith('-2"')
                for method in ['HEAD','GET']:
                    with public_response(origin+'/'+key,'*/*',method=method) as received:
                        assert received.status==200 and received.getheader('ETag')==mapping['etag']
                        assert received.getheader('Content-Length')==str(len(raw))
                        assert received.getheader('X-Press-Asset-Version') is None
                        if method=='GET':assert hashlib.sha256(b''.join(response_chunks(received,len(raw)))).hexdigest()==digest
                if extension in {'mp3','mp4'}:
                    for value,expected in [('bytes=0-127',raw[:128]),('bytes=-128',raw[-128:])]:
                        with public_response(origin+'/'+key,'*/*',range_value=value) as received:
                            assert received.status==206 and b''.join(response_chunks(received,128))==expected
                    with public_response(origin+'/'+key,'*/*',range_value='bytes=999999999999-') as received:assert received.status==416
                print(json.dumps({'format':extension,'upload_hash_pin_and_delivery_passed':True}),flush=True)
            readonly=S3({**values,'S3_ACCESS_KEY_ID':delivery['accessKey'],'S3_SECRET_ACCESS_KEY':delivery['secretKey']},operation='upload')
            try:readonly.upload(keys[0],b'forbidden','image/png')
            except S3Error as error:assert error.status==403
            else:raise AssertionError('delivery credential could write')
            unknown=S3({**values,'S3_ACCESS_KEY_ID':'unconfigured-fixture-key','S3_SECRET_ACCESS_KEY':'unconfigured-fixture-secret'},operation='inspect')
            try:unknown.head(keys[0])
            except S3Error as error:assert error.status==403
            else:raise AssertionError('unknown credential could read')
            try:urllib.request.urlopen(values['S3_ENDPOINT']+'/'+values['S3_BUCKET']+'/'+keys[0],timeout=5)
            except urllib.error.HTTPError as error:assert error.code==403
            else:raise AssertionError('anonymous request could read')
            print(json.dumps({'delivery_write_rejected':True,'unknown_and_anonymous_access_rejected':True,'two_part_multipart_verified':True}),flush=True)
        finally:
            cleanup=S3(credentials(root),operation='delete')
            for item in cleanup.inventory():cleanup.delete(item)
            assert cleanup.inventory()==[]
            for key in keys:
                with public_response(origin+'/'+key,'*/*',method='HEAD') as received:assert received.status==404
            with response(store.config,query=[('uploads','')]) as received:
                assert b'<Upload>' not in received.read(1024*1024)
            print(json.dumps({'fixture_cleanup_verified':True}),flush=True)


if __name__=='__main__':main()
