"""Real local S3/media qualification, run only inside a disposable test container."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from guavajam.asset_storage import S3, credentials


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
    from .provider_qualification import run
    identities=json.loads(Path('/qualification/identities.json').read_text())['identities']
    base={'S3_ENDPOINT':'https://storage.publisher.test','S3_REGION':'us-east-1','S3_BUCKET':'publication-assets'}
    names={'publisher':'staging','delivery':'readonly','release':'release','maintenance':'maintenance'}
    roles={names[item['name']]:{**base,'S3_ACCESS_KEY_ID':item['credentials'][0]['accessKey'],
           'S3_SECRET_ACCESS_KEY':item['credentials'][0]['secretKey']} for item in identities}
    with tempfile.TemporaryDirectory() as temporary:
        result=run(Path(temporary),roles,'https://assets.publisher.test',strict_maintenance=False)
        assert result['passed'] and result['cleanup_verified']
        print(json.dumps(result),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--persistence',choices=['write','verify'])
    args=parser.parse_args()
    if args.persistence:persistence(args.persistence)
    else:main()
