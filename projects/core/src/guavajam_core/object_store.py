"""Provider-independent asset storage records and preservation invariants."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import ContextManager, Protocol, Any

from .asset_policy import AssetError

PREFIX = 'sha256/'
LEASE_PREFIX = PREFIX + '.leases/'
PIN_PREFIX = PREFIX + '.pins/'
CACHE_CONTROL = 'public, max-age=31536000, immutable, no-transform'
GRACE_DAYS = 30
MAX_OBJECTS = 100_000


def valid_version(value):
    """S3 version IDs are opaque UTF-8 strings, not B2 file IDs."""
    try:
        return (isinstance(value, str) and 0 < len(value.encode('utf-8')) <= 1024
                and not re.search(r'[\x00-\x1f\x7f]', value))
    except UnicodeError:
        return False


def valid_etag(value):
    # A strong HTTP validator; deliberately NOT interpreted as a content hash.
    return isinstance(value, str) and re.fullmatch(r'"[\x21\x23-\x7e]{1,1024}"', value) is not None


def immutable_version(value):
    # S3's null version can be replaced, including after versioning is suspended.
    return value is not None and value != 'null'


def same_version(left, right):
    return (None if left == 'null' else left) == (None if right == 'null' else right)


@dataclass(frozen=True)
class StoredObject:
    key: str
    version_id: str | None
    etag: str | None
    byte_count: int
    modified_ms: int
    content_type: str = ''
    delete_marker: bool = False
    is_latest: bool = True
    retention_mode: str | None = None
    retain_until: int | None = None
    legal_hold: bool = False

    @property
    def identity(self):
        return self.key, self.version_id, self.etag


def check_response(received, stored, *, status=200, length=None):
    """Bind reads to the observed object, even on servers ignoring If-Match."""
    expected = stored.byte_count if length is None else length
    if (received.status != status or received.getheader('ETag') != stored.etag
            or not same_version(received.getheader('x-amz-version-id'), stored.version_id)
            or received.getheader('Content-Length') != str(expected)
            or received.getheader('Content-Type', '').partition(';')[0].strip().lower() != stored.content_type
            or received.getheader('Content-Encoding', 'identity').lower() != 'identity'):
        raise AssetError('storage readback differs from the pinned object')


class ObjectStore(Protocol):
    """Core consumes these operations, never a provider's response dictionaries.

    Mutating operators are serialized externally (including during collection).
    Version IDs and storage-enforced retention are independent optional features.
    """
    versioning: bool
    object_lock: bool

    def head(self, key: str, version_id: str | None = None, *, etag: str | None = None) -> StoredObject | None: ...
    def current(self, key: str) -> StoredObject | None: ...
    def read(self, stored: StoredObject, *, method: str = 'GET', range_value: str | None = None) -> ContextManager[Any]: ...
    def inventory(self, prefix: str = PREFIX) -> list[StoredObject]: ...
    def upload(self, key: str, raw: bytes, media_type: str, *, cache_control: str = CACHE_CONTROL,
               previous: StoredObject | None = None, create_only: bool = False,
               retention_mode: str | None = None, retain_until: int | None = None) -> StoredObject: ...
    def upload_file(self, key: str, path: Any, media_type: str) -> StoredObject: ...
    def retain(self, stored: StoredObject, mode: str, until: int) -> StoredObject: ...
    def verify(self, key: str, raw: bytes, media_type: str) -> None: ...
    def delete(self, stored: StoredObject) -> None: ...
