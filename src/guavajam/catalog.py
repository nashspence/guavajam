"""Derived edition history for one work, with independently retained continuity.

Every edition field is read from Git; this file has no administrative overrides
and conveys no signer authority. Discovery must verify the underlying objects.
"""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit

SCHEMA = 'guavajam.catalog.v1'
MAX_BYTES = 16 * 1024 * 1024
MAX_EDITIONS = 10000
REVISION = re.compile(r'[0-9a-f]{40}\Z')


class CatalogError(ValueError):
    pass


def fail(message):
    raise CatalogError('catalog: ' + message)


def text(value, label, maximum=512):
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value.encode('utf-8')) > maximum or not value.isprintable()
            or unicodedata.normalize('NFC', value) != value
            or any(unicodedata.category(c) == 'Cf' for c in value)):
        fail(label + ' must be bounded, single-line NFC text')
    return value


def fields(value, required, optional, label):
    if not isinstance(value, dict) or required - value.keys() or value.keys() - required - optional:
        fail(label + ' has missing or unknown fields')


def https(value, label):
    text(value, label, 2048)
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or parsed.port not in {None, 443} or '\\' in value
                or re.search(r'[\s<>"{}]', value) or not value.isascii()):
            fail(label + ' requires an absolute HTTPS URL without credentials/query/fragment')
        if parsed.hostname.lower() != parsed.hostname or not re.fullmatch(r'[a-z0-9.-]+', parsed.hostname):
            fail(label + ' requires a canonical ASCII host')
    except ValueError as exc:
        raise CatalogError('catalog: invalid ' + label) from exc
    return value


def day(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        fail(label + ' requires YYYY-MM-DD')
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise CatalogError('catalog: invalid ' + label) from exc
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail('duplicate JSON field: ' + key)
        result[key] = value
    return result


def read_catalog(path):
    """Read bounded JSON; reject duplicate keys rather than accepting last-wins."""
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        fail('file exceeds 16 MiB')
    try:
        result = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique,
                            parse_constant=lambda _: fail('non-finite JSON number'))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise CatalogError('catalog: invalid UTF-8 JSON') from exc
    validate(result)
    return result


def validate(catalog):
    fields(catalog, {'schema', 'hash_algorithm', 'tip', 'editions'}, set(), 'catalog')
    if catalog['schema'] != SCHEMA or catalog['hash_algorithm'] != 'sha1':
        fail('unsupported catalog schema or Git object format')
    if not isinstance(catalog['tip'], str) or not REVISION.fullmatch(catalog['tip']):
        fail('tip requires an exact commit identity')
    if not isinstance(catalog['editions'], list) or len(catalog['editions']) > MAX_EDITIONS:
        fail('editions must be a bounded list')
    revisions = set()
    for edition in catalog['editions']:
        fields(edition, {'revision', 'tree', 'published', 'message'}, set(), 'edition')
        for field in ('revision', 'tree'):
            if not isinstance(edition[field], str) or not REVISION.fullmatch(edition[field]):
                fail(field + ' requires an exact Git object identity')
        if edition['revision'] in revisions:
            fail('edition revisions must be unique')
        revisions.add(edition['revision'])
        day(edition['published'], 'published')
        if not isinstance(edition['message'], str) or len(edition['message'].encode('utf-8')) > 65536:
            fail('message must be bounded UTF-8 text')
    if len(encoded(catalog)) > MAX_BYTES:
        fail('catalog exceeds 16 MiB')
    return catalog


def compare(previous, current):
    """Retain the exact ordered edition prefix; Git verifies the tip's ancestry."""
    validate(previous)
    validate(current)
    old = previous['editions']
    if current['editions'][:len(old)] != old:
        fail('accepted editions cannot disappear, change or be reordered')
    return current


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
