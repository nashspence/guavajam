#!/usr/bin/env python3
"""Verify every referenced asset, including media, on every source check."""
from pathlib import Path
from urllib.parse import unquote, urlsplit
import hashlib
import sys
from asset_policy import AssetError, MAX_ASSETS, MAX_TOTAL_BYTES, fetch_asset, load_policy, verify_bytes
from validate_sources import git_sources, local_asset_size, parse_document, walk
from supporting_gfm import load_gfm_record, record_path
from press_media import candidates, declarations
from verify_media_sources import verify_items


def verify_source_assets(root, *, fetcher=fetch_asset, media_verifier=verify_items, paths=None, require_retention=False):
    root = root.resolve(); policy = load_policy(root); tracked = set(git_sources(root) if paths is None else paths)
    remote_sizes, all_media, budgets = {}, [], {}
    for path in sorted(tracked):
        if path.suffix != '.md' or path.parts[:2] == ('tests','fixtures'): continue
        if record_path(path):
            ast, *_ = load_gfm_record(root/path,root=root)
        else:
            ast, errors = parse_document(root/path,path)
            if errors: raise AssetError('\n'.join(errors))
        manuscript = len(path.parts)==3 and path.parts[0]=='works' and path.name=='manuscript.md'
        if list(candidates(ast['blocks'])):
            if not manuscript: raise AssetError('media declarations belong in a manuscript')
            if not policy: raise AssetError('media requires asset-policy.json')
            from validate_sources import validate_front_matter, inspect_manuscript_syntax
            _, body, line, errors = validate_front_matter(path,(root/path).read_text('utf-8'))
            info, issues = inspect_manuscript_syntax(path,body,line)
            if errors or issues: raise AssetError('invalid media source anchors')
            all_media.extend(declarations(ast,policy.origin,info['anchors']))
        images={n['c'][2][0] for n in walk(ast['blocks']) if n['t']=='Image'}
        downloads={n['c'][2][0] for n in walk(ast['blocks']) if n['t']=='Link' and policy and policy.manages(n['c'][2][0])}
        if not manuscript: images={url for url in images if policy and policy.manages(url)}
        if len(images|downloads)>MAX_ASSETS: raise AssetError(f'{path} exceeds the distinct asset limit')
        group=path.parts[:2] if path.parts[:1]==('works',) else (str(path),)
        budget=budgets.setdefault(group,{})
        for target in sorted(images|downloads):
            if ':' in target or target.startswith('//'):
                if not policy: raise AssetError('no approved asset origin configured')
                digest, extension=(policy.parse_image if target in images else policy.parse)(target)
                if extension in {'mp3','mp4','vtt'}:
                    all_media.append({'src':target});continue
                if target not in remote_sizes:
                    raw, mime=fetcher(policy,target)
                    if hashlib.sha256(raw).hexdigest()!=digest:raise AssetError('asset SHA-256 does not match URL')
                    verify_bytes(raw,extension,mime);remote_sizes[target]=len(raw)
                size=remote_sizes[target]
            else:
                uri=urlsplit(target);local=(root/path.parent/unquote(uri.path)).resolve()
                if uri.query or uri.fragment or not local.is_relative_to(root/path.parent/'assets') or local.relative_to(root) not in tracked:
                    raise AssetError(f'{path}: invalid local source image')
                size=local_asset_size(local)
            budget[target]=size
            if len(budget)>MAX_ASSETS:raise AssetError(f'{path} exceeds the distinct asset limit')
            if sum(budget.values())>MAX_TOTAL_BYTES:raise AssetError(f'{path} exceeds the total unique asset byte limit')
    if require_retention:
        from asset_policy import public_response
        import time,re
        urls=set(remote_sizes)|{v for item in all_media for k,v in item.items() if k in {'src','poster','captions'}}
        for url in sorted(urls):
            with public_response(url,'*/*',method='HEAD') as receipt:
                until=receipt.getheader('X-Press-Retain-Until','')
                version=receipt.getheader('X-Press-Asset-Version','')
                if receipt.status!=200 or not until.isdigit() or int(until)<=int(time.time()*1000) or not re.fullmatch('[A-Za-z0-9_-]{1,256}',version):
                    raise AssetError('publication requires a version-pinned asset with current retention: '+url)
    if all_media:
        media_verifier(all_media,policy.origin)
    return len(remote_sizes)+len({url for item in all_media for key,url in item.items() if key in {'src','poster','captions'}})


def main():
    try:count=verify_source_assets(Path(__file__).resolve().parents[1])
    except (AssetError,OSError,ValueError,TypeError) as exc:
        print('ERROR:',exc,file=sys.stderr);return 1
    print(f'Verified source assets: {count} content-addressed HTTPS objects');return 0

if __name__=='__main__':sys.exit(main())
