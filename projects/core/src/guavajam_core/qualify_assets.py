"""Qualify one exact committed draft using private storage, without release authority."""
import argparse
import json
from pathlib import Path
import sys
import subprocess
from .asset_policy import AssetError, load_policy
from .asset_storage import S3, credentials
from .identifiers import SLUG
from .private_assets import PrivateAssets
from .source_archive import references
from .source_snapshot import snapshot
from .validate_sources import validate_repository
from .verify_source_assets import verify_source_assets


def qualify(repo, revision, work, *, store=None, require_retention=False):
    if not SLUG.fullmatch(work):
        raise AssetError('invalid work slug')
    manuscript = Path('works') / work / 'manuscript.md'
    with snapshot(repo, revision) as (root, files):
        if manuscript not in files:
            raise AssetError('draft manuscript is absent')
        errors = validate_repository(root, files, enforce_version=True)
        if errors:
            raise AssetError('draft source validation failed')
        selected = [path for path in files if path.is_relative_to(manuscript.parent)]
        urls = references(root, files, str(manuscript))
        if urls:
            store = store or S3(credentials(repo), operation='inspect')
            reader = PrivateAssets(store, load_policy(root), require_retention=require_retention)
            mappings = reader.verify(root, selected)
            expected = {'sha256/' + '.'.join(load_policy(root).parse(url)) for url in urls}
            if set(mappings) != expected:
                raise AssetError('qualification inventory differs')
        else:
            verify_source_assets(root, paths=selected)
            mappings = {}
    return mappings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path('.'))
    parser.add_argument('--revision', required=True)
    parser.add_argument('--work', required=True)
    parser.add_argument('--require-retention', action='store_true')
    args = parser.parse_args(argv)
    try:
        items = qualify(args.repo, args.revision, args.work, require_retention=args.require_retention)
        print(json.dumps({'revision': args.revision, 'assets': len(items),
                          'privately_verified': True, 'public_delivery_verified': False}, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('Private asset qualification failed; no public changes made.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
