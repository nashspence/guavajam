"""Atom discovery views, derived from verified editions and retained membership events.

Feeds are disposable presentations, never publication or signer authority. Collection
membership changes are observed at successful verification checkpoints; the signed
Git committer timestamp supplies each individual work edition's claimed date.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import uuid
import xml.etree.ElementTree as ET

from guavajam.catalog import CatalogError, https
from guavajam.publication_protocol import commit, history

ATOM = 'http://www.w3.org/2005/Atom'
ET.register_namespace('', ATOM)
FEED_STATE = 'guavajam.collection-feed.v1'
MAX_EVENTS = 200
FEED_LENGTH = 50
KINDS = {'work-added', 'work-removed', 'new-edition'}
REVISION = re.compile(r'[a-f0-9]{40}\Z')
STAMP = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\Z')


def _xml(value):
    """Commit messages can contain UTF-8 characters forbidden in XML 1.0."""
    return ''.join(char if (ord(char) in (9, 10, 13) or
                            0x20 <= ord(char) <= 0xD7FF or
                            0xE000 <= ord(char) <= 0xFFFD or
                            0x10000 <= ord(char) <= 0x10FFFF) else '\ufffd'
                   for char in str(value))


def _child(parent, name, text=None, **attrs):
    element = ET.SubElement(parent, '{' + ATOM + '}' + name, attrs)
    if text is not None:
        element.text = _xml(text)
    return element


def _time(epoch):
    return datetime.fromtimestamp(int(epoch), timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def observed_time():
    """SOURCE_DATE_EPOCH supports reproducible test/deployment observations."""
    epoch = os.environ.get('SOURCE_DATE_EPOCH')
    return _time(epoch if epoch is not None else datetime.now(timezone.utc).timestamp())


def _commit_time(repo, revision, fallback):
    header, _ = commit(repo, revision)
    found = re.search(rb'^committer .+ (-?\d+) [+-]\d{4}$', header, re.M)
    try:
        return _time(found[1])
    except (TypeError, OverflowError, ValueError, OSError):
        return fallback + 'T00:00:00Z'


def _feed(identifier, title, *, updated, alternate):
    root = ET.Element('{' + ATOM + '}feed')
    _child(root, 'id', identifier)
    _child(root, 'title', title)
    _child(root, 'updated', updated)
    _child(root, 'link', href='feed.atom', rel='self', type='application/atom+xml')
    _child(root, 'link', href=alternate, rel='alternate', type='text/html')
    return root


def _entry(root, *, identifier, title, updated, href, summary, category, published=None):
    entry = _child(root, 'entry')
    _child(entry, 'id', identifier)
    _child(entry, 'title', title)
    _child(entry, 'updated', updated)
    if published is not None:
        _child(entry, 'published', published)
    _child(entry, 'link', href=href, rel='alternate', type='text/html')
    _child(entry, 'category', term=category)
    _child(entry, 'summary', summary, type='text')
    return entry


def _write(path, root):
    Path(path).write_bytes(ET.tostring(root, encoding='utf-8', xml_declaration=True) + b'\n')


def write_work_feed(output, repo, catalog, *, repository=''):
    """One entry per immutable verified edition; no persistent work feed state."""
    editions = catalog['editions']
    # The configured source URL distinguishes independently indexed repositories;
    # an immutable first edition is the fallback for standalone publications.
    initial = repository or (editions[0]['revision'] if editions else history(repo, catalog['tip'])[1][0])
    identifier = 'urn:uuid:' + str(uuid.uuid5(uuid.NAMESPACE_URL, 'guavajam:work:' + initial))
    details = []
    for position, edition in enumerate(editions[-FEED_LENGTH:], max(1, len(editions) - FEED_LENGTH + 1)):
        sha = edition['revision']
        view = json.loads((Path(output) / 'editions' / sha / 'read' / 'VIEW.json').read_text('utf-8'))
        details.append((position, edition, view['facts'], _commit_time(repo, sha, edition['published'])))
    title = details[-1][2]['title'] if details else 'Unpublished work'
    updated = details[-1][3] if details else _commit_time(repo, catalog['tip'], observed_time()[:10])
    root = _feed(identifier, title + ' — Editions', updated=updated, alternate='index.html')
    if not editions:
        _child(_child(root, 'author'), 'name', 'GuavaJam work feed')
    for position, edition, facts, stamp in reversed(details):
        sha = edition['revision']
        entry = _entry(root, identifier='urn:uuid:' + str(uuid.uuid5(uuid.NAMESPACE_URL, 'guavajam:edition:' + sha)),
                       title=facts['title'] + ' — v' + str(position), updated=stamp, published=stamp,
                       href='editions/' + sha + '/read/index.html',
                       summary=edition['message'], category='edition')
        from guavajam.bibliography import ordered_authors
        for author in ordered_authors(facts):
            _child(_child(entry, 'author'), 'name', author['name'])
    _write(Path(output) / 'feed.atom', root)


def validate_state(state):
    """Bound the independently retained (not publisher-controlled) event log."""
    if (not isinstance(state, dict) or set(state) != {'schema', 'id', 'initialized', 'members', 'titles', 'events'}
            or state['schema'] != FEED_STATE or not isinstance(state['id'], str)
            or not state['id'].startswith('urn:uuid:')):
        raise CatalogError('invalid retained collection feed state')
    try:
        uuid.UUID(state['id'][9:])
    except (ValueError, AttributeError) as exc:
        raise CatalogError('invalid retained collection feed identity') from exc
    def check_stamp(stamp):
        if not isinstance(stamp, str) or not STAMP.fullmatch(stamp):
            raise CatalogError('invalid retained feed timestamp')
        try:
            datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        except ValueError as exc:
            raise CatalogError('invalid retained feed timestamp') from exc
    check_stamp(state['initialized'])
    members, titles, events = state['members'], state['titles'], state['events']
    if not isinstance(members, list) or len(members) > 256 or len(set(map(str, members))) != len(members):
        raise CatalogError('invalid retained feed membership')
    for url in members:
        https(url, 'retained feed member')
    if not isinstance(titles, dict) or len(titles) > 1024:
        raise CatalogError('invalid retained feed titles')
    for url, title in titles.items():
        https(url, 'retained feed title repository')
        if not isinstance(title, str) or len(title.encode('utf-8')) > 4096:
            raise CatalogError('invalid retained feed title')
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise CatalogError('invalid retained feed events')
    ids = set()
    for event in events:
        if (not isinstance(event, dict) or set(event) !=
                {'id', 'kind', 'repository', 'title', 'observed', 'revision', 'version', 'message'}
                or event['kind'] not in KINDS or not isinstance(event['id'], str)
                or not event['id'].startswith('urn:uuid:') or event['id'] in ids):
            raise CatalogError('invalid retained feed event')
        try:
            uuid.UUID(event['id'][9:])
        except (ValueError, AttributeError) as exc:
            raise CatalogError('invalid retained feed event ID') from exc
        ids.add(event['id'])
        https(event['repository'], 'retained feed repository')
        check_stamp(event['observed'])
        if (not isinstance(event['title'], str) or len(event['title'].encode('utf-8')) > 4096
                or not isinstance(event['message'], str) or len(event['message'].encode('utf-8')) > 65536
                or type(event['version']) is not int or event['version'] < 0 or event['version'] > 10000
                or (event['revision'] is not None and
                    (not isinstance(event['revision'], str) or not REVISION.fullmatch(event['revision'])))):
            raise CatalogError('invalid retained feed event details')
        if (event['kind'] == 'new-edition') != (event['revision'] is not None):
            raise CatalogError('invalid retained feed revision')
    return state


def advance_collection(previous, previous_catalogs, entries, site):
    """Observe only membership changes and new editions of continuing members.

    Legacy checkpoints with existing catalogs establish a baseline without
    fabricating historical notifications during the first upgraded build.
    """
    when = observed_time()
    if previous is not None:
        validate_state(previous)
        before = set(previous['members'])
        events = list(previous['events'])
        titles = dict(previous['titles'])
        identifier = previous['id']
        initialized = previous['initialized']
    else:
        before = {entry['repository'] for entry in entries} if previous_catalogs else set()
        events, titles = [], {}
        identifier = 'urn:uuid:' + str(uuid.uuid4())
        initialized = when
    members = [entry['repository'] for entry in entries]
    selected = set(members)

    def record(kind, url, title, *, revision=None, version=0, message=''):
        events.append({'id': 'urn:uuid:' + str(uuid.uuid4()), 'kind': kind, 'repository': url,
                       'title': title, 'observed': when, 'revision': revision,
                       'version': version, 'message': message})

    for url in sorted(before - selected):
        record('work-removed', url, titles.get(url, url))

    for entry in entries:
        url = entry['repository']
        directory = Path(site) / entry['path']
        catalog = json.loads((directory / 'CATALOG.json').read_text('utf-8'))
        title = json.loads((directory / 'SITE.json').read_text('utf-8'))['title']
        titles[url] = title
        if url not in before:
            record('work-added', url, title, version=len(catalog['editions']))
        else:
            prior = previous_catalogs.get(url)
            if prior is None:
                raise CatalogError('retained feed member has no verified catalog')
            for position, edition in enumerate(catalog['editions'][len(prior['editions']):], len(prior['editions']) + 1):
                view = json.loads((directory / 'editions' / edition['revision'] / 'read' / 'VIEW.json').read_text('utf-8'))
                record('new-edition', url, view['facts']['title'], revision=edition['revision'],
                       version=position, message=edition['message'])
    state = {'schema': FEED_STATE, 'id': identifier, 'initialized': initialized,
             'members': members, 'titles': titles, 'events': events[-MAX_EVENTS:]}
    return validate_state(state)


def write_collection_feed(site, state):
    validate_state(state)
    root = _feed(state['id'], 'GuavaJam — Collection updates',
                 updated=state['events'][-1]['observed'] if state['events'] else state['initialized'],
                 alternate='index.html')
    _child(_child(root, 'author'), 'name', 'GuavaJam collection')
    active = set(state['members'])
    for event in reversed(state['events'][-FEED_LENGTH:]):
        url, kind, title = event['repository'], event['kind'], event['title']
        if kind == 'work-added':
            headline = 'Work added: ' + title
            summary = 'Added to this collection with ' + str(event['version']) + ' existing edition(s).'
        elif kind == 'work-removed':
            headline = 'Work removed: ' + title
            summary = 'Removed from this collection; the underlying publication has not been retracted.'
        else:
            headline = 'New edition: ' + title + ' — v' + str(event['version'])
            summary = event['message']
        if url in active and kind != 'work-removed':
            from .update import digest
            href = 'sources/' + digest(url.encode()) + ('/editions/' + event['revision'] + '/read/index.html'
                                                        if kind == 'new-edition' else '/index.html')
        else:
            href = url
        _entry(root, identifier=event['id'], title=headline, updated=event['observed'],
               href=href, summary=summary, category=kind)
    _write(Path(site) / 'feed.atom', root)
