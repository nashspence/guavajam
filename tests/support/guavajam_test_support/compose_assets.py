"""Real local S3/media qualification, run only inside a disposable test container."""
import argparse
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


def persistence(phase):
    """Exercise actual S3 data/metadata across Compose down/up, then remove the marker."""
    identities=json.loads(Path('/qualification/identities.json').read_text())['identities']
    publisher=identities[0]['credentials'][0]
    values={'S3_ENDPOINT':'https://storage.publisher.test','S3_REGION':'us-east-1',
            'S3_BUCKET':'publication-assets','S3_ACCESS_KEY_ID':publisher['accessKey'],
            'S3_SECRET_ACCESS_KEY':publisher['secretKey']}
    store=S3(values,operation='upload' if phase=='write' else 'inspect')
    raw=b'WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nOwned disposable persistence fixture.\n'
    key='sha256/'+hashlib.sha256(raw).hexdigest()+'.vtt'
    if phase=='write':
        assert store.inventory()==[]
        store.upload(key,raw,'text/vtt')
    else:
        item=store.head(key)
        assert item and item.byte_count==len(raw) and item.content_type=='text/vtt'
        with store.read(item) as received:assert received.read()==raw
        S3(values,operation='delete').delete(item)
        assert store.inventory()==[]
    print(json.dumps({'persistent_s3_fixture_'+phase:True}),flush=True)


def main():
    identities=json.loads(Path('/qualification/identities.json').read_text())['identities']
    roles={x['name']:x['credentials'][0] for x in identities}
    publisher,delivery=roles['publisher'],roles['delivery']
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
                        assert received.status==404  # Exact known URL is still private.
                with public_response(origin+'/'+key,'*/*',range_value='bytes=0-127') as received:
                    assert received.status==404
            from .discovery_fixture import Publisher
            from guavajam_core.asset_release import release, Releases
            from unittest.mock import patch
            source_root=root/'publisher';source_root.mkdir()
            p=Publisher(source_root)
            remote=root/'origin.git'
            subprocess.run(['git','init','--bare','-q',str(remote)],check=True)
            p.git('remote','add','origin',str(remote));p.git('push','-q','origin','main')
            base=p.git('rev-parse','HEAD');p.git('switch','-c','review')
            (p.repo/'asset-policy.json').write_text(json.dumps({'origin':origin})+'\n')
            body='![Figure]('+origin+'/'+keys[0]+')\n\n'
            body+='\n\n'.join('[Reviewed media '+str(i)+']('+origin+'/'+key+')' for i,key in enumerate(keys[1:]))
            p.source('field-notes','Field Notes',body)
            p.git('add','-A');p.git('commit','-qm','Review private media');edition=p.publish()
            p.git('push','-q','origin','review')
            release_values={**values,'S3_ACCESS_KEY_ID':roles['release']['accessKey'],'S3_SECRET_ACCESS_KEY':roles['release']['secretKey']}
            releaser=S3(release_values,operation='release')
            with patch.dict(os.environ,{'GNUPGHOME':p.env['GNUPGHOME']}):
                try:release(p.repo,edition,base,store=releaser,fingerprints=[p.fingerprint],apply=True)
                except ValueError:pass
                else:raise AssertionError('unmerged signed branch could release media')
                assert all(Releases(store).get(key) is None for key in keys)
                p.git('switch','main');p.git('merge','--no-ff','review','-m','Accept reviewed edition')
                p.git('push','-q','origin','main');tip=p.git('rev-parse','HEAD')
                result=release(p.repo,edition,tip,store=releaser,fingerprints=[p.fingerprint],apply=True)
                assert result['public_delivery_verified']
                before=len(store.inventory())
                release(p.repo,edition,tip,store=releaser,fingerprints=[p.fingerprint],apply=True)
                assert len(store.inventory())==before
            # Test raw signed requests, not only client-mode guards.
            for config,key in [(store.config,Releases.path(keys[0])),(releaser.config,keys[0])]:
                try:
                    with response(config,key,method='PUT',body=b'forbidden',extra={'Content-Type':'application/json'}):pass
                except S3Error as error:assert error.status==403
                else:raise AssertionError('storage provider failed staging/release role separation')
            for name,key in zip(['image.png','recording.mp3','recording.mp4','captions.vtt'],keys):
                raw=(root/name).read_bytes();extension=Path(name).suffix[1:]
                digest=hashlib.sha256(raw).hexdigest();mapping=Index(store).get(key)
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
                print(json.dumps({'format':extension,'private_stage_signed_release_and_delivery_passed':True}),flush=True)
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
            cleanup=S3({**values,'S3_ACCESS_KEY_ID':roles['maintenance']['accessKey'],'S3_SECRET_ACCESS_KEY':roles['maintenance']['secretKey']},operation='delete')
            for item in cleanup.inventory():cleanup.delete(item)
            assert cleanup.inventory()==[]
            for key in keys:
                with public_response(origin+'/'+key,'*/*',method='HEAD') as received:assert received.status==404
            with response(store.config,query=[('uploads','')]) as received:
                assert b'<Upload>' not in received.read(1024*1024)
            print(json.dumps({'fixture_cleanup_verified':True}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--persistence',choices=['write','verify'])
    args=parser.parse_args()
    if args.persistence:persistence(args.persistence)
    else:main()
