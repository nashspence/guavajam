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
from .publication_protocol import discover, oid, resolve, run
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
                or value['schema'] != 'guavajam.asset-release.v1'):
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
        value = {'schema': 'guavajam.asset-release.v1', 'publication': authority, 'origin': origin, 'asset': mapping}
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


def release(repo, revision, expected_main, *, store, release_store, fingerprints=(), allowed_signers=None,
            remote='origin', apply=False, require_retention=False):
    repo = Path(repo).resolve(strict=True)
    oid(revision)
    tip = remote_main(repo, expected_main, remote)
    trust = {'fingerprints': fingerprints, 'allowed_signers': allowed_signers}
    # Only a content-changing commit on accepted first-parent main history
    # qualifies. Source programs, hooks and filters never run.
    catalog, _ = discover(repo, ref=tip, **trust)
    if revision not in {edition['revision'] for edition in catalog['editions']}:
        raise AssetError('release revision is not an edition on accepted first-parent main history')
    prepared = prepare(repo, revision, store=store, require_retention=require_retention)
    releases = Releases(release_store)
    origin = prepared['asset_origin']
    mappings = {item['key']: {k: v for k, v in item.items() if k != 'key'} for item in prepared['assets']}
    for key, mapping in mappings.items():
        releases.check(key, mapping, origin)
    result = {'schema': 'guavajam.asset-release-result.v1', 'revision': revision, 'main_tip': tip,
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


def release_stores(repo, release_env, *, apply=False):
    """Separate private qualification reads from release-prefix reads/writes.

    A provider with a single prefix per key need not give the release writer
    access to private bytes. Explicit files prevent inherited credential mixing.
    """
    store = S3(credentials(repo), operation='inspect')
    release_store = S3(credentials(repo, {'S3_ENV': str(release_env)}),
                       operation='release' if apply else 'inspect')
    left, right = store.config, release_store.config
    if (any(getattr(left, field) != getattr(right, field) for field in
            ('endpoint', 'region', 'bucket', 'addressing_style', 'versioning', 'object_lock', 'conditional_writes'))
            or left.access_key == right.access_key):
        raise AssetError('release requires a distinct credential for the same configured storage')
    return store, release_store


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path('.'))
    parser.add_argument('--revision', required=True, help='exact signed publication commit')
    parser.add_argument('--expected-main', required=True, help='exact reviewed remote main tip')
    parser.add_argument('--remote', default='origin')
    parser.add_argument('--release-env', type=Path, required=True,
                        help='owner-only S3 configuration for the separate release-prefix authority')
    parser.add_argument('--apply', action='store_true', help='authorize public disclosure; default verifies without writes')
    parser.add_argument('--require-retention', action='store_true')
    trust = parser.add_mutually_exclusive_group(required=True)
    trust.add_argument('--trusted-gpg-fingerprint', action='append', default=[])
    trust.add_argument('--ssh-allowed-signers', type=Path)
    args = parser.parse_args(argv)
    try:
        store, release_store = release_stores(args.repo, args.release_env, apply=args.apply)
        from .manage_assets import operator_lock
        with operator_lock(args.repo):
            result = release(args.repo, args.revision, args.expected_main, store=store, release_store=release_store,
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
