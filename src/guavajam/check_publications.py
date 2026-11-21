#!/usr/bin/env python3
"""Audit accepted content changes using repository-external publisher trust."""
from .repository import repository_root
from pathlib import Path
import argparse
import os
import subprocess
import sys
import tempfile

from .publication_commit import verify, PublicationError
from .publication_protocol import edition_history, history


def incoming(root, base, *, all_history=False):
    """Never infer publications from signatures/messages on side branches."""
    tip, revisions = history(root, 'refs/heads/main')
    _, candidates = edition_history(root, tip)
    if base and base != '0' * 40 and not all_history:
        if base not in revisions:
            raise PublicationError('base must remain on the accepted first-parent history')
        accepted = set(revisions[revisions.index(base) + 1:])
        candidates = [(sha, tree) for sha, tree in candidates if sha in accepted]
    return [sha for sha, _ in candidates]


def check(root, base=None, *, online=True, all_history=False, require_retention=False):
    if require_retention and not online:
        raise PublicationError('retention verification requires online checks')
    candidates = incoming(root, base, all_history=all_history)
    if not candidates:
        return []
    fingerprints = os.environ.get('GUAVAJAM_PUBLISHER_GPG_FINGERPRINTS', '').split()
    signers = os.environ.get('GUAVAJAM_PUBLISHER_SSH_SIGNERS', '')
    if bool(fingerprints) == bool(signers):
        raise PublicationError('content changes require exactly one externally configured publisher signer policy')
    with tempfile.TemporaryDirectory(prefix='guavajam-trust-') as directory:
        path = Path(directory) / 'allowed-signers'
        if signers:
            path.write_text(signers + '\n')
            path.chmod(0o600)
        old_home = os.environ.get('GNUPGHOME')
        try:
            if fingerprints:
                key = os.environ.get('GUAVAJAM_PUBLISHER_GPG_PUBLIC_KEYS', '')
                if not key:
                    raise PublicationError('trusted GPG public keys must be independently configured')
                home = Path(directory) / 'gpg'
                home.mkdir(mode=0o700)
                os.environ['GNUPGHOME'] = str(home)
                imported = subprocess.run(['gpg', '--batch', '--import'], input=key.encode(),
                                          capture_output=True, timeout=30, check=False)
                if imported.returncode:
                    raise PublicationError('independent GPG policy import failed')
            return [verify(root, sha, fingerprints=fingerprints,
                           allowed_signers=path if signers else None,
                           online_media=online, require_retention=require_retention)
                    for sha in candidates]
        finally:
            if old_home is None:
                os.environ.pop('GNUPGHOME', None)
            else:
                os.environ['GNUPGHOME'] = old_home


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default=os.environ.get('GUAVAJAM_BASE_SHA', ''))
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--all', action='store_true', dest='all_history')
    parser.add_argument('--require-retention', action='store_true')
    args = parser.parse_args()
    try:
        results = check(repository_root(), args.base, online=not args.offline,
                        all_history=args.all_history, require_retention=args.require_retention)
    except (PublicationError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print('Publication policy failed: ' + str(exc), file=sys.stderr)
        return 1
    print('Verified incoming editions:', len(results))
    return 0


if __name__ == '__main__':
    sys.exit(main())
