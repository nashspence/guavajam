"""Read-only qualification of staged assets, using the same public byte validators.

The only endpoint receiving credentials comes from operator-owned S3 config.
Source URLs select content keys, never a credential destination. Pins describe
storage integrity, not permission to disclose bytes.
"""
from contextlib import contextmanager
import time
from urllib.parse import quote

from .asset_policy import AssetError, fetch_asset
from .gateway_index import Index
from .object_store import check_response, immutable_version, same_version
from .verify_media_sources import verify_items, verify_one
from .verify_source_assets import verify_source_assets


class _Response:
    def __init__(self, response, mapping):
        self.response, self.mapping, self.status = response, mapping, response.status

    def getheader(self, name, default=None):
        if name.lower() == 'x-press-retain-until':
            return str(self.mapping['retain_until'] or 0)
        if name.lower() == 'x-press-asset-version':
            version = self.mapping['version_id']
            return quote(version, safe='') if immutable_version(version) else default
        if name.lower() == 'accept-ranges' and self.mapping['extension'] in {'mp3', 'mp4'}:
            return 'bytes'  # Actual range requests are checked by VerifiedRangeFile.
        if name.lower() == 'content-disposition' and self.mapping['extension'] == 'bin':
            return 'attachment'
        if name.lower() == 'x-content-type-options':
            return 'nosniff'
        return self.response.getheader(name, default)

    def read(self, *args):
        return self.response.read(*args)

    def read1(self, *args):
        return self.response.read1(*args)


class PrivateAssets:
    def __init__(self, store, policy, *, require_retention=False):
        self.store, self.policy = store, policy
        self.index = Index(store)
        self.require_retention = require_retention
        self.mappings = {}

    def observe(self, url):
        digest, extension = self.policy.parse(url)
        key = 'sha256/' + digest + '.' + extension
        mapping = self.index.get(key)
        if mapping is None:
            raise AssetError('draft dependency has no private object pin')
        if key in self.mappings and self.mappings[key] != mapping:
            raise AssetError('private pin changed during qualification')
        info = self.store.head(key, mapping['version_id'], etag=mapping['etag'])
        if (info is None or info.delete_marker or info.key != key
                or not same_version(info.version_id, mapping['version_id'])
                or any(getattr(info, field) != mapping[field] for field in ('etag', 'byte_count', 'content_type'))):
            raise AssetError('private object readback differs from its pin')
        until = mapping['retain_until']
        if until and (not info.retain_until or info.retain_until < until
                      or info.retention_mode != mapping['retention_mode']):
            raise AssetError('private retention readback differs from its pin')
        if self.require_retention and (not until or until <= int(time.time() * 1000)):
            raise AssetError('required current retention is unavailable')
        self.mappings[key] = mapping
        return info, mapping

    @contextmanager
    def response(self, url, accept, *, method='GET', timeout=None, range_value=None):
        info, mapping = self.observe(url)
        length, status = info.byte_count, 200
        if range_value is not None:
            start, end = map(int, range_value.removeprefix('bytes=').split('-'))
            if not 0 <= start <= end < length:
                raise AssetError('invalid qualification range')
            length, status = end - start + 1, 206
        with self.store.read(info, method=method, range_value=range_value) as response:
            check_response(response, info, status=status, length=length)
            if range_value and response.getheader('Content-Range') != f'bytes {start}-{end}/{info.byte_count}':
                raise AssetError('private media range differs')
            yield _Response(response, mapping)

    def verify(self, root, paths):
        verify_source_assets(
            root, paths=paths, require_retention=self.require_retention,
            fetcher=lambda policy, url: fetch_asset(policy, url, response_factory=self.response),
            media_verifier=lambda items, origin: verify_items(
                items, origin, verifier=lambda url, host, **kwargs: verify_one(
                    url, host, response_factory=self.response, **kwargs)),
            response_factory=self.response)
        # Reject pin/retention movement between the first and final read.
        for key in list(self.mappings):
            self.observe(self.policy.origin + '/' + key)
        return dict(self.mappings)
