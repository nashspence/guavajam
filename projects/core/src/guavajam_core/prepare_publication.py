#!/usr/bin/env python3
"""Verify an exact draft's assets and report storage guarantees before owner signing."""
from .repository import repository_root
from pathlib import Path
import argparse
import json
import sys
import time
from urllib.parse import quote

from .source_snapshot import snapshot, git
from .asset_policy import AssetError, load_policy, public_response
from .source_archive import references
from .verify_source_assets import verify_source_assets
from .validate_sources import validate_repository
from .asset_storage import S3, credentials
from .gateway_index import Index
from .object_store import immutable_version, same_version


def prepare(repo, revision, work, *, published, store=None, label=None, require_retention=False):
    from .publication_protocol import publication_trailers
    manuscript = 'works/' + work + '/manuscript.md'
    trailers = publication_trailers(manuscript, published, label)
    with snapshot(repo, revision) as (root, files):
        if Path(manuscript) not in files:
            raise AssetError('draft manuscript is not in the selected revision')
        errors = validate_repository(root, files, enforce_version=True)
        if errors:
            raise AssetError('; '.join(errors[:8]))
        selected = [path for path in files if path.is_relative_to(Path(manuscript).parent)]
        verify_source_assets(root, paths=selected, require_retention=require_retention)
        urls = references(root, files, manuscript)
        policy, assets = load_policy(root), []
        if urls:
            store = store or S3(credentials(Path(repo)), operation='inspect')
            index = Index(store)
            for url in urls:
                digest, extension = policy.parse(url)
                key = 'sha256/' + digest + '.' + extension
                mapping = index.get(key)
                if mapping is None:
                    raise AssetError('draft dependency has no private object pin')
                info = store.head(key, mapping['version_id'], etag=mapping['etag'])
                if (info is None or info.key != key or not same_version(info.version_id, mapping['version_id'])
                        or info.etag != mapping['etag'] or info.byte_count != mapping['byte_count']
                        or info.content_type != mapping['content_type']):
                    raise AssetError('private object readback differs from the draft dependency')
                until = mapping['retain_until']
                if until and (not info.retain_until or info.retain_until < until
                              or info.retention_mode != mapping['retention_mode']):
                    raise AssetError('private retention readback differs from the pinned commitment')
                protected = bool(until and until > int(time.time() * 1000))
                if require_retention and not protected:
                    raise AssetError('requested publication retention is not available')
                with public_response(url, '*/*', method='HEAD') as public:
                    version = quote(mapping['version_id'], safe='') if immutable_version(mapping['version_id']) else None
                    if (public.status != 200 or public.getheader('ETag') != mapping['etag']
                            or public.getheader('X-Press-Asset-Version') != version
                            or public.getheader('X-Press-Retain-Until') != str(until or 0)):
                        raise AssetError('public delivery differs from the privately verified object')
                assets.append({'key': key, 'version_id': mapping['version_id'], 'etag': mapping['etag'],
                               'retain_until': until, 'retention_mode': mapping['retention_mode']})
    return {'schema': 'press.publication-preparation.v1', 'revision': revision,
            'source_tree': git(repo, 'rev-parse', revision + '^{tree}').strip().decode(), 'manuscript': manuscript,
            'assets_verified': True,
            'retention_verified': bool(assets) and all((item['retain_until'] or 0) > int(time.time() * 1000) for item in assets),
            'assets': assets, 'publication_trailers': trailers}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--work', required=True)
    parser.add_argument('--published', required=True)
    parser.add_argument('--label')
    parser.add_argument('--require-retention', action='store_true', help='Require current, verified Object Lock protection for every asset')
    args = parser.parse_args()
    try:
        result = prepare(repository_root(), args.revision, args.work, published=args.published,
                         label=args.label, require_retention=args.require_retention)
    except (AssetError, OSError, ValueError, KeyError, TypeError) as exc:
        print('Publication preparation failed: ' + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
