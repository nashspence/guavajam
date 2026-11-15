"""Release verified assets only for an externally trusted edition on remote main.

Private pins do not authorize delivery. Public eligibility and object identity
are monotonic; retention may only extend. Records are written by a separately privileged
operator, never a PR job or the optional view. Missing records deny delivery.
Multiple object writes are intentionally not a distributed transaction: retry
reconciles exact records and public delivery without signing another edition.
"""
from pathlib import Path
import argparse
import json
import re
import sys
import subprocess

from .asset_policy import AssetError, AssetPolicy, response_chunks
from .asset_storage import S3, credentials
from .gateway_index import validate as validate_pin
from .object_store import RELEASE_PREFIX, check_response
from .publication_protocol import ancestor, oid, resolve, run, verify_shape
from .publication_commit import verify
from .prepare_publication import prepare


class Releases:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def path(key):
        if not re.fullmatch(r'sha256/[0-9a-f]{64}\.(png|jpg|jpeg|webp|svg|bin|mp3|mp4|vtt)', key):
            raise AssetError('invalid released asset key')
        return RELEASE_PREFIX + key.removeprefix('sha256/') + '.json'

    def _load(self, key):
        item = self.store.head(self.path(key))
        if item is None:
            return None, None
        if item.content_type != 'application/json' or not 0 < item.byte_count <= 8192:
            raise AssetError('invalid release record metadata')
        with self.store.read(item) as received:
            check_response(received, item)
            raw = b''.join(response_chunks(received, 8192))
        from .publication_protocol import _json
        value = _json(raw)
        if (not isinstance(value, dict) or set(value) != {'schema', 'publication', 'origin', 'asset'}
                or value['schema'] != 'press.asset-release.v1'):
            raise AssetError('invalid release record')
        oid(value['publication'])
        AssetPolicy(value['origin'])
        validate_pin(value['asset'], key)
        pin = value['asset']
        if pin['retain_until'] and (not item.retain_until or item.retain_until < pin['retain_until']
                                  or item.retention_mode != pin['retention_mode']):
            raise AssetError('release record retention differs')
        return value, item

    def get(self, key):
        return self._load(key)[0]

    @staticmethod
    def _compatible(key, mapping, origin, previous):
        """Already released bytes may be reused, never reassigned to a new version."""
        AssetPolicy(origin)
        validate_pin(mapping, key)
        if previous:
            if previous['origin'] != origin:
                raise AssetError('release origin differs from the existing delivery authority')
            old = previous['asset']
            immutable = ('version_id', 'etag', 'sha256', 'extension', 'byte_count', 'content_type')
            if any(old[field] != mapping[field] for field in immutable):
                raise AssetError('released URL already identifies another object')
            if old['retain_until'] and (not mapping['retain_until'] or mapping['retain_until'] < old['retain_until']
                                        or mapping['retention_mode'] != old['retention_mode']):
                raise AssetError('cannot weaken a released retention commitment')
        return previous

    def check(self, key, mapping, origin):
        return self._compatible(key, mapping, origin, self.get(key))

    def put(self, key, mapping, publication, origin):
        oid(publication)
        previous, observed = self._load(key)
        self._compatible(key, mapping, origin, previous)
        if previous and previous['asset'] == mapping:
            return False
        # Only a retention extension may update an existing record. Preserve
        # its original issuing authority and exact byte/version identity.
        authority = previous['publication'] if previous else publication
        value = {'schema': 'press.asset-release.v1', 'publication': authority, 'origin': origin, 'asset': mapping}
        raw = (json.dumps(value, sort_keys=True) + '\n').encode()
        self.store.upload(self.path(key), raw, 'application/json', cache_control='no-store',
                          previous=observed, create_only=observed is None,
                          retention_mode=mapping['retention_mode'], retain_until=mapping['retain_until'])
        if self.get(key) != value:
            raise AssetError('release record readback differs')
        return True


def remote_main(repo, expected, remote='origin'):
    """Fetch only the operator-configured remote main; never trust a PR's HEAD."""
    oid(expected)
    if not isinstance(remote, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', remote):
        raise AssetError('use an operator-configured remote name')
    ref = 'refs/guavajam/asset-release/main'
    run(repo, '-c', 'protocol.ext.allow=never', '-c', 'fetch.fsckObjects=true',
        'fetch', '--no-tags', '--no-write-fetch-head', '--no-recurse-submodules',
        remote, 'refs/heads/main:' + ref, limit=65536)
    tip = resolve(repo, ref)
    if tip != expected:
        raise AssetError('remote main differs from the reviewed release tip; retry against its exact tip')
    return tip


def release(repo, revision, expected_main, *, store, fingerprints=(), allowed_signers=None,
            remote='origin', apply=False, require_retention=False):
    repo = Path(repo).resolve(strict=True)
    oid(revision)
    tip = remote_main(repo, expected_main, remote)
    ancestor(repo, revision, tip)
    trust = {'fingerprints': fingerprints, 'allowed_signers': allowed_signers}
    # A signed ordinary commit, changed-tree declaration, or branch-only edition
    # cannot qualify. Source programs, hooks and filters never run.
    verify(repo, revision, online_media=False, **trust)
    declared = verify_shape(repo, revision, 'publication')
    work = declared['manuscript'].split('/')[1]
    prepared = prepare(repo, revision, work, published=declared['published'],
                       label=declared.get('label'), store=store, require_retention=require_retention)
    releases = Releases(store)
    origin = prepared['asset_origin']
    mappings = {item['key']: {k: v for k, v in item.items() if k != 'key'} for item in prepared['assets']}
    for key, mapping in mappings.items():
        releases.check(key, mapping, origin)
    result = {'schema': 'press.asset-release-result.v1', 'revision': revision, 'main_tip': tip,
              'assets': len(mappings), 'applied': False, 'public_delivery_verified': False}
    if not apply:
        return result
    # A concurrent main advance rejects this execution before the first public write.
    remote_main(repo, expected_main, remote)
    for key, mapping in mappings.items():
        releases.put(key, mapping, revision, origin)
    # Failures here are partial release, not rollback. Issued mappings are kept;
    # an identical invocation rechecks authority/bytes and retries delivery.
    verify(repo, revision, online_media=True, require_retention=require_retention, **trust)
    return {**result, 'applied': True, 'public_delivery_verified': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path('.'))
    parser.add_argument('--revision', required=True, help='exact signed publication commit')
    parser.add_argument('--expected-main', required=True, help='exact reviewed remote main tip')
    parser.add_argument('--remote', default='origin')
    parser.add_argument('--apply', action='store_true', help='authorize public disclosure; default verifies without writes')
    parser.add_argument('--require-retention', action='store_true')
    trust = parser.add_mutually_exclusive_group(required=True)
    trust.add_argument('--trusted-gpg-fingerprint', action='append', default=[])
    trust.add_argument('--ssh-allowed-signers', type=Path)
    args = parser.parse_args(argv)
    try:
        store = S3(credentials(args.repo), operation='release' if args.apply else 'inspect')
        from .manage_assets import operator_lock
        with operator_lock(args.repo):
            result = release(args.repo, args.revision, args.expected_main, store=store,
                             fingerprints=args.trusted_gpg_fingerprint, allowed_signers=args.ssh_allowed_signers,
                             remote=args.remote, apply=args.apply, require_retention=args.require_retention)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        # Do not print source text, object IDs, provider bodies or credentials.
        print('Asset release failed; already released assets remain public. Reconcile and retry the same revision.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
