"""S3-compatible storage, with opt-in version inventory and Object Lock.

Baseline operation uses only object GET/HEAD/PUT/DELETE and ListObjectsV2.
No provider-specific login or bucket-management permission is required.
"""
from __future__ import annotations

import base64
from dataclasses import replace
import datetime
from email.utils import parsedate_to_datetime
import hashlib
import os
from pathlib import Path
import re
import shlex
import stat
import time
import xml.etree.ElementTree as ET

from .asset_policy import AssetError, MAX_BYTES, response_chunks, verify_bytes
from .object_store import (CACHE_CONTROL, MAX_OBJECTS, PREFIX, StoredObject,
                           check_response, immutable_version, same_version, valid_etag, valid_version)
from .s3 import S3Config, S3Error, object_query, read_headers, response

REQUIRED = {'S3_ENDPOINT', 'S3_REGION', 'S3_BUCKET', 'S3_ACCESS_KEY_ID', 'S3_SECRET_ACCESS_KEY'}
VARIABLES = REQUIRED | {'S3_SESSION_TOKEN', 'S3_ADDRESSING_STYLE', 'S3_VERSIONING',
                        'S3_OBJECT_LOCK', 'S3_CONDITIONAL_WRITES'}
XML_LIMIT = 4 * 1024 * 1024


def credentials(root, environ=None):
    """Read owner-only configuration as data, never as shell code."""
    env = dict(os.environ if environ is None else environ)
    path = Path(env.get('S3_ENV') or Path(root) / '.env.assets')
    if env.get('S3_ENV') or path.exists():
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd) as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > 16384):
                    raise AssetError('S3_ENV must be an owner-only regular file (mode 0600)')
                for line in stream.read().splitlines():
                    if not line.strip() or line.lstrip().startswith('#'):
                        continue
                    key, sep, value = line.removeprefix('export ').partition('=')
                    if not sep or key not in VARIABLES:
                        raise AssetError('S3_ENV accepts only documented S3 variable assignments')
                    parts = shlex.split(value, comments=True)
                    if len(parts) != 1:
                        raise AssetError('invalid S3_ENV assignment')
                    env[key] = parts[0]
        except (OSError, ValueError):
            raise AssetError('cannot read private S3_ENV configuration') from None
    if any(not env.get(name) for name in REQUIRED):
        raise AssetError('set S3_ENDPOINT, S3_REGION, S3_BUCKET, S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY locally')
    return {name: env[name] for name in VARIABLES if name in env}


def xml_document(raw, expected):
    try:
        # S3 responses are UTF-8 XML; reject DTDs rather than expanding entities.
        text = raw.decode('utf-8')
        if re.search(r'<!DOCTYPE|<!ENTITY', text, re.I):
            raise ValueError()
        root = ET.fromstring(text)
        for node in root.iter():
            node.tag = node.tag.rsplit('}', 1)[-1]
        # CompleteMultipartUpload can return HTTP 200 with an Error body.
        if root.tag != expected:
            raise ValueError()
        return root
    except (ValueError, ET.ParseError):
        raise AssetError('invalid or incomplete S3 XML response') from None


def timestamp(value, *, http=False):
    try:
        moment = parsedate_to_datetime(value) if http else datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if moment.tzinfo is None:
            raise ValueError()
        result = int(moment.timestamp() * 1000)
        if result <= 0:
            raise ValueError()
        return result
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise AssetError('invalid S3 object timestamp') from None


