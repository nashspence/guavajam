#!/usr/bin/env python3
"""Read an edition's committed state and next step; never authorize or mutate it."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from asset_policy import AssetError
from audit_assets import (edition_page, editorial_checkpoint, registered_manifest,
                          verify_public_release)
from build_edition import git, snapshot
from edition_package import package_names
from editorial_notices import edition_notices
from identifier_schema import edition_registration_path, validate_registrations
from issuance import read_registration
from public_record import edition_projection
from publish_edition import (check_release_target, release_inventory, release_records,
                             tag_commit)


NEXT = {
    'unreserved': 'Stage and qualify a reviewed source commit, review its proof, then merge its authorization.',
    'reserved': 'Retain the reviewed proof independently; review and merge the issuance claim before promotion.',
    'claimed': 'Use the protected publication workflow with the registered staging run and provenance digest.',
    'stopped': 'Run protected absence confirmation, then review the permanent abandonment record; reconcile any publication that won the race.',
    'cancelled': 'This version is permanently retired. Keep its record and use a new version for future work.',
    'abandoned': 'This version is permanently retired. Keep its claim and provenance; use a new version for future work.',
    'draft-incomplete': 'Resume protected promotion with the same reviewed proof; it verifies existing bytes and fills missing attachments.',
    'draft': 'Continue protected promotion with the same reviewed proof; status does not authorize publication.',
    'public-awaiting-receipt': 'Prepare the verified receipt, generate its records, and merge the receipt; retain the resulting archive and checkpoint independently.',
    'receipted': 'Retain the complete package, approved Git archive and current editorial checkpoint independently; refresh after notices or identifier changes.',
}


def _snapshot_status(root, paths, tag, repo):
    path = edition_registration_path(tag)
    report = {
        'schema': 'press.edition-status.v1', 'tag': tag, 'state': 'unreserved',
        'remote': {'state': 'not-checked'},
        'preservation': {'independent_custody': 'not-observed'},
    }
    record = manifest = None
    if path in paths:
        # Reuse the existing record contract, including terminal-state invariants.
        # Source content and registry issuance remain the required CI's responsibility.
        errors = validate_registrations(root, paths, {})
        if errors:
            raise AssetError('\n'.join(errors))
        record = read_registration(root, path)
        report['proof'] = {key: record[key] for key in (
            'source_commit', 'staged_run_id', 'provenance_sha256', 'artifact_sha256', 'record_url'
        ) if key in record}
        if 'cancellation_reason' in record:
            report['state'] = 'cancelled'
        elif 'abandoned_run_id' in record:
            report['state'] = 'abandoned'
        elif 'published_at' in record:
            report['state'] = 'receipted'
        elif 'stop_reason' in record:
            report['state'] = 'stopped'
        elif record.get('issuing') is True:
            report['state'] = 'claimed'
        else:
            report['state'] = 'reserved'
        if record.get('issuing') is True:
            manifest = registered_manifest(root, tag, record)
        if 'published_at' in record:
            _, slug, version = tag.split('/')
            edition_projection(root, slug, version, record)
            report['published_at'] = record['published_at']
            report['preservation']['editorial_sha256'] = editorial_checkpoint(
                record, edition_notices(root, tag), edition_page(root, tag))
            report['preservation']['next_step'] = (
                'Use make archive-export with saved packages and this checkpoint commit, '
                'then make check-backup-archive against the independently hosted export. '
                'Keep fixed issued package bytes and a trusted receipt as compatibility test data.'
            )
    if repo is not None:
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
            raise AssetError('expected repository owner/name')
        releases = [release for release in release_records(repo) if release.get('tag_name') == tag]
        if len(releases) > 1:
            raise AssetError('multiple releases use this edition tag')
        resolved = tag_commit(repo, tag)
        release = releases[0] if releases else None
        # A successful listing need not expose drafts to this caller. Only the
        # protected confirmation workflow may establish authoritative absence.
        report['remote'] = {
            'state': 'no-visible-release-or-tag', 'repository': repo,
            'draft_absence': 'not-established',
        }
        if record is None or report['state'] in {'cancelled', 'abandoned', 'reserved'}:
            if release is not None or resolved is not None:
                raise AssetError('remote release or tag exists without an active issuance claim')
        else:
            if resolved is not None and resolved != record['source_commit']:
                raise AssetError('edition tag differs from the reviewed source commit')
            if release is None:
                if 'published_at' in record:
                    raise AssetError('receipted edition is missing its public release')
                if resolved is not None:
                    raise AssetError('edition tag exists without a release; reconcile before continuing')
            elif release.get('draft') is True:
                if 'published_at' in record:
                    raise AssetError('receipted edition reverted to draft')
                names = package_names(manifest)
                uploaded = release_inventory(release, tag, manifest, names, draft=True)
                check_release_target(release, resolved, record['source_commit'])
                complete = set(uploaded) == set(names)
                report['remote'] = {
                    'state': 'draft', 'repository': repo, 'inventory': 'metadata-only',
                    'missing_attachments': sorted(set(names) - set(uploaded)),
                }
                if report['state'] != 'stopped':
                    report['state'] = 'draft' if complete else 'draft-incomplete'
            else:
                # Public status requires the existing exact-byte verification path.
                # API/auth failures propagate: unavailable is never mistaken for absent.
                verify_public_release(repo, release, root=root, require_receipt='published_at' in record)
                report['remote'] = {'state': 'public-verified', 'repository': repo}
                if 'published_at' not in record:
                    report['state'] = 'public-awaiting-receipt'
    report['next_step'] = NEXT[report['state']]
    if report['state'] == 'stopped' and report['remote']['state'] == 'draft':
        report['next_step'] = (
            'Inspect and resolve the unpublished draft and any tag first; '
            'then run protected absence confirmation and review the permanent abandonment record.'
        )
    return report


def inspect_edition(checkout, tag, *, repo=None):
    """Inspect committed HEAD; uncommitted registrations cannot impersonate a receipt."""
    edition_registration_path(tag)
    commit = git(checkout, 'rev-parse', '--verify', 'HEAD^{commit}')
    with tempfile.TemporaryDirectory(prefix='press-status-') as temp:
        root = Path(temp)
        paths = set(snapshot(checkout, commit, root))
        report = _snapshot_status(root, paths, tag, repo)
    report['checkpoint_commit'] = commit
    report['checkout_has_changes'] = bool(git(checkout, 'status', '--porcelain'))
    report['scope'] = 'Committed HEAD; this report is not a publication authorization or proof of independent custody.'
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tag', help='works/<slug>/vX.Y.Z')
    parser.add_argument('--online', action='store_true', help='read visible GitHub state and verify any public edition; requires gh; cannot confirm draft absence')
    parser.add_argument('--repo', default=os.environ.get('GITHUB_REPOSITORY', 'nashspence/press'))
    parser.add_argument('--json', action='store_true', help='print the structured status')
    args = parser.parse_args()
    try:
        report = inspect_edition(Path(__file__).resolve().parents[1], args.tag,
                                 repo=args.repo if args.online else None)
    except (AssetError, OSError, ValueError, KeyError, TypeError) as exc:
        print('ERROR:', exc, file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"{report['tag']}: {report['state']}")
        print('Committed snapshot:', report['checkpoint_commit'])
        print('Uncommitted changes:', 'present; ignored' if report['checkout_has_changes'] else 'none')
        print('Remote:', report['remote']['state'])
        if 'draft_absence' in report['remote']:
            print('Draft absence: not established; use the protected confirmation workflow when required.')
        for key, value in report.get('proof', {}).items():
            print(f'{key}: {value}')
        if 'missing_attachments' in report['remote']:
            print('Draft inventory: metadata only; missing:', ', '.join(report['remote']['missing_attachments']) or 'none')
        for key, value in report['preservation'].items():
            print(f'Preservation {key}: {value}')
        print('Next:', report['next_step'])
        print(report['scope'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
