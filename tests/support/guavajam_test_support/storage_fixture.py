"""Provider-neutral in-memory object store for preservation contracts."""
import contextlib
from dataclasses import replace
import hashlib
import io
import time

from guavajam_core.asset_policy import AssetError
from guavajam_core.object_store import StoredObject, immutable_version, same_version

RAW = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><circle cx="5" cy="5" r="4"/></svg>\n'
KEY = 'sha256/' + hashlib.sha256(RAW).hexdigest() + '.svg'
VALUES = {'S3_ENDPOINT': 'https://objects.example.test:9443', 'S3_REGION': 'test-region',
          'S3_BUCKET': 'test-bucket', 'S3_ACCESS_KEY_ID': 'fixture-id',
          'S3_SECRET_ACCESS_KEY': 'fixture-private-value'}


class Response(io.BytesIO):
    def __init__(self, raw=b'', *, status=200, headers=None):
        super().__init__(raw)
        self.status = status
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}

    def getheader(self, name, default=None):
        return self.headers.get(name.lower(), default)


class MemoryStore:
    def __init__(self, records=(), *, versioning=True, object_lock=False, seed=False):
        self.versioning, self.object_lock = versioning, object_lock
        self.records, self.bytes, self.uploads, self.deleted = list(records), {}, [], []
        self.reads, self.retentions = [], []
        self.counter = 0
        if seed:
            self.upload(KEY, RAW, 'image/svg+xml')

    def inventory(self, prefix='sha256/'):
        return sorted((r for r in self.records if r.key.startswith(prefix)),
                      key=lambda r: (r.key, not r.is_latest, -r.modified_ms, r.version_id or ''))

    def current(self, key):
        return next((r for r in self.inventory(key) if r.key == key and r.is_latest), None)

    def head(self, key, version_id=None, *, etag=None):
        item = (next((r for r in self.records if r.key == key and same_version(r.version_id, version_id)), None)
                if version_id is not None else self.current(key))
        if item is None or item.delete_marker:
            return None
        if etag is not None and etag != item.etag:
            raise AssetError('object changed: ETag condition failed')
        return item

    def upload(self, key, raw, mime, *, previous=None, create_only=False, retention_mode=None, retain_until=None, **kwargs):
        current = self.current(key)
        if create_only and current is not None:
            raise AssetError('refusing to overwrite an existing object')
        if previous is not None and (current is None or current.identity != previous.identity):
            raise AssetError('object changed before write')
        if retain_until is not None and not self.object_lock:
            raise AssetError('Object Lock unavailable')
        version = 'version_' + str(self.counter) if self.versioning else None
        self.counter += 1
        if self.versioning:
            self.records = [replace(r, is_latest=False) if r.key == key else r for r in self.records]
        else:
            self.records = [r for r in self.records if r.key != key]
        row = StoredObject(key, version, '"' + hashlib.md5(raw, usedforsecurity=False).hexdigest() + '"',
                           len(raw), int(time.time() * 1000), mime,
                           retention_mode=retention_mode, retain_until=retain_until)
        self.records.append(row)
        self.bytes[(key, version)] = raw
        self.uploads.append((key, raw, mime, kwargs))
        return row

    @contextlib.contextmanager
    def read(self, stored, *, method='GET', range_value=None):
        row = self.head(stored.key, stored.version_id, etag=stored.etag)
        if row is None:
            raise AssetError('object missing')
        self.reads.append((stored, method, range_value))
        raw = self.bytes[(row.key, row.version_id)]
        headers = {'Content-Type': row.content_type, 'ETag': row.etag}
        if row.version_id is not None:
            headers['x-amz-version-id'] = row.version_id
        status = 200
        if range_value:
            start, end = map(int, range_value.removeprefix('bytes=').split('-'))
            headers['Content-Range'] = f'bytes {start}-{end}/{len(raw)}'
            raw, status = raw[start:end + 1], 206
        headers['Content-Length'] = str(len(raw))
        with Response(raw if method == 'GET' else b'', status=status, headers=headers) as response:
            yield response

    def retain(self, stored, mode, until):
        if not self.object_lock or not immutable_version(stored.version_id):
            raise AssetError('Object Lock unavailable')
        current = self.head(stored.key, stored.version_id, etag=stored.etag)
        if current.retention_mode and current.retention_mode != mode:
            raise AssetError('never weaken existing retention')
        result = replace(current, retention_mode=mode, retain_until=max(current.retain_until or 0, until))
        self.records[self.records.index(current)] = result
        self.retentions.append((stored, mode, until))
        return result

    def verify(self, key, raw, mime):
        row = self.current(key)
        if row is None or row.delete_marker or self.bytes.get((key, row.version_id)) != raw or row.content_type != mime:
            raise AssetError('origin differs')

    def delete(self, item):
        self.deleted.append(item)
        self.records = [r for r in self.records if r.identity != item.identity]
        self.bytes.pop((item.key, item.version_id), None)
