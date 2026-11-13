#!/usr/bin/env python3
"""Export the exact GuavaJam catalog from signed publication/site conventions.

This reads Git data, never tags or a mutable checkout. Date/DOI/status changes
belong in the publisher's reviewed press-site.json, authorized by Press-Site: 1.
It does not sign, push, register DOIs, fetch media or deploy a site.
"""
import argparse
import os
from pathlib import Path
import sys
import tempfile

from publication_protocol import discover
from site_catalog import encoded, read_catalog


def export(repo, output, *, ref='refs/heads/main', fingerprints=(), allowed_signers=None, previous=None):
    output = Path(output).absolute()
    if output.is_symlink():
        raise ValueError('catalog destination may not be a symlink')
    old = read_catalog(previous) if previous else (read_catalog(output) if output.exists() else None)
    catalog, evidence = discover(repo, ref=ref, fingerprints=fingerprints, allowed_signers=allowed_signers, previous=old)
    raw = encoded(catalog)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and output.read_bytes() == raw:
        return evidence, False
    fd, name = tempfile.mkstemp(prefix='.catalog-', dir=output.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, output)
    finally:
        Path(name).unlink(missing_ok=True)
    return evidence, True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--ref', default='refs/heads/main')
    parser.add_argument('--output', type=Path, default=Path('.build/guavajam/CATALOG.json'))
    parser.add_argument('--previous-catalog', type=Path)
    trust = parser.add_mutually_exclusive_group(required=True)
    trust.add_argument('--trusted-gpg-fingerprint', action='append', default=[])
    trust.add_argument('--ssh-allowed-signers', type=Path)
    args = parser.parse_args(argv)
    try:
        evidence, changed = export(args.repo, args.output, ref=args.ref, fingerprints=args.trusted_gpg_fingerprint,
                                   allowed_signers=args.ssh_allowed_signers, previous=args.previous_catalog)
    except (ValueError, OSError) as exc:
        print('Site export failed: ' + str(exc), file=sys.stderr); return 1
    print(('Wrote' if changed else 'Unchanged'), args.output, 'for', len(evidence['publications']),
          'publication(s) at', evidence['tip'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
