#!/usr/bin/env python3
"""Qualify exact draft bytes privately before signing; never authorize public delivery."""
from .repository import repository_root
import argparse
import json
import sys
import subprocess
import time

from .source_snapshot import git
from .asset_policy import AssetError
from .identifiers import SLUG


def prepare(repo, revision, work, *, store=None, require_retention=False):
    from .publication_protocol import blob, work_metadata
    if not isinstance(work, str) or not SLUG.fullmatch(work):
        raise AssetError('invalid work slug')
    manuscript = 'works/' + work + '/manuscript.md'
    metadata = work_metadata(blob(repo, revision, manuscript, maximum=8 * 1024 * 1024))
    if metadata['slug'] != work:
        raise AssetError('work slug differs from its source path')
    from .qualify_assets import qualify
    mappings = qualify(repo, revision, work, store=store, require_retention=require_retention)
    assets = [{'key': key, **mapping} for key, mapping in sorted(mappings.items())]
    origin = None
    if assets:
        from .publication_protocol import blob, _json
        from .asset_policy import AssetPolicy
        settings = _json(blob(repo, revision, 'asset-policy.json'))
        origin = AssetPolicy(settings['origin']).origin
    return {'schema': 'guavajam.publication-preparation.v1', 'revision': revision,
            'source_tree': git(repo, 'rev-parse', revision + '^{tree}').strip().decode(), 'manuscript': manuscript,
            'assets_verified': True, 'public_delivery_verified': False,
            'retention_verified': bool(assets) and all((item['retain_until'] or 0) > int(time.time() * 1000) for item in assets),
            'assets': assets, 'asset_origin': origin, 'edition': int(metadata['edition'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--work', required=True)
    parser.add_argument('--require-retention', action='store_true', help='Require current, verified Object Lock protection for every asset')
    args = parser.parse_args()
    try:
        result = prepare(repository_root(), args.revision, args.work, require_retention=args.require_retention)
    except (AssetError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print('Publication preparation failed: ' + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
