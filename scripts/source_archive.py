#!/usr/bin/env python3
"""Preserve signed source Git objects and deduplicated assets; verify against external trust."""
from pathlib import Path
import argparse,hashlib,json,os,re,subprocess,sys,tempfile
from asset_policy import AssetError,load_policy,public_response,response_chunks,MAX_BYTES
from publication_commit import verify
from source_snapshot import snapshot,git
from verify_media_sources import MIME as MEDIA_MIME
from asset_policy import MIME


def references(root,files,manuscript):
    from validate_sources import parse_document,walk
    from supporting_gfm import record_path,load_gfm_record
    from press_media import declarations
    from validate_sources import validate_front_matter,inspect_manuscript_syntax
    policy=load_policy(root);urls=set()
    for relative in files:
        if relative.suffix!='.md' or not relative.is_relative_to(Path(manuscript).parent):continue
        if record_path(relative):ast,*_=load_gfm_record(root/relative,root=root)
        else:
            ast,errors=parse_document(root/relative,relative)
            if errors:raise AssetError('; '.join(errors))
        for node in walk(ast['blocks']):
            if node['t'] in {'Link','Image'} and policy and policy.manages(node['c'][2][0]):
                policy.parse(node['c'][2][0]);urls.add(node['c'][2][0])
        if relative==Path(manuscript):
            _,body,line,_=validate_front_matter(relative,(root/relative).read_text('utf-8'));info,_=inspect_manuscript_syntax(relative,body,line)
            for item in declarations(ast,policy.origin if policy else '',info['anchors']):
                urls.update(item[k] for k in ('src','poster','captions') if k in item)
    return sorted(urls)


def output_directory(path,repo):
    path=Path(path).absolute()
    if path.exists() or path.resolve().is_relative_to(Path(repo).resolve()):raise AssetError('archive output must be new and outside the source checkout')
    return path


