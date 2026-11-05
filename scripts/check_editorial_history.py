"""Validate tracked editorial notices and preserve the history of issued editions."""
import argparse
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import quote

from asset_policy import AssetError
from audit_assets import verify_public_release
from build_edition import COMMIT, git
from editorial_notices import edition_notices
from identifier_schema import TAG, require_current_edition_origin, strict_json, validate_registrations
from issuance import authorization, read_registration
from publish_edition import api, release_records, tag_commit
from reviewed_artifact import download_reviewed
from verify_edition import verify_edition


def git_paths(root, *args):
    raw = git(root, *args, binary=True)
    return {Path(item.decode('utf-8')) for item in raw.split(b'\0') if item}


def require_compatible_proof(root, tag, record):
    require_current_edition_origin(root, {'tag': tag, 'record_url': record['record_url']})
    if git(root, 'diff', '--cached', '--raw', '--no-renames', record['source_commit'], '--', '.github/workflows'):
        raise AssetError(f'{tag}: workflow files differ from the proof source; restore compatible workflows or restage')


def check_editorial_history(root: Path, base: str | None = None, *, repo: str | None = None) -> None:
    root = root.resolve()
    if (root / 'registrations').is_symlink():
        raise AssetError('registrations: expected a directory without symlinks')
    scopes = ('notices', 'registrations/editions', 'registrations/works')
    tracked = git_paths(root, 'ls-files', '-z', '--', *scopes, ':(glob)works/*/manuscript.md')
    groups = {}
    for path in sorted(tracked):
        if path.parts[0] == 'works':
            continue
        file = root / path
        if not file.is_file() or file.resolve() != file:
            raise AssetError(f'{path}: expected a tracked regular file without symlinks')
        if path.parts[0] == 'notices':
            if len(path.parts) != 4:
                raise AssetError(f'{path}: expected notices/<slug>/vX.Y.Z/NNNN.md')
            tag = f'works/{path.parts[1]}/{path.parts[2]}'
            if not TAG.fullmatch(tag):
                raise AssetError(f'{path}: invalid notice edition tag')
            groups[tag] = groups.get(tag, 0) + 1

    errors = validate_registrations(root, tracked, {})
    if errors:
        raise AssetError('\n'.join(errors))
    for tag, count in groups.items():
        _, slug, version = tag.split('/')
        registration = Path('registrations/editions') / slug / (version + '.json')
        if registration not in tracked:
            raise AssetError(f'{tag}: notices require a tracked edition registration')
        record = read_registration(root, registration)
        if 'published_at' not in record:
            raise AssetError(f'{tag}: notices require the edition’s publication receipt')
        notices = edition_notices(root, tag)
        if len(notices) != count:
            raise AssetError(f'{tag}: all notice entries must be tracked')
        previous_date = record['published_at'][:10]
        for notice in notices:
            date = notice[2:12]  # edition_notices returns validated "- YYYY-MM-DD …" entries.
            if date < previous_date:
                raise AssetError(f'{tag}: notice predates publication or the preceding notice')
            previous_date = date

    if base is None:
        return
    if not COMMIT.fullmatch(base) or git(root, 'rev-parse', '--verify', base + '^{commit}') != base:
        raise AssetError('editorial history base must be an existing full commit SHA')
    added = git_paths(root, 'diff', '--name-only', '-z', '--no-renames', '--diff-filter=A',
                      base, '--', 'registrations/editions')
    for path in sorted(added):
        record = read_registration(root, path)
        if {'issuing', 'published_at', 'cancellation_reason'} & record.keys():
            raise AssetError(f'{path}: commit the pending authorization before claiming or cancelling it')
        if repo is not None:
            if 'staged_run_id' not in record:
                raise AssetError(f'{path}: new authorizations require the hosted proof’s staged_run_id')
            with tempfile.TemporaryDirectory(prefix='press-authorization-') as temp:
                directory = Path(temp) / 'edition'
                run_id = record['staged_run_id']
                download_reviewed(repo, run_id, record['provenance_sha256'], directory)
                tag = f'works/{path.parent.name}/{path.stem}'
                manifest = verify_edition(directory, tag=tag, commit=record['source_commit'])
                require_compatible_proof(root, tag, manifest)
                captured = manifest.get('work_identifiers', {}).get('concept_doi')
                work_path = Path('registrations/works') / (path.parent.name + '.json')
                if captured and (work_path not in tracked or
                                 read_registration(root, work_path)['identifiers'].get('concept_doi') != captured):
                    raise AssetError(f'{tag}: register the staged concept DOI on the work before reserving this edition')
                # read_registration already requires the exact canonical UTF-8 JSON encoding.
                if record != authorization(directory, manifest, staged_run_id=run_id):
                    raise AssetError(f'{path}: authorization differs from the verified staged proof')
    new_works = git_paths(root, 'diff', '--name-only', '-z', '--no-renames', '--diff-filter=A',
                          base, '--', 'registrations/works')
    for path in new_works:
        if 'concept_doi_history' in read_registration(root, path):
            raise AssetError(f'{path}: commit the initial concept DOI assignment before recording a change')
    # Treat renames as deletion/addition so moving a notice or an issued record cannot reset it.
    changed = git_paths(root, 'diff', '--name-only', '-z', '--no-renames', '--diff-filter=MDT',
                        base, '--', *scopes)
    for path in sorted(changed):
        if path.parts[0] == 'notices':
            raise AssetError(f'{path}: issued notices are append-only; add a new numbered notice')
        old, errors = strict_json(git(root, 'show', f'{base}:{path.as_posix()}', binary=True), path)
        if errors:
            raise AssetError('\n'.join(errors))
        if path.parts[:2] == ('registrations', 'works'):
            current = read_registration(root, path) if path in tracked else {}
            prior_doi = old.get('identifiers', {}).get('concept_doi')
            current_doi = current.get('identifiers', {}).get('concept_doi')
            prior_history = old.get('concept_doi_history', [])
            history = current.get('concept_doi_history', [])
            if history[:len(prior_history)] != prior_history:
                raise AssetError(f'{path}: concept DOI history is append-only')
            additions = history[len(prior_history):]
            if additions or ((prior_doi or prior_history) and current_doi != prior_doi):
                if len(additions) != 1 or additions[0]['from'] != prior_doi or additions[0]['to'] != current_doi:
                    raise AssetError(f'{path}: record one dated concept DOI change from the previous assignment')
            continue
        if path not in tracked:
            raise AssetError(f'{path}: a committed issuance intent cannot be removed')
        current = read_registration(root, path)
        tag = f'works/{old["work"]}/{old["version"]}'
        first_receipt = 'published_at' not in old and 'published_at' in current
        if 'issuing' not in old and 'issuing' in current:
            if 'cancellation_reason' in old:
                raise AssetError(f'{path}: a cancelled edition cannot be claimed')
            current.pop('issuing')
            if repo is not None and not first_receipt:
                require_compatible_proof(root, tag, old)
        if first_receipt:
            if old.get('issuing') is not True:
                raise AssetError(f'{path}: merge the issuing claim before recording publication')
            if tag in groups or 'identifiers' in current:
                raise AssetError(f'{path}: commit the publication receipt before adding notices or identifiers')
            timestamp = current.pop('published_at')
        if 'cancellation_reason' not in old and 'cancellation_reason' in current:
            if 'issuing' in old or 'published_at' in old or first_receipt:
                raise AssetError(f'{path}: a claimed or issued edition cannot be cancelled')
            if repo is not None and (tag_commit(repo, tag) is not None or
                                     any(r['tag_name'] == tag for r in release_records(repo))):
                raise AssetError(f'{path}: cancellation requires absence of the public tag and release')
            current.pop('cancellation_reason')
        old_ids = old.pop('identifiers', {})
        new_ids = current.pop('identifiers', {})
        changed_ids = {key: value for key, value in old_ids.items() if new_ids.get(key) != value}
        if changed_ids:
            notice_dir = Path('notices') / old['work'] / old['version']
            added_notices = git_paths(root, 'diff', '--name-only', '-z', '--diff-filter=A', base, '--', str(notice_dir))
            # Dates and notice syntax have already been validated above.
            corrections = {(root / item).read_text(encoding='utf-8').rstrip('\n').partition(' ')[2]
                           for item in added_notices}
            for key, value in changed_ids.items():
                if key in new_ids:
                    statement = f'Correction: Identifier {key} changed from {value} to {new_ids[key]}.'
                else:
                    statement = f'Correction: Identifier {key} retired: {value}.'
                if 'published_at' not in old or statement not in corrections:
                    raise AssetError(f'{path}: changing identifier {key} requires a new dated {statement}')
        if old != current:
            raise AssetError(f'{path}: the committed authorization and publication receipt are immutable')
        if first_receipt and repo is not None:
            if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
                raise AssetError('invalid repository for release receipt verification')
            release = api(repo, 'releases/tags/' + quote(tag, safe=''))
            if release.get('tag_name') != tag or release.get('published_at') != timestamp:
                raise AssetError(f'{path}: receipt must match the official public release and its timestamp')
            verify_public_release(repo, release, root=root, require_receipt=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', help='full previous commit SHA to compare with the tracked checkout')
    parser.add_argument('--repo', help='GitHub owner/repository for online verification of new receipts')
    args = parser.parse_args()
    try:
        check_editorial_history(Path(__file__).resolve().parents[1], args.base, repo=args.repo)
    except (OSError, ValueError) as exc:
        sys.exit(f'Editorial history check failed: {exc}')