def iso_time(until):
    return datetime.datetime.fromtimestamp(until / 1000, datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def md5(raw):
    # Transport checksum only. Publication identity always uses SHA-256.
    return base64.b64encode(hashlib.md5(raw, usedforsecurity=False).digest()).decode()


class S3:
    def __init__(self, values, *, operation):
        if operation not in {'upload', 'pin', 'inspect', 'preview', 'delete'}:
            raise AssetError('unknown asset operation')
        self.config = S3Config.from_values(values)
        self.operation = operation
        self.versioning = self.config.versioning
        self.object_lock = self.config.object_lock

    def _allow(self, *operations):
        if self.operation not in operations:
            raise AssetError('storage operation is not permitted in this client mode')

    def _xml(self, expected, key='', *, method='GET', query=(), body=b'', extra=None):
        with response(self.config, key, method=method, query=query, body=body, extra=extra) as received:
            if received.status != 200:
                raise AssetError('unexpected S3 response status')
            raw = b''.join(response_chunks(received, XML_LIMIT))
            return xml_document(raw, expected), received.getheader('x-amz-version-id')

    def _retention(self, key, version):
        data, _ = self._xml('Retention', key, query=[*object_query(version), ('retention', '')])
        mode, until = data.findtext('Mode'), data.findtext('RetainUntilDate')
        if mode is None and until is None:
            return None, None
        if mode not in {'GOVERNANCE', 'COMPLIANCE'} or not until:
            raise AssetError('invalid Object Lock retention response')
        return mode.lower(), timestamp(until)

    def head(self, key, version_id=None, *, etag=None):
        try:
            with response(self.config, key, method='HEAD', query=object_query(version_id),
                          extra=read_headers(etag)) as received:
                if received.status != 200 or received.getheader('Content-Encoding', 'identity').lower() != 'identity':
                    raise AssetError('invalid S3 object metadata')
                actual_version = received.getheader('x-amz-version-id')
                actual_etag = received.getheader('ETag')
                length = received.getheader('Content-Length', '')
                if (not valid_etag(actual_etag) or not re.fullmatch(r'[0-9]+', length)
                        or (actual_version is not None and not valid_version(actual_version))
                        or (version_id is not None and not same_version(version_id, actual_version))
                        or (etag is not None and etag != actual_etag)):
                    raise AssetError('S3 object identity or byte count differs')
                item = StoredObject(key, actual_version, actual_etag, int(length),
                                    timestamp(received.getheader('Last-Modified'), http=True),
                                    received.getheader('Content-Type', '').partition(';')[0].strip().lower())
        except S3Error as exc:
            if exc.status == 404:
                return None
            raise
        if self.object_lock and immutable_version(item.version_id):
            mode, until = self._retention(key, item.version_id)
            held, _ = self._xml('LegalHold', key, query=[*object_query(item.version_id), ('legal-hold', '')])
            hold = held.findtext('Status')
            if hold not in {'ON', 'OFF'}:
                raise AssetError('invalid Object Lock legal hold response')
            item = replace(item, retention_mode=mode, retain_until=until, legal_hold=hold == 'ON')
        return item

    def current(self, key):
        if not self.versioning:
            return self.head(key)
        records = [item for item in self.inventory(key) if item.key == key and item.is_latest]
        if len(records) > 1:
            raise AssetError('ambiguous latest S3 object')
        return records[0] if records else None

    def read(self, stored, *, method='GET', range_value=None):
        self._allow('upload', 'pin', 'inspect')
        if method not in {'GET', 'HEAD'} or stored.delete_marker or not valid_etag(stored.etag):
            raise AssetError('invalid object read')
        return response(self.config, stored.key, method=method, query=object_query(stored.version_id),
                        extra=read_headers(stored.etag, range_value))

    def inventory(self, prefix=PREFIX):
        if not prefix.startswith(PREFIX):
            raise AssetError('inventory must stay within the asset namespace')
        records, identities, cursors = [], set(), set()
        cursor = []
        while True:
            query = ([('versions', '')] if self.versioning else [('list-type', '2')]) + [('prefix', prefix), ('max-keys', '1000')] + cursor
            page, _ = self._xml('ListVersionsResult' if self.versioning else 'ListBucketResult', query=query)
            if page.findtext('Name') != self.config.bucket or page.findtext('Prefix', '') != prefix:
                raise AssetError('S3 inventory scope differs')
            if page.findall('CommonPrefixes'):
                raise AssetError('grouped S3 inventory is incomplete')
            entries = [n for n in page if n.tag in {'Version', 'DeleteMarker', 'Contents'}]
            if len(entries) > 1000 or len(records) + len(entries) > MAX_OBJECTS:
                raise AssetError('S3 inventory exceeds its bounded limit')
            for node in entries:
                key = node.findtext('Key')
                version = node.findtext('VersionId') if self.versioning else None
                marker = node.tag == 'DeleteMarker'
                latest = node.findtext('IsLatest') if self.versioning else 'true'
                if (not isinstance(key, str) or not key.startswith(prefix) or latest not in {'true', 'false'}
                        or (self.versioning and not valid_version(version)) or (key, version) in identities):
                    raise AssetError('invalid or duplicate S3 inventory identity')
                identities.add((key, version))
                modified = timestamp(node.findtext('LastModified'))
                if marker:
                    if not self.versioning:
                        raise AssetError('unexpected delete marker in object inventory')
                    item = StoredObject(key, version, None, 0, modified, delete_marker=True, is_latest=latest == 'true')
                else:
                    etag, size = node.findtext('ETag'), node.findtext('Size', '')
                    if not valid_etag(etag) or not re.fullmatch(r'[0-9]+', size):
                        raise AssetError('invalid S3 inventory metadata')
                    item = self.head(key, version, etag=etag)
                    if (item is None or item.byte_count != int(size) or item.modified_ms // 1000 != modified // 1000):
                        raise AssetError('S3 inventory changed during enumeration')
                    if not self.versioning and immutable_version(item.version_id):
                        raise AssetError('enable S3_VERSIONING for complete versioned-bucket cleanup')
                    item = replace(item, modified_ms=modified, is_latest=latest == 'true')
                records.append(item)
            truncated = page.findtext('IsTruncated')
            if truncated == 'false':
                if self.versioning:
                    keys = {item.key for item in records}
                    latest = [item.key for item in records if item.is_latest]
                    if len(latest) != len(keys) or set(latest) != keys:
                        raise AssetError('ambiguous latest S3 version inventory')
                return sorted(records, key=lambda x: (x.key, not x.is_latest, -x.modified_ms, x.version_id or ''))
            if truncated != 'true' or not entries:
                raise AssetError('incomplete S3 inventory pagination')
            if self.versioning:
                name, version = page.findtext('NextKeyMarker'), page.findtext('NextVersionIdMarker')
                if not name or not name.startswith(prefix) or not valid_version(version):
                    raise AssetError('invalid S3 version pagination cursor')
                cursor = [('key-marker', name), ('version-id-marker', version)]
            else:
                token = page.findtext('NextContinuationToken')
                if not token or len(token) > 8192:
                    raise AssetError('invalid S3 object pagination cursor')
                cursor = [('continuation-token', token)]
            identity = tuple(cursor)
            if identity in cursors:
                raise AssetError('repeated S3 inventory cursor')
            cursors.add(identity)

    def _conditions(self, key, previous, create_only):
        if previous is not None and (create_only or previous.key != key):
            raise AssetError('invalid conditional object write')
        if previous is not None:
            current = self.head(key)
            if current is None or current.identity != previous.identity:
                raise AssetError('object changed before write')
        elif create_only and self.current(key) is not None:
            raise AssetError('refusing to overwrite an existing object')
        if not self.config.conditional_writes:
            return {}
        if previous is not None:
            return {'If-Match': previous.etag}
        return {'If-None-Match': '*'} if create_only else {}

    def _lock_headers(self, mode, until):
        if mode is None and until is None:
            return {}
        if (not self.object_lock or mode not in {'governance', 'compliance'}
                or type(until) is not int or until <= int(time.time() * 1000)):
            raise AssetError('explicit retention requires enabled Object Lock and a future retention time')
        return {'x-amz-object-lock-mode': mode.upper(), 'x-amz-object-lock-retain-until-date': iso_time(until)}

    def _receipt(self, key, version, etag, size, media_type, mode=None, until=None):
        if not valid_etag(etag) or (version is not None and not valid_version(version)):
            raise AssetError('invalid S3 upload receipt')
        item = self.head(key, version, etag=etag)
        if (item is None or not same_version(item.version_id, version) or item.byte_count != size
                or item.content_type != media_type):
            raise AssetError('S3 upload readback differs from supplied metadata')
        if mode and (item.retention_mode != mode or not item.retain_until or item.retain_until < until):
            raise AssetError('uploaded Object Lock retention readback differs')
        return item

    def upload(self, key, raw, media_type, *, cache_control=CACHE_CONTROL, previous=None,
               create_only=False, retention_mode=None, retain_until=None):
        self._allow('upload', 'pin')
        lock_headers = self._lock_headers(retention_mode, retain_until)
        extra = {'Content-Type': media_type, 'Cache-Control': cache_control, 'Content-MD5': md5(raw),
                 **self._conditions(key, previous, create_only), **lock_headers}
        with response(self.config, key, method='PUT', body=raw, extra=extra) as received:
            if received.status != 200:
                raise AssetError('unexpected S3 upload response')
            version, etag = received.getheader('x-amz-version-id'), received.getheader('ETag')
        return self._receipt(key, version, etag, len(raw), media_type, retention_mode, retain_until)

    def upload_file(self, key, path, media_type, *, part_size=32 * 1024 * 1024):
        self._allow('upload')
        if not 5 * 1024 * 1024 <= part_size <= 128 * 1024 * 1024:
            raise AssetError('multipart part size exceeds bounded limits')
        conditions = self._conditions(key, None, True)
        data, _ = self._xml('InitiateMultipartUploadResult', key, method='POST', query=[('uploads', '')],
                            extra={'Content-Type': media_type, 'Cache-Control': CACHE_CONTROL})
        upload_id = data.findtext('UploadId')
        if (data.findtext('Bucket') != self.config.bucket or data.findtext('Key') != key
                or not upload_id or len(upload_id) > 8192):
            raise AssetError('invalid multipart upload identity')
        parts, size = ET.Element('CompleteMultipartUpload'), 0
        try:
            with Path(path).open('rb') as stream:
                number = 0
                while raw := stream.read(part_size):
                    number += 1
                    size += len(raw)
                    if number > 10000 or size > 20 * 1024 ** 3:
                        raise AssetError('multipart upload exceeds its byte or part budget')
                    with response(self.config, key, method='PUT', query=[('uploadId', upload_id), ('partNumber', str(number))],
                                  body=raw, extra={'Content-MD5': md5(raw)}) as received:
                        etag = received.getheader('ETag')
                        if received.status != 200 or not valid_etag(etag):
                            raise AssetError('invalid multipart part receipt')
                    part = ET.SubElement(parts, 'Part')
                    ET.SubElement(part, 'PartNumber').text = str(number)
                    ET.SubElement(part, 'ETag').text = etag
            if not size:
                raise AssetError('empty multipart asset')
            body = ET.tostring(parts, encoding='utf-8')
            data, version = self._xml('CompleteMultipartUploadResult', key, method='POST', query=[('uploadId', upload_id)],
                                      body=body, extra={'Content-Type': 'application/xml', **conditions})
            if data.findtext('Bucket') != self.config.bucket or data.findtext('Key') != key:
                raise AssetError('multipart completion identity differs')
            return self._receipt(key, version, data.findtext('ETag'), size, media_type)
        except Exception:
            # Best effort; never conceal the original failure or log service bodies.
            try:
                with response(self.config, key, method='DELETE', query=[('uploadId', upload_id)]):
                    pass
            except (AssetError, OSError):
                pass
            raise

    def retain(self, stored, mode, until):
        self._allow('pin')
        self._lock_headers(mode, until)
        if not immutable_version(stored.version_id):
            raise AssetError('Object Lock requires an immutable object version')
        current = self.head(stored.key, stored.version_id, etag=stored.etag)
        if current is None or (current.retention_mode and current.retention_mode != mode):
            raise AssetError('retention mode differs; never weaken existing retention')
        if current.retain_until and current.retain_until >= until:
            return current
        root = ET.Element('Retention', xmlns='http://s3.amazonaws.com/doc/2006-03-01/')
        ET.SubElement(root, 'Mode').text = mode.upper()
        ET.SubElement(root, 'RetainUntilDate').text = iso_time(until)
        body = ET.tostring(root, encoding='utf-8')
        with response(self.config, stored.key, method='PUT', query=[*object_query(stored.version_id), ('retention', '')],
                      body=body, extra={'Content-Type': 'application/xml', 'Content-MD5': md5(body)}) as received:
            if received.status != 200:
                raise AssetError('unexpected retention response')
        current = self.head(stored.key, stored.version_id, etag=stored.etag)
        if current is None or current.retention_mode != mode or not current.retain_until or current.retain_until < until:
            raise AssetError('Object Lock retention readback differs')
        return current

    def verify(self, key, raw, media_type):
        item = self.current(key)
        if item is None or item.delete_marker:
            raise AssetError('no visible object for storage readback')
        with self.read(item) as received:
            check_response(received, item)
            content = b''.join(response_chunks(received, MAX_BYTES))
            verify_bytes(content, key.rsplit('.', 1)[1], received.getheader('Content-Type', ''))
            if content != raw or item.content_type != media_type:
                raise AssetError('storage readback differs from original bytes')

    def delete(self, stored):
        self._allow('delete')
        if not stored.delete_marker:
            current = self.head(stored.key, stored.version_id, etag=stored.etag)
            if (current is None or current.identity != stored.identity or current.byte_count != stored.byte_count
                    or current.modified_ms // 1000 != stored.modified_ms // 1000):
                raise AssetError('object changed before deletion')
            if current.legal_hold or (current.retain_until or 0) > int(time.time() * 1000):
                raise AssetError('object is protected by retention or legal hold')
        # Never bypass governance or create a delete marker in a versioned bucket.
        # For unversioned stores there is no atomic portable compare-and-delete:
        # the maintenance operator MUST pause all writers until GC completes.
        if self.versioning and stored.version_id is None:
            raise AssetError('versioned cleanup requires an explicit version identity')
        with response(self.config, stored.key, method='DELETE', query=object_query(stored.version_id)) as received:
            if received.status != 204:
                raise AssetError('unexpected S3 deletion response')
