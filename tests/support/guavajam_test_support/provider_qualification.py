"""Opt-in, real S3 qualification; publishes synthetic media and removes only owned keys.

Run with make test-provider PROVIDER_ARGS='--help'. Credentials stay outside receipts.
An interrupted run can be completed with --cleanup-only and the same --run-dir.
"""
import argparse
from datetime import date
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import uuid
from unittest.mock import patch

from PIL import Image
from PIL.PngImagePlugin import PngInfo
from guavajam_core.asset_policy import AssetError, AssetPolicy, public_response, response_chunks
from guavajam_core.asset_storage import S3, credentials
from guavajam_core.asset_release import Releases, release
from guavajam_core.asset_versions import pin_key
from guavajam_core.gateway_index import Index
from guavajam_core.ingest_asset import upload_media
from guavajam_core.manage_assets import upload, candidates
from guavajam_core.object_store import PREFIX, LEASE_PREFIX
from guavajam_core.s3 import S3Error, response
from .discovery_fixture import Publisher
from .media_fixture import audio, video

SCHEMA = 'guavajam.provider-qualification.v1'
NAMES = ['image.png', 'recording.mp3', 'recording.mp4', 'captions.vtt']


def require(condition):
    if not condition:
        raise AssetError('provider qualification assertion failed')


def paths(keys):
    require(len(keys) == 4 and len(set(keys)) == 4)
    require(all(re.fullmatch(r'sha256/[0-9a-f]{64}\.(png|mp3|mp4|vtt)', k) for k in keys))
    require({k.rsplit('.', 1)[1] for k in keys} == {'png', 'mp3', 'mp4', 'vtt'})
    return [p for k in keys for p in (k, LEASE_PREFIX + k[len(PREFIX):], Index.path(k), Releases.path(k))]


