"""Validate tracked editorial notices and preserve the history of issued editions."""
import argparse
from pathlib import Path
import re
import sys
from urllib.parse import quote

from asset_policy import AssetError
from build_edition import COMMIT, git
from editorial_notices import edition_notices
from identifier_schema import TAG, strict_json, validate_registrations
from issuance import read_registration
from publish_edition import api


def git_paths(root, *args):
    raw = git(root, *args, binary=True)
    return {Path(item.decode('utf-8')) for item in raw.split(b'\0') if item}


def check_editorial_history(root: Path, base: str | None = None, *, repo: str | None = None) -> None:
    root = root.resolve()
    if (root / 'registrations').is_symlink():
        raise AssetError('registrations: expected a directory without symlinks')
    scopes = ('notices', 'registrations/editions')
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
        if 'published_at' in read_registration(root, path):
            raise AssetError(f'{path}: commit the pending authorization before its publication receipt')
    # Treat renames as deletion/addition so moving a notice or an issued record cannot reset it.
    changed = git_paths(root, 'diff', '--name-only', '-z', '--no-renames', '--diff-filter=MDT',
                        base, '--', *scopes)
    for path in sorted(changed):
        if path.parts[0] == 'notices':
            raise AssetError(f'{path}: issued notices are append-only; add a new numbered notice')
        old, errors = strict_json(git(root, 'show', f'{base}:{path.as_posix()}', binary=True), path)
        if errors:
            raise AssetError('\n'.join(errors))
        if path not in tracked:
            if 'published_at' in old:
                raise AssetError(f'{path}: a receipted edition registration cannot be removed')
            continue
        current = read_registration(root, path)
        if 'published_at' not in old and 'published_at' not in current:
            continue
        # Identifiers may arrive from registries after publication.
        for record in (old, current):
            record.pop('identifiers', None)
        first_receipt = 'published_at' not in old
        if first_receipt:
            timestamp = current.pop('published_at')
        if old != current:
            raise AssetError(f'{path}: the issued authorization and publication receipt are immutable')
        if first_receipt and repo is not None:
            if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
                raise AssetError('invalid repository for release receipt verification')
            tag = f'works/{current["work"]}/{current["version"]}'
            release = api(repo, 'releases/tags/' + quote(tag, safe=''))
            if (release.get('tag_name') != tag or release.get('draft') is not False
                    or release.get('prerelease') is not False or release.get('published_at') != timestamp):
                raise AssetError(f'{path}: receipt must match the official public release and its published_at')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', help='full previous commit SHA to compare with the tracked checkout')
    parser.add_argument('--repo', help='GitHub owner/repository for online verification of new receipts')
    args = parser.parse_args()
    try:
        check_editorial_history(Path(__file__).resolve().parents[1], args.base, repo=args.repo)
    except (OSError, ValueError) as exc:
        sys.exit(f'Editorial history check failed: {exc}')
