"""Small SigV4 transport shared by S3-compatible asset operations.

Only the operator's explicit HTTPS endpoint receives credentials. There is no
provider discovery, redirect following, implicit credential chain or bucket setup.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import datetime
import hashlib
import hmac
import ipaddress
import re
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit

from .asset_policy import AssetError
from .object_store import valid_etag, valid_version


@dataclass(frozen=True)
class S3Config:
    endpoint: str
    region: str
    bucket: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    session_token: str = field(default='', repr=False)
    addressing_style: str = 'path'
    versioning: bool = False
    object_lock: bool = False
    conditional_writes: bool = False

    def __post_init__(self):
        try:
            if re.search(r'[\s\\%]', self.endpoint):
                raise ValueError()
            uri = urlsplit(self.endpoint)
            host = uri.hostname
            port = uri.port
            if (uri.scheme != 'https' or not host or uri.username is not None or uri.password is not None
                    or uri.path not in {'', '/'} or uri.query or uri.fragment
                    or re.search(r'[\s\\%]', uri.netloc) or uri.netloc.endswith(':')
                    or (port is not None and not 1 <= port <= 65535)):
                raise ValueError()
            if ':' not in host and not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host):
                raise ValueError()
            if ':' in host:
                ipaddress.IPv6Address(host)
            if self.addressing_style not in {'path', 'virtual'}:
                raise ValueError()
            if self.addressing_style == 'virtual':
                try:
                    ipaddress.ip_address(host)
                except ValueError:
                    pass
                else:
                    raise ValueError()
            if (not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', self.bucket)
                    or '..' in self.bucket or not re.fullmatch(r'[a-z0-9-]+', self.region)):
                raise ValueError()
            if not self.access_key or not self.secret_key or any(
                    re.search(r'[\x00-\x20\x7f]', value)
                    for value in (self.access_key, self.secret_key, self.session_token)):
                raise ValueError()
            if self.object_lock and not self.versioning:
                raise ValueError()
        except (TypeError, ValueError):
            raise AssetError('invalid S3 configuration; use an explicit HTTPS endpoint, region, bucket and credentials; Object Lock requires versioning') from None

    @classmethod
    def from_values(cls, values):
        def flag(name):
            value = values.get(name, 'off')
            if value not in {'on', 'off'}:
                raise AssetError(name + ' must be on or off')
            return value == 'on'
        try:
            return cls(values['S3_ENDPOINT'], values['S3_REGION'], values['S3_BUCKET'],
                       values['S3_ACCESS_KEY_ID'], values['S3_SECRET_ACCESS_KEY'],
                       values.get('S3_SESSION_TOKEN', ''), values.get('S3_ADDRESSING_STYLE', 'path'),
                       flag('S3_VERSIONING'), flag('S3_OBJECT_LOCK'), flag('S3_CONDITIONAL_WRITES'))
        except KeyError:
            raise AssetError('missing required S3 configuration') from None

    def values(self):
        """Explicit deployment bindings; callers must never log this result."""
        return {'S3_ENDPOINT': self.endpoint, 'S3_REGION': self.region, 'S3_BUCKET': self.bucket,
                'S3_ACCESS_KEY_ID': self.access_key, 'S3_SECRET_ACCESS_KEY': self.secret_key,
                'S3_SESSION_TOKEN': self.session_token, 'S3_ADDRESSING_STYLE': self.addressing_style,
                'S3_VERSIONING': 'on' if self.versioning else 'off',
                'S3_OBJECT_LOCK': 'on' if self.object_lock else 'off',
                'S3_CONDITIONAL_WRITES': 'on' if self.conditional_writes else 'off'}


def headers(config, key='', *, method='GET', query=(), body=b'', extra=None, now=None):
    """Return an exactly signed URL and headers (query values are always encoded)."""
    if key and (not key.startswith('sha256/') or '..' in key.split('/')
                or re.search(r'[\x00-\x1f\x7f]', key)):
        raise AssetError('S3 key must be inside the asset namespace')
    if method not in {'GET', 'HEAD', 'PUT', 'POST', 'DELETE'}:
        raise AssetError('unsupported S3 method')
    uri = urlsplit(config.endpoint)
    host = uri.netloc.lower()
    path = '/' + quote(key, safe='/~')
    if config.addressing_style == 'path':
        path = '/' + quote(config.bucket, safe='') + path
    else:
        host = config.bucket + '.' + host
    query_string = '&'.join(k + '=' + v for k, v in sorted(
        (quote(str(k), safe='~'), quote(str(v), safe='~')) for k, v in query))
    instant = now or datetime.datetime.now(datetime.timezone.utc)
    stamp = instant.astimezone(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    day = stamp[:8]
    digest = hashlib.sha256(body).hexdigest()
    supplied = {name.lower(): value for name, value in (extra or {}).items()}
    if any(name in supplied for name in ('host', 'authorization', 'x-amz-date', 'x-amz-content-sha256', 'x-amz-security-token')):
        raise AssetError('S3 authentication headers cannot be overridden')
    supplied.update({'host': host, 'x-amz-date': stamp, 'x-amz-content-sha256': digest})
    if config.session_token:
        supplied['x-amz-security-token'] = config.session_token
    if method in {'PUT', 'POST'}:
        supplied['content-length'] = str(len(body))
    if any(not re.fullmatch(r'[a-z0-9-]+', name) or not isinstance(value, str)
           or re.search(r'[\r\n\x00]', value) for name, value in supplied.items()):
        raise AssetError('invalid S3 request headers')
    canonical_headers = ''.join(name + ':' + ' '.join(value.split()) + '\n' for name, value in sorted(supplied.items()))
    signed = ';'.join(sorted(supplied))
    canonical = '\n'.join((method, path, query_string, canonical_headers, signed, digest))
    scope = day + '/' + config.region + '/s3/aws4_request'
    message = 'AWS4-HMAC-SHA256\n' + stamp + '\n' + scope + '\n' + hashlib.sha256(canonical.encode()).hexdigest()
    signing = ('AWS4' + config.secret_key).encode()
    for part in (day, config.region, 's3', 'aws4_request'):
        signing = hmac.new(signing, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(signing, message.encode(), hashlib.sha256).hexdigest()
    supplied['authorization'] = ('AWS4-HMAC-SHA256 Credential=' + config.access_key + '/' + scope
                                 + ', SignedHeaders=' + signed + ', Signature=' + signature)
    supplied['accept-encoding'] = 'identity'
    return 'https://' + host + path + ('?' + query_string if query_string else ''), supplied


class S3Error(AssetError):
    def __init__(self, status):
        self.status = status
        super().__init__('S3 operation failed (HTTP ' + str(status) + '); response details omitted')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def response(config, key='', *, method='GET', query=(), body=b'', extra=None):
    url, authorization = headers(config, key, method=method, query=query, body=body, extra=extra)
    try:
        return urllib.request.build_opener(NoRedirect()).open(
            urllib.request.Request(url, data=body if method in {'PUT', 'POST'} else None,
                                   method=method, headers=authorization), timeout=45)
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        raise S3Error(status) from None
    except (OSError, ValueError):
        raise AssetError('S3 connection failed; request details omitted') from None


def object_query(version_id):
    if version_id is None:
        return []
    if not valid_version(version_id):
        raise AssetError('invalid S3 version identity')
    return [('versionId', version_id)]


def read_headers(etag=None, range_value=None):
    result = {}
    if etag is not None:
        if not valid_etag(etag):
            raise AssetError('a strong object ETag is required')
        result['If-Match'] = etag
    if range_value is not None:
        if not re.fullmatch(r'bytes=(?:[0-9]+-[0-9]*|-[1-9][0-9]*)', range_value):
            raise AssetError('invalid object byte range')
        result['Range'] = range_value
    return result
