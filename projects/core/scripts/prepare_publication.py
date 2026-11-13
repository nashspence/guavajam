#!/usr/bin/env python3
"""Prepare an exact draft revision and read back its private storage retention before owner signing."""
from pathlib import Path
import argparse,json,sys,time
from source_snapshot import snapshot,git
from asset_policy import AssetError,load_policy,public_response
from source_archive import references
from verify_source_assets import verify_source_assets
from validate_sources import validate_repository
from asset_storage import B2,credentials
from gateway_index import Index


def prepare(repo,revision,work,*,store=None,published=None,label=None):
    from publication_commit import PUBLICATION
    from publication_protocol import publication_trailers
    manuscript='works/'+work+'/manuscript.md'
    if not PUBLICATION.fullmatch(manuscript):raise AssetError('work must use its reviewed source slug')
    with snapshot(repo,revision) as (root,files):
        if Path(manuscript) not in files:raise AssetError('draft manuscript is not in the selected revision')
        errors=validate_repository(root,files,enforce_version=True)
        if errors:raise AssetError('; '.join(errors[:8]))
        selected=[path for path in files if path.is_relative_to(Path(manuscript).parent)]
        verify_source_assets(root,paths=selected,require_retention=True)
        urls=references(root,files,manuscript);policy=load_policy(root);assets=[]
        if urls:
            store=store or B2(credentials(Path(repo)),operation='inspect')
            index=Index(store)
            for url in urls:
                digest,extension=policy.parse(url);key='sha256/'+digest+'.'+extension;mapping=index.get(key)
                if mapping is None:raise AssetError('draft dependency has no private version pin')
                info=store.info(mapping['version_id']);retention=info.get('fileRetention',{})
                held=retention.get('value') or {}
                if (info.get('fileId')!=mapping['version_id'] or info.get('fileName')!=key or info.get('bucketId')!=store.bucket_id
                    or info.get('contentLength')!=mapping['byte_count'] or not retention.get('isClientAuthorizedToRead')
                    or held.get('mode') not in {'governance','compliance'} or type(held.get('retainUntilTimestamp')) is not int
                    or held['retainUntilTimestamp']<=int(time.time()*1000) or held['retainUntilTimestamp']<mapping['retain_until']):
                    raise AssetError('private B2 version or retention readback differs from the draft dependency')
                with public_response(url,'*/*',method='HEAD') as public:
                    if public.status!=200 or public.getheader('X-Press-Asset-Version')!=mapping['version_id']:raise AssetError('public delivery differs from the privately verified version')
                assets.append({'key':key,'version_id':mapping['version_id'],'retain_until':held['retainUntilTimestamp']})
    return {'schema':'press.publication-preparation.v1','revision':revision,
            'source_tree':git(repo,'rev-parse',revision+'^{tree}').strip().decode(),'manuscript':manuscript,
            'assets_verified':True,'retention_verified':True,'assets':assets,
            'publication_trailers':publication_trailers(manuscript,published,label)}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--revision',required=True);parser.add_argument('--work',required=True);parser.add_argument('--published');parser.add_argument('--label');args=parser.parse_args()
    try:result=prepare(Path(__file__).resolve().parents[1],args.revision,args.work,published=args.published,label=args.label)
    except (AssetError,OSError,ValueError,KeyError,TypeError) as exc:print('Publication preparation failed: '+str(exc),file=sys.stderr);return 1
    print(json.dumps(result,indent=2,sort_keys=True));return 0
if __name__=='__main__':sys.exit(main())
