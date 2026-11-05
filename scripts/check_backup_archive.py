#!/usr/bin/env python3
"""Verify every issued edition and work on standby against an independently retained Git commit."""
import argparse
import hashlib
import os
import sys
import tempfile
from pathlib import Path

from asset_policy import AssetError
from audit_assets import audit_check, edition_page, registered_manifest
from backup_package import verify_backup_package
from build_edition import snapshot
from editorial_notices import edition_notices
from public_record import REGISTRATIONS, WORK_RECORDS, claimed_registrations
from reader_routes import verify_reader_routes, verify_work_route


def check_backup_archive(checkout, commit, base, repo):
    failures = []
    works = set()
    editions = 0
    with tempfile.TemporaryDirectory(prefix="press-standby-") as temp:
        root = Path(temp)
        paths = snapshot(checkout, commit, root)
        for path in sorted(p for p in paths if p.is_relative_to(REGISTRATIONS)):
            with audit_check(failures, str(path)):
                for slug, version, receipt in claimed_registrations(root, [path]):
                    if "published_at" not in receipt:
                        continue
                    tag = f"works/{slug}/{version}"
                    editions += 1
                    with audit_check(failures, f"{tag} standby package"):
                        verify_backup_package(base, tag, receipt)
                    manifest = registered_manifest(root, tag, receipt)
                    with audit_check(failures, f"{tag} standby reader"):
                        verify_reader_routes(manifest, repo, direct_base=base, published_at=receipt['published_at'],
                                             notices=edition_notices(root, tag), identifiers=receipt.get('identifiers'),
                                             checkpoint_page=edition_page(root, tag))
                    if slug in works:
                        continue
                    works.add(slug)
                    with audit_check(failures, f"works/{slug} standby work page"):
                        page = root / WORK_RECORDS / slug / 'index.html'
                        digest = hashlib.sha256(page.read_bytes()).hexdigest()
                        verify_work_route(manifest, repo, digest, direct_base=base)
    if not editions:
        failures.append("trusted inventory contains no issued editions")
    if failures:
        raise AssetError("\n".join(failures))
    return editions, len(works)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True, help='full commit SHA from the independently retained current inventory')
    parser.add_argument('--backup-base', required=True, help='direct recovery host base URL')
    parser.add_argument('--repo', default=os.environ.get('GITHUB_REPOSITORY', 'nashspence/press'))
    args = parser.parse_args()
    try:
        editions, works = check_backup_archive(Path.cwd(), args.commit, args.backup_base, args.repo)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print('ERROR:', exc, file=sys.stderr)
        return 1
    print(f'Verified standby against {args.commit}: {editions} editions, {works} works')
    return 0


if __name__ == '__main__':
    sys.exit(main())
