"""Hash and pin stored objects, using versions and retention only when available."""
from contextlib import contextmanager
import hashlib
import io
import json
import re
import time

from .repository import repository_root
from .asset_policy import AssetError, MIME, response_chunks
from .gateway_index import Index
from .object_store import ObjectStore, check_response, immutable_version, same_version
from .verify_media_sources import MIME as MEDIA_MIME


def pin_key(store: ObjectStore, index: Index, key: str, *, version=None, retain_until=None, mode=None):
    if not re.fullmatch(r'sha256/[0-9a-f]{64}\.(png|jpg|jpeg|webp|svg|bin|mp3|mp4|vtt)', key):
        raise AssetError('invalid asset key')
    now = int(time.time() * 1000)
    if retain_until is not None or mode is not None:
        if type(retain_until) is not int or retain_until <= now or mode not in {'governance', 'compliance'}:
            raise AssetError('supply an explicit future RETAIN_UNTIL and RETENTION_MODE')
        if not store.object_lock:
            raise AssetError('requested retention requires S3_OBJECT_LOCK=on; it will not be silently omitted')
    previous = index.get(key)
    if previous:
        if version is not None and not same_version(version, previous['version_id']):
            raise AssetError('asset URL already has a different pinned object')
        info = store.head(key, previous['version_id'], etag=previous['etag'])
    elif version is not None:
        info = store.head(key, version)
    else:
        info = store.current(key)
    if info is None or info.delete_marker:
        raise AssetError('asset has no visible uploaded object to pin')
    if previous and (not same_version(info.version_id, previous['version_id']) or info.etag != previous['etag']):
        raise AssetError('asset URL already has a different pinned object')
    if retain_until is not None and not immutable_version(info.version_id):
        raise AssetError('Object Lock requires an immutable object version')
    digest, extension = key.removeprefix('sha256/').split('.')
    mime = {**MIME, **MEDIA_MIME, 'bin': 'application/octet-stream'}[extension]
    maximum = 20 * 1024 ** 3 if extension in {'mp3', 'mp4'} else 10 * 1024 * 1024
    if (info.key != key or not 0 <= info.byte_count <= maximum
            or (info.byte_count == 0 and extension != 'bin') or info.content_type != mime):
        raise AssetError('stored object is outside its asset identity, MIME or byte budget')
    saved = io.BytesIO() if extension not in {'mp3', 'mp4'} else None
    with store.read(info) as received:
        check_response(received, info)
        total, hasher = 0, hashlib.sha256()
        for chunk in response_chunks(received, maximum):
            total += len(chunk)
            hasher.update(chunk)
            if saved is not None:
                saved.write(chunk)
        if total != info.byte_count or hasher.hexdigest() != digest:
            raise AssetError('stored object SHA-256 differs from its asset URL')
    from .media_formats import inspect
    if saved is None:
        from .media_ranges import VerifiedRangeFile
        @contextmanager
        def private_range(_url, _mime, *, timeout, range_value):
            start, end = map(int, range_value.removeprefix('bytes=').split('-'))
            with store.read(info, range_value=range_value) as received:
                check_response(received, info, status=206, length=end-start+1)
                yield received
        with VerifiedRangeFile(key, mime, total, info.etag, private_range) as probe:
            inspect(probe, extension)
    elif extension != 'bin':
        inspect(saved, extension)
    else:
        from .asset_policy import verify_bytes
        verify_bytes(saved.getvalue(), 'bin', mime)
    if previous and previous['retain_until']:
        if (not info.retain_until or info.retain_until < previous['retain_until']
                or info.retention_mode != previous['retention_mode']):
            raise AssetError('stored retention is shorter than the pinned commitment')
    if retain_until is not None:
        info = store.retain(info, mode, retain_until)
        if info.retention_mode != mode or not info.retain_until or info.retain_until < retain_until:
            raise AssetError('Object Lock retention readback differs')
    until = info.retain_until
    # An already-expired backend lock does not create a new retention promise.
    if until is not None and until <= now and previous is None:
        until = None
    value = {'schema': 'press.asset.v1', 'sha256': digest, 'extension': extension,
             'version_id': info.version_id, 'etag': info.etag, 'byte_count': total,
             'content_type': mime, 'retain_until': until,
             'retention_mode': info.retention_mode if until else None}
    index.put(key, value)
    return value


def main():
    import argparse
    from .asset_storage import S3, credentials
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('key')
    parser.add_argument('--retain-until', type=int)
    parser.add_argument('--retention-mode', choices=['governance', 'compliance'])
    args = parser.parse_args()
    try:
        store = S3(credentials(repository_root()), operation='pin')
        result = pin_key(store, Index(store), args.key, retain_until=args.retain_until, mode=args.retention_mode)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (AssetError, OSError, ValueError) as exc:
        print('Asset pinning failed: ' + str(exc), file=__import__('sys').stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