def save(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.new')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump(value, out, sort_keys=True); out.write('\n'); out.flush(); os.fsync(out.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def exact(store, owned):
    return [row for key in owned for row in store.inventory(key) if row.key == key]


def claim(store, keys, journal, binding):
    owned = paths(keys)
    require(not exact(store, owned))  # No writes, including leases, before this succeeds.
    value = {'schema': SCHEMA, 'binding': binding, 'keys': keys, 'claimed': True}
    save(journal, value)
    return value


def cleanup(store, value, binding, *, timeout=600, clock=time.time, sleep=time.sleep):
    require(value.get('schema') == SCHEMA and value.get('claimed') is True and value.get('binding') == binding)
    owned = paths(value['keys'])
    deadline = clock() + timeout
    deleted = 0
    while rows := exact(store, owned):
        require(not any(row.legal_hold for row in rows))
        until = max((row.retain_until or 0) for row in rows) / 1000
        if until > clock():
            require(until < deadline)
            sleep(min(15, until - clock() + 1))
            continue
        require(clock() < deadline)
        for row in rows:
            store.delete(row)  # Explicit versions, no retention bypass; exact keys only.
            deleted += 1
    return deleted


def fixtures(root):
    nonce = uuid.uuid4().hex
    metadata = PngInfo(); metadata.add_text('qualification', nonce)
    Image.new('RGB', (17, 13), (40, 90, 120)).save(root / NAMES[0], pnginfo=metadata)
    audio(root / NAMES[1], comment=nonce)
    video(root / NAMES[2])
    with (root / NAMES[2]).open('ab') as out:
        size = 40 * 1024 * 1024
        out.write((size + 8).to_bytes(4, 'big') + b'free' + nonce.encode() + bytes(size - len(nonce)))
    (root / NAMES[3]).write_text('WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nSynthetic qualification ' + nonce + '.\n')
    return ['sha256/' + hashlib.sha256((root / name).read_bytes()).hexdigest() + Path(name).suffix for name in NAMES]


def denied(config, key, *, method='PUT', query=None):
    try:
        with response(config, key, method=method, query=query or [],
                      **({'body': b'forbidden', 'extra': {'Content-Type': 'application/json'}} if method == 'PUT' else {})):
            pass
    except S3Error as error:
        require(error.status == 403)
    else:
        raise AssetError('provider role boundary failed')


class LostWrite:
    """Lose the successful response after the provider has committed one grant."""
    def __init__(self, store): self.store = store
    def __getattr__(self, name): return getattr(self.store, name)
    def upload(self, *args, **kwargs):
        self.store.upload(*args, **kwargs)
        raise AssetError('synthetic lost successful response')


def exercise(root, roles, origin, keys, *, retention_seconds=0, strict_maintenance=True):
    staged = S3(roles['staging'], operation='upload')
    reader = S3(roles['readonly'], operation='inspect')
    releaser = S3(roles['release'], operation='release')
    maintenance = S3(roles['maintenance'], operation='delete')
    lock = retention_seconds > 0
    (root / 'asset-policy.json').write_text(json.dumps({'origin': origin}))
    for name, key in zip(NAMES, keys):
        file = root / name
        result = upload(root, file, staged, index=Index(staged)) if file.suffix == '.png' else upload_media(root, file, staged, Index(staged))
        require(result[1] == origin + '/' + key)
    if lock:
        until = int((time.time() + retention_seconds) * 1000)
        pins = S3(roles['staging'], operation='pin')
        for key in keys: pin_key(pins, Index(pins), key, retain_until=until, mode='governance')
    for key in keys:
        for method, range_value in [('HEAD', None), ('GET', None), ('GET', 'bytes=0-127')]:
            with public_response(origin + '/' + key, '*/*', method=method, range_value=range_value) as received:
                require(received.status == 404)
    require(Index(reader).get(keys[2])['etag'].endswith('-2"'))
    denied(staged.config, Releases.path(keys[0]))
    denied(releaser.config, keys[0])
    lease = LEASE_PREFIX + keys[0][len(PREFIX):]
    denied(reader.config, lease)
    if strict_maintenance: denied(maintenance.config, lease)
    if strict_maintenance:
        item = reader.head(lease)
        denied(staged.config, lease, method='DELETE', query=[('versionId', item.version_id)] if item.version_id else [])
    unknown = S3({**roles['readonly'], 'S3_ACCESS_KEY_ID': 'unconfigured-qualification-key', 'S3_SECRET_ACCESS_KEY': 'unconfigured-qualification-secret'}, operation='inspect').config
    denied(unknown, keys[0], method='HEAD')
    # Raw unsigned S3 request cannot read an exact known content URL.
    # Construct the same URL but omit all authentication headers.
    import urllib.request
    import urllib.error
    from urllib.parse import quote
    endpoint = roles['readonly']['S3_ENDPOINT'].rstrip('/')
    bucket = roles['readonly']['S3_BUCKET']
    if roles['readonly'].get('S3_ADDRESSING_STYLE', 'path') == 'virtual':
        from urllib.parse import urlsplit, urlunsplit
        url = urlsplit(endpoint); anonymous = urlunsplit((url.scheme, bucket + '.' + url.netloc, '/' + keys[0], '', ''))
    else: anonymous = endpoint + '/' + quote(bucket, safe='') + '/' + keys[0]
    try: urllib.request.urlopen(urllib.request.Request(anonymous, method='HEAD'), timeout=10)
    except urllib.error.HTTPError as error: require(error.code in {401, 403})
    else: raise AssetError('anonymous provider read allowed')
    fixture = root / 'publisher'; fixture.mkdir()
    p = Publisher(fixture)
    try:
        remote = root / 'origin.git'
        subprocess.run(['git', 'init', '--bare', '-q', str(remote)], check=True, capture_output=True)
        p.git('remote', 'add', 'origin', str(remote)); p.git('push', '-q', 'origin', 'main')
        base = p.git('rev-parse', 'HEAD'); p.git('switch', '-c', 'review')
        (p.repo / 'asset-policy.json').write_text(json.dumps({'origin': origin}))
        body = '![Synthetic image](' + origin + '/' + keys[0] + ')\n\n'
        body += '\n\n'.join('[Synthetic media](' + origin + '/' + k + ')' for k in keys[1:])
        p.source('field-notes', 'Field Notes', body); p.git('add', '-A'); p.git('commit', '-qm', 'Review synthetic media')
        edition = p.publish(date.today().isoformat()); p.git('push', '-q', 'origin', 'review')
        kwargs = dict(store=reader, release_store=releaser, fingerprints=[p.fingerprint], require_retention=lock)
        with patch.dict(os.environ, {'GNUPGHOME': str(p.home)}):
            try: release(p.repo, edition, base, apply=True, **kwargs)
            except ValueError: pass
            else: raise AssetError('unmerged edition could release')
            require(all(Releases(releaser).get(k) is None for k in keys))
            p.git('switch', 'main'); p.git('merge', '--no-ff', 'review', '-m', 'Accept synthetic edition')
            p.git('push', '-q', 'origin', 'main'); tip = p.git('rev-parse', 'HEAD')
            release(p.repo, edition, tip, apply=False, **kwargs)
            require(all(Releases(releaser).get(k) is None for k in keys))
            try: release(p.repo, edition, tip, apply=True, **{**kwargs, 'release_store': LostWrite(releaser)})
            except AssetError: pass
            else: raise AssetError('lost response was not observed')
            grants = [Releases(releaser).get(k) is not None for k in keys]
            require(sum(grants) == 1)
            for key, granted in zip(keys, grants):
                with public_response(origin + '/' + key, '*/*', method='HEAD') as received:
                    require(received.status == (200 if granted else 404))
            require(release(p.repo, edition, tip, apply=True, **kwargs)['public_delivery_verified'])
            before = {row.identity for row in exact(maintenance, paths(keys))}
            require(release(p.repo, edition, tip, apply=True, **kwargs)['public_delivery_verified'])
            require(before == {row.identity for row in exact(maintenance, paths(keys))})
            require(p.git('rev-parse', 'HEAD') == tip)
        for name, key in zip(NAMES, keys):
            raw = (root / name).read_bytes(); mapping = Index(reader).get(key)
            for method in ['HEAD', 'GET']:
                with public_response(origin + '/' + key, '*/*', method=method) as received:
                    require(received.status == 200 and received.getheader('ETag') == mapping['etag'])
                    require(received.getheader('X-Press-Asset-Publication') == edition)
                    require(received.getheader('Content-Length') == str(len(raw)))
                    if method == 'GET': require(b''.join(response_chunks(received, len(raw))) == raw)
            if name.endswith(('.mp3', '.mp4')):
                for value, expected in [('bytes=0-127', raw[:128]), ('bytes=-128', raw[-128:])]:
                    with public_response(origin + '/' + key, '*/*', range_value=value) as received:
                        require(received.status == 206 and b''.join(response_chunks(received, 128)) == expected)
                with public_response(origin + '/' + key, '*/*', range_value='bytes=999999999999-') as received:
                    require(received.status == 416)
        # No bucket-wide inventory: removal of every Git reference still cannot collect these releases.
        require(not candidates(exact(maintenance, paths(keys)), set(), time.time() + 100 * 86400))
        return {'private_staging': True, 'signed_main_authority': True, 'partial_release_recovery': True,
                'idempotent_retry': True, 'multipart_and_ranges': True, 'released_collection_protection': True,
                'role_boundaries': True, 'strict_writer_roles': strict_maintenance, 'retention': lock}
    finally:
        subprocess.run(['gpgconf', '--homedir', str(p.home), '--kill', 'gpg-agent'], capture_output=True)


def binding_for(roles, origin):
    # Bind recovery to the selected provider/bucket/origin, not key generations.
    return hashlib.sha256(json.dumps([roles['readonly'][k] for k in ('S3_ENDPOINT', 'S3_REGION', 'S3_BUCKET')] + [origin]).encode()).hexdigest()


def run(root, roles, origin, *, retention_seconds=0, strict_maintenance=True, cleanup_only=False):
    AssetPolicy(origin)
    require(0 <= retention_seconds <= 300 and (not retention_seconds or retention_seconds >= 120))
    common = ['S3_ENDPOINT', 'S3_REGION', 'S3_BUCKET', 'S3_ADDRESSING_STYLE', 'S3_VERSIONING', 'S3_OBJECT_LOCK', 'S3_CONDITIONAL_WRITES']
    require(all(all(config.get(k) == roles['readonly'].get(k) for k in common) for config in roles.values()))
    require(len({config['S3_ACCESS_KEY_ID'] for config in roles.values()}) == 4)
    require((roles['readonly'].get('S3_OBJECT_LOCK') == 'on') == bool(retention_seconds))
    info = root.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
    fd = os.open(root / 'lease', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal = root / 'owned.json'; receipt = root / 'receipt.json'
        binding = binding_for(roles, origin); maintenance = S3(roles['maintenance'], operation='delete')
        result = {'schema': SCHEMA, 'passed': False, 'phase': 'preflight', 'cleanup_verified': False}
        value = None
        try:
            if cleanup_only:
                info = journal.lstat()
                require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size < 8192)
                value = json.loads(journal.read_text())
            else:
                require(not journal.exists() and not any((root / name).exists() for name in NAMES))
                keys = fixtures(root); value = claim(maintenance, keys, journal, binding)
                result['phase'] = 'exercise'; save(receipt, result)
                result['checks'] = exercise(root, roles, origin, keys, retention_seconds=retention_seconds, strict_maintenance=strict_maintenance)
                result['exercise_passed'] = True
        finally:
            if value is not None:
                result['phase'] = 'cleanup'; save(receipt, result)
                result['deleted_versions'] = cleanup(maintenance, value, binding)
                for key in value['keys']:
                    with public_response(origin + '/' + key, '*/*', method='HEAD') as received: require(received.status == 404)
                result['cleanup_verified'] = True
            result['passed'] = result.get('exercise_passed', False) and result['cleanup_verified']
            result['phase'] = 'complete' if result['cleanup_verified'] else 'preflight'
            save(receipt, result)
        return result
    finally: os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Explicitly authorize temporary public synthetic assets and scoped deletion')
    parser.add_argument('--roles-dir', type=Path, required=True, help='Owner-only staging.env, readonly.env, release.env, maintenance.env')
    parser.add_argument('--origin', required=True, help='Existing release-gated gateway origin')
    parser.add_argument('--run-dir', type=Path, required=True, help='New mode-0700 durable directory; keep for receipts and interrupted cleanup')
    parser.add_argument('--retention-seconds', type=int, default=0, help='120–300 for an Object Lock provider; cleanup waits without bypass')
    parser.add_argument('--cleanup-only', action='store_true')
    args = parser.parse_args(argv)
    if not args.apply: parser.error('--apply is required; this test publishes synthetic media')
    try:
        roles = {name: credentials(Path.cwd(), {'S3_ENV': str(args.roles_dir / (name + '.env'))})
                 for name in ['staging', 'readonly', 'release', 'maintenance']}
        if not args.cleanup_only: args.run_dir.mkdir(mode=0o700)
        result = run(args.run_dir.absolute(), roles, args.origin, retention_seconds=args.retention_seconds, cleanup_only=args.cleanup_only)
        print(json.dumps(result, sort_keys=True))
        return 0 if result['cleanup_verified'] and (args.cleanup_only or result['passed']) else 1
    except Exception:
        # Provider errors/child output can contain endpoints or secret material.
        print(json.dumps({'schema': SCHEMA, 'passed': False, 'phase': 'failed', 'inspect_private_receipt': True}))
        return 1


if __name__ == '__main__': raise SystemExit(main())
