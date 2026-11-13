"""Reviewed catalog data, not a publication registry or a source of signer trust.

The caller selects a local repository and external signing policy. The catalog
selects exact publications and supplies administrative DOI/date/status facts.
No URL in this file is fetched and no source-controlled program is executed.
"""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import quote, urlsplit

from .identifiers import DOI, SLUG

SCHEMA = 'guavajam.catalog.v1'
MAX_BYTES = 1024 * 1024
MAX_EDITIONS = 256
REVISION = re.compile(r'[0-9a-f]{40}\Z')
STATUSES = frozenset({'available', 'superseded', 'withdrawn', 'unavailable'})
READABLE_STATUSES = frozenset({'available', 'superseded', 'withdrawn'})


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


def doi(value):
    if not isinstance(value, str) or len(value) > 512 or not DOI.fullmatch(value):
        fail('DOIs must be lowercase bare identifiers, not URLs')
    return value


def doi_url(value):
    return 'https://doi.org/' + quote(doi(value), safe='/')


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
        fail('file exceeds 1 MiB')
    try:
        result = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique,
                            parse_constant=lambda _: fail('non-finite JSON number'))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise CatalogError('catalog: invalid UTF-8 JSON') from exc
    validate(result)
    return result


def validate(catalog):
    fields(catalog, {'schema', 'title', 'publisher', 'base_url', 'language', 'works'},
           {'description', 'source_view'}, 'catalog')
    if catalog['schema'] != SCHEMA:
        fail('unsupported schema')
    for name in ('title', 'publisher'):
        text(catalog[name], name)
    if 'description' in catalog:
        text(catalog['description'], 'description', 4096)
    if catalog['language'] != 'en':
        fail('this catalog interface currently supports language en; source documents retain their own languages')
    base = https(catalog['base_url'], 'base_url')
    path = urlsplit(base).path
    if base.endswith('/') or '%' in path or not re.fullmatch(r'(?:/[a-zA-Z0-9_-]+)*', path):
        fail('base_url must have a simple optional path prefix and no trailing slash')
    if 'source_view' in catalog:
        template = text(catalog['source_view'], 'source_view', 2048)
        if template.count('{revision}') != 1 or template.count('{path}') != 1:
            fail('source_view must contain exactly one {revision} and {path}')
        replaced = template.replace('{revision}', 'a' * 40).replace('{path}', 'works/example/manuscript.md')
        https(replaced, 'source_view')
        # Neither substitution may change the origin or authority of the URL.
        if '{' in urlsplit(template).netloc or '{' in urlsplit(template).scheme:
            fail('source_view placeholders must be in the path')
    works = catalog['works']
    if not isinstance(works, list) or len(works) > MAX_EDITIONS:
        fail('works must be a bounded list')
    slugs, revisions, dois = set(), set(), set()
    for work in works:
        fields(work, {'slug', 'metadata_revision', 'editions'}, {'concept_doi', 'recommended_revision'}, 'work')
        slug = work['slug']
        if not isinstance(slug, str) or len(slug) > 128 or not SLUG.fullmatch(slug) or slug in slugs:
            fail('work slugs must be unique, bounded Press-compatible slugs')
        slugs.add(slug)
        if not isinstance(work['editions'], list) or not work['editions']:
            fail('each work requires at least one exact edition')
        editions = {}
        for edition in work['editions']:
            fields(edition, {'revision', 'published', 'status'}, {'label', 'doi', 'notices', 'successor_revision'}, 'edition')
            sha = edition['revision']
            if not isinstance(sha, str) or not REVISION.fullmatch(sha) or sha in revisions:
                fail('each edition requires a unique full lowercase SHA-1 commit')
            revisions.add(sha)
            if len(revisions) > MAX_EDITIONS:
                fail('catalog exceeds 256 editions')
            editions[sha] = edition
            day(edition['published'], 'published')
            if not isinstance(edition['status'], str) or edition['status'] not in STATUSES:
                fail('status must be available, superseded, withdrawn or unavailable')
            if 'label' in edition:
                text(edition['label'], 'label')
            notices = edition.get('notices', [])
            if not isinstance(notices, list) or len(notices) > 64:
                fail('notices must be a bounded list')
            previous = edition['published']
            for notice in notices:
                fields(notice, {'date', 'text'}, {'url'}, 'notice')
                day(notice['date'], 'notice date')
                text(notice['text'], 'notice', 4096)
                if notice['date'] < previous:
                    fail('notices must be chronological and not predate publication')
                previous = notice['date']
                if 'url' in notice:
                    https(notice['url'], 'notice URL')
            if edition['status'] != 'available' and not notices:
                fail('superseded, withdrawn and unavailable editions require an explanatory notice')
        for edition in work['editions']:
            successor = edition.get('successor_revision')
            if edition['status'] == 'superseded' and successor is None:
                fail('superseded editions require a same-work successor_revision')
            if successor is not None:
                if edition['status'] not in {'withdrawn', 'superseded'}:
                    fail('only withdrawn or superseded editions may name a successor')
                if (not isinstance(successor, str) or successor not in editions
                        or successor == edition['revision']
                        or editions[successor]['published'] < edition['published']):
                    fail('successor_revision must identify a later or same-day distinct edition of this work')
                seen = {edition['revision']}
                current = successor
                while current is not None:
                    if current in seen:
                        fail('edition successors may not form cycles')
                    seen.add(current)
                    current = editions[current].get('successor_revision')
        if not isinstance(work['metadata_revision'], str) or work['metadata_revision'] not in editions:
            fail('metadata_revision must select an edition of this work')
        if 'recommended_revision' in work:
            selected = work['recommended_revision']
            if not isinstance(selected, str) or selected not in editions or editions[selected]['status'] != 'available':
                fail('recommended_revision must select an available edition of this work')
        for item, key in [(work, 'concept_doi'), *((e, 'doi') for e in work['editions'])]:
            if key in item:
                identifier = doi(item[key])
                if identifier in dois:
                    fail('a DOI cannot identify multiple works/editions')
                dois.add(identifier)
    return catalog


def compare(previous, current):
    """Guard existing landing URLs and DOI identities; never infer prior state.

    The caller must supply its last deployed, independently retained catalog.
    `--initial-catalog` is an explicit first-build choice, not a migration bypass.
    """
    validate(previous)
    validate(current)
    now = {w['slug']: w for w in current['works']}
    for old_work in previous['works']:
        slug = old_work['slug']
        if slug not in now:
            fail('existing work pages cannot disappear; retain a tombstone: ' + slug)
        work = now[slug]
        if 'concept_doi' in old_work and work.get('concept_doi') != old_work['concept_doi']:
            fail('a work DOI cannot be removed or rebound')
        editions = {e['revision']: e for e in work['editions']}
        for old in old_work['editions']:
            new = editions.get(old['revision'])
            if new is None:
                fail('existing edition pages cannot disappear or move between works')
            if 'doi' in old and new.get('doi') != old['doi']:
                fail('an edition DOI cannot be removed or rebound')
            notices = old.get('notices', [])
            newer = new.get('notices', [])
            if newer[:len(notices)] != notices:
                fail('existing editorial notices must be retained verbatim')
            if any(old.get(k) != new.get(k) for k in ('status', 'published', 'label', 'successor_revision')) and len(newer) <= len(notices):
                fail('status/date/label/successor changes require an appended editorial notice')
    return current


def source_url(catalog, revision, path, anchor=None):
    if 'source_view' not in catalog:
        return None
    url = catalog['source_view'].replace('{revision}', revision).replace('{path}', quote(path, safe='/'))
    return url + ('#' + quote(anchor, safe='') if anchor else '')


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
