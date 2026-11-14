"""Private, provider-neutral sidecars bind digest URLs to verified object identities."""
import json
import re

from .asset_policy import AssetError, response_chunks
from .object_store import PIN_PREFIX, ObjectStore, check_response, immutable_version, valid_etag, valid_version

FIELDS = {'schema', 'sha256', 'extension', 'version_id', 'etag', 'byte_count',
          'content_type', 'retain_until', 'retention_mode'}


def validate(value, key):
    match = re.fullmatch(r'sha256/([0-9a-f]{64})\.(png|jpg|jpeg|webp|svg|bin|mp3|mp4|vtt)', key)
    if not isinstance(value, dict) or not match or set(value) != FIELDS:
        raise AssetError('invalid asset mapping fields')
    if value['schema'] != 'press.asset.v1' or value['sha256'] != match[1] or value['extension'] != match[2]:
        raise AssetError('asset mapping identity differs')
    version = value['version_id']
    if (version is not None and not valid_version(version)) or not valid_etag(value['etag']):
        raise AssetError('invalid pinned object identity')
    limit = 20 * 1024 ** 3 if match[2] in {'mp3', 'mp4'} else 10 * 1024 * 1024
    if type(value['byte_count']) is not int or not 0 <= value['byte_count'] <= limit or (value['byte_count'] == 0 and match[2] != 'bin'):
        raise AssetError('invalid asset byte count')
    until, mode = value['retain_until'], value['retention_mode']
    if until is None:
        if mode is not None:
            raise AssetError('retention mode requires a retention time')
    elif (type(until) is not int or not 0 < until <= 9007199254740991
          or mode not in {'governance', 'compliance'} or not immutable_version(version)):
        raise AssetError('invalid asset retention commitment')
    from .verify_media_sources import MIME as MEDIA_MIME
    from .asset_policy import MIME
    if value['content_type'] != {**MIME, **MEDIA_MIME, 'bin': 'application/octet-stream'}[match[2]]:
        raise AssetError('asset MIME differs from extension')


class Index:
    def __init__(self, store: ObjectStore):
        self.store = store

    @staticmethod
    def path(key):
        return PIN_PREFIX + key.removeprefix('sha256/') + '.json'

    def _load(self, key):
        item = self.store.head(self.path(key))
        if item is None:
            return None, None
        if item.content_type != 'application/json' or not 0 < item.byte_count <= 8192:
            raise AssetError('invalid private asset mapping metadata')
        with self.store.read(item) as received:
            check_response(received, item)
            raw = b''.join(response_chunks(received, 8192))
        if len(raw) != item.byte_count:
            raise AssetError('truncated private asset mapping')
        try:
            value = json.loads(raw)
        except (ValueError, TypeError):
            raise AssetError('invalid private asset mapping') from None
        validate(value, key)
        if value['retain_until'] and (not item.retain_until or item.retain_until < value['retain_until']
                                      or item.retention_mode != value['retention_mode']):
            raise AssetError('private mapping retention differs from its pinned commitment')
        return value, item

    def get(self, key):
        return self._load(key)[0]

    def put(self, key, value):
        validate(value, key)
        previous, observed = self._load(key)
        if previous:
            if any(previous[k] != value[k] for k in ('version_id', 'etag', 'sha256', 'extension', 'byte_count', 'content_type')):
                raise AssetError('digest URL is pinned to another object; investigate instead of replacing it')
            if previous['retain_until'] and (not value['retain_until'] or value['retain_until'] < previous['retain_until']
                                            or value['retention_mode'] != previous['retention_mode']):
                raise AssetError('never weaken an existing retention commitment')
        if previous == value:
            return
        raw = (json.dumps(value, sort_keys=True) + '\n').encode()
        # Apply the sidecar's lock atomically with the PUT, not after publication
        # of a mapping that claims retention. Readback is mandatory in the store.
        self.store.upload(self.path(key), raw, 'application/json', cache_control='no-store',
                          previous=observed, create_only=observed is None,
                          retention_mode=value['retention_mode'], retain_until=value['retain_until'])
        if self.get(key) != value:
            raise AssetError('asset mapping readback differs')