def export(repo,revision,output,*,fingerprints=(),allowed_signers=None,response_factory=public_response):
    record=verify(repo,revision,fingerprints=fingerprints,allowed_signers=allowed_signers,online_media=False)
    output=output_directory(output,repo)
    # Build under the new directory's parent and publish it atomically after verification.
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.press-archive-',dir=output.parent) as directory:
        stage=Path(directory)/'archive';stage.mkdir(mode=0o700)
        bundle=stage/'source.bundle'
        git(repo,'bundle','create',str(bundle),'--all')
        objects={}
        with snapshot(repo,revision) as (root,files):
            policy=load_policy(root)
            for url in references(root,files,record['manuscript']):
                digest,extension=policy.parse(url);key='sha256/'+digest+'.'+extension
                target=stage/'objects'/key;target.parent.mkdir(parents=True,exist_ok=True)
                mime={**MIME,**MEDIA_MIME,'bin':'application/octet-stream'}[extension]
                limit=20*1024**3 if extension in {'mp3','mp4'} else MAX_BYTES
                size,hasher=0,hashlib.sha256()
                with response_factory(url,mime,timeout=1800) as received,target.open('xb') as saved:
                    if received.status!=200 or received.getheader('Content-Type','').partition(';')[0]!=mime:raise AssetError('archive source did not return the full approved media type')
                    for chunk in response_chunks(received,limit):size+=len(chunk);hasher.update(chunk);saved.write(chunk)
                if hasher.hexdigest()!=digest:raise AssetError('archive asset SHA-256 differs from signed source')
                objects[key]={'url':url,'sha256':digest,'byte_count':size,'content_type':mime}
        data={'schema':'press.source-archive.v1','revision':revision,'manuscript':record['manuscript'],
              'git_bundle_sha256':file_hash(bundle),'objects':objects}
        (stage/'ARCHIVE.json').write_text(json.dumps(data,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
        check(stage,revision,fingerprints=fingerprints,allowed_signers=allowed_signers)
        stage.rename(output)
    return len(objects)


def file_hash(path):
    hasher=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk:=stream.read(65536):hasher.update(chunk)
    return hasher.hexdigest()


def check(archive,revision,*,fingerprints=(),allowed_signers=None):
    archive=Path(archive).resolve();manifest=archive/'ARCHIVE.json'
    if allowed_signers and Path(allowed_signers).resolve().is_relative_to(archive):raise AssetError('archive cannot supply its own signing authority')
    if not manifest.is_file() or manifest.is_symlink() or manifest.stat().st_size>1024*1024:raise AssetError('invalid archive inventory')
    data=json.loads(manifest.read_text('utf-8'))
    if not isinstance(data,dict) or set(data)!={'schema','revision','manuscript','git_bundle_sha256','objects'} or data['schema']!='press.source-archive.v1' or data['revision']!=revision:
        raise AssetError('archive differs from the externally selected source revision')
    bundle=archive/'source.bundle'
    if bundle.is_symlink() or not bundle.is_file() or file_hash(bundle)!=data['git_bundle_sha256']:raise AssetError('Git bundle digest differs')
    with tempfile.TemporaryDirectory(prefix='press-restore-') as directory:
        restored=Path(directory)/'source.git'
        result=subprocess.run(['git','clone','--bare','--no-hardlinks',str(bundle),str(restored)],capture_output=True,check=False)
        if result.returncode:raise AssetError('Git bundle could not restore source objects')
        record=verify(restored,revision,fingerprints=fingerprints,allowed_signers=allowed_signers,online_media=False)
        if record['manuscript']!=data['manuscript']:raise AssetError('archive manuscript differs from the signed declaration')
        with snapshot(restored,revision) as (root,files):
            policy=load_policy(root);urls=references(root,files,record['manuscript'])
            expected={'sha256/'+'.'.join(policy.parse(url)):url for url in urls}
        if not isinstance(data['objects'],dict) or set(expected)!=set(data['objects']):raise AssetError('archive object inventory omits or adds signed dependencies')
        for key,url in expected.items():
            item=data['objects'][key];target=archive/'objects'/key
            if not target.is_file() or target.is_symlink() or not target.resolve().is_relative_to(archive/'objects'):
                raise AssetError('archive dependency is missing or unsafe')
            digest,extension=key.removeprefix('sha256/').split('.')
            limit=20*1024**3 if extension in {'mp3','mp4'} else MAX_BYTES
            if target.stat().st_size>limit:raise AssetError('saved dependency exceeds byte budget')
            if item.get('url')!=url or item.get('sha256')!=digest or file_hash(target)!=digest or target.stat().st_size!=item.get('byte_count'):
                raise AssetError('saved dependency differs from signed source bytes')
            if extension=='bin':
                from asset_policy import verify_bytes
                if target.stat().st_size>MAX_BYTES:raise AssetError('opaque archive asset exceeds byte budget')
                verify_bytes(target.read_bytes(),'bin','application/octet-stream')
            else:
                from media_formats import inspect
                with target.open('rb') as stream:inspect(stream,extension)
        return len(expected)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('operation',choices=['export','check']);parser.add_argument('--revision',required=True);parser.add_argument('--directory',required=True,type=Path)
    trust=parser.add_mutually_exclusive_group(required=True);trust.add_argument('--trusted-gpg-fingerprint',action='append',default=[]);trust.add_argument('--ssh-allowed-signers',type=Path);args=parser.parse_args()
    try:
        trust={'fingerprints':args.trusted_gpg_fingerprint,'allowed_signers':args.ssh_allowed_signers}
        count=export(Path(__file__).resolve().parents[1],args.revision,args.directory,**trust) if args.operation=='export' else check(args.directory,args.revision,**trust)
        print('Verified source archive dependencies:',count);return 0
    except (AssetError,OSError,ValueError,KeyError,TypeError) as exc:print('Source archive failed: '+str(exc),file=sys.stderr);return 1
if __name__=='__main__':sys.exit(main())
