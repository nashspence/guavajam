"""Discover signed editions from ordinary Git trees, never commit-message syntax.

Edition zero is an unpublished draft. In accepted history, every change to a
numbered work's complete subtree introduces its next edition in a signed commit.
Merges carry existing editions by exact subtree identity. One commit can issue
one edition; its unmodified message and UTC committer date describe that edition.
No program, hook, signer policy or template is loaded from the indexed tree.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import tempfile

from .catalog import validate, compare, fields, CatalogError
from .trusted_git import verify_signature as trusted_signature

OID = re.compile(r'[0-9a-f]{40}\Z')
WORK = re.compile(r'works/([a-z0-9]+(?:-[a-z0-9]+)*)/manuscript\.md\Z')
REPOSITORY_PATH = 'guavajam.json'
MAX_HISTORY = 10000
MAX_POLICY = 1024 * 1024
MAX_MESSAGE = 65536


def fail(message):
    raise CatalogError('discovery: ' + message)


def run(repo, *args, limit=32 * 1024 * 1024):
    """Bound time and captured output; never run a source-controlled executable."""
    command = ['git', '--no-replace-objects', '-c', 'core.hooksPath=/dev/null', *args]
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            p = subprocess.run(command, cwd=repo, stdout=out, stderr=err, timeout=60, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CatalogError('discovery: Git operation failed or timed out') from exc
        if p.returncode or out.tell() > limit or err.tell() > 65536:
            fail('Git operation rejected source, output budget or ancestry')
        out.seek(0)
        return out.read(limit + 1)


def oid(value):
    if not isinstance(value, str) or not OID.fullmatch(value):
        fail('expected a full lowercase SHA-1 commit')
    return value


def resolve(repo, ref='refs/heads/main'):
    if not isinstance(ref, str) or not (OID.fullmatch(ref) or re.fullmatch(r'refs/(?:heads|remotes|guavajam)/[A-Za-z0-9._/-]+', ref)):
        fail('select a full commit or explicit publication branch ref, never a tag')
    if run(repo, 'rev-parse', '--show-object-format').strip() != b'sha1':
        fail('this protocol currently implements Git SHA-1; other algorithms require a new profile')
    if run(repo, 'rev-parse', '--is-shallow-repository').strip() != b'false':
        fail('complete history is required; unshallow the publication repository')
    result = run(repo, 'rev-parse', '--verify', ref + '^{commit}').strip().decode('ascii')
    oid(result)
    return result


def ancestor(repo, before, after):
    oid(before); oid(after)
    run(repo, 'merge-base', '--is-ancestor', before, after)


def blob(repo, revision, path, maximum=MAX_POLICY):
    oid(revision)
    if path.startswith('/') or '\\' in path or '..' in Path(path).parts or '\x00' in path:
        fail('invalid data path')
    row = run(repo, 'ls-tree', revision, '--', path, limit=8192)
    if not row.startswith((b'100644 blob ', b'100755 blob ')) or row.count(b'\n') != 1:
        fail('expected a committed regular data file: ' + path)
    object_id = row.split(b'\t', 1)[0].split()[-1].decode('ascii')
    size = int(run(repo, 'cat-file', '-s', object_id, limit=64))
    if size > maximum:
        fail('data object exceeds limit: ' + path)
    return run(repo, 'cat-file', 'blob', object_id, limit=maximum)


def commit(repo, revision):
    oid(revision)
    size = int(run(repo, 'cat-file', '-s', revision, limit=64))
    if size > MAX_MESSAGE:
        fail('publication/checkpoint commit exceeds 64 KiB')
    raw = run(repo, 'cat-file', 'commit', revision, limit=MAX_MESSAGE)
    try:
        header, message = raw.split(b'\n\n', 1)
        message = message.decode('utf-8', 'strict')
    except (ValueError, UnicodeError) as exc:
        raise CatalogError('discovery: invalid commit encoding') from exc
    return header, message


def commit_facts(repo, revision):
    """Project signed Git facts without interpreting or normalizing the message."""
    header, message = commit(repo, revision)
    match = re.search(rb'^committer .* ([0-9]+) ([+-][0-9]{4})$', header, re.M)
    if match is None:
        fail('commit lacks a valid committer timestamp')
    try:
        published = datetime.fromtimestamp(int(match[1]), timezone.utc).date().isoformat()
    except (ValueError, OverflowError, OSError) as exc:
        raise CatalogError('discovery: unsupported committer timestamp') from exc
    return {'published': published, 'message': message}


def verify_shape(repo, revision, expected=None):
    """Check immutable edition lineage; cryptographic trust is supplied separately."""
    header, _ = commit(repo, revision)
    if not re.search(rb'^gpgsig ', header, re.M):
        fail('publication or repository approval must be in a signed commit')
    _, events = publication_history(repo, revision)
    event = next((event for sha, event in events if sha == revision), None)
    if event is None or (expected and event['kind'] != expected
                         and not (expected == 'repository' and event.get('repository'))):
        fail('revision does not introduce the expected edition or repository approval')
    return event


def verify_signature(repo, revision, *, fingerprints=(), allowed_signers=None):
    try:
        return trusted_signature(repo, revision, fingerprints=fingerprints, allowed_signers=allowed_signers)
    except (ValueError, OSError) as exc:
        raise CatalogError('discovery: ' + str(exc)) from exc


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                fail('duplicate JSON key: ' + key)
            result[key] = value
        return result
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                          parse_constant=lambda _: fail('non-finite number'))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise CatalogError('discovery: invalid bounded UTF-8 JSON') from exc


def policy(raw):
    if len(raw) > MAX_POLICY:
        fail('repository settings exceed 1 MiB')
    data = _json(raw)
    fields(data, {'schema', 'publisher'},
           {'title', 'language', 'description', 'base_url', 'source_view', 'works', 'editions'}, 'repository settings')
    if data['schema'] != 'guavajam.repository.v1':
        fail('unsupported publisher repository settings schema')
    for key in ('works', 'editions'):
        if not isinstance(data.get(key, {}), dict):
            fail(key + ' overrides must be an object')
    # Reuse the exact consumer schema rather than accept arbitrary templates.
    catalog = {k: data[k] for k in ('publisher', 'base_url', 'description', 'source_view') if k in data}
    catalog.update(schema='guavajam.catalog.v1', title=data.get('title', 'Publications'),
                   language=data.get('language', 'en'), works=[])
    validate(catalog)
    return data, catalog


def work_metadata(raw):
    """Read only bounded, unambiguous source metadata; rendering is unnecessary."""
    from .validate_sources import validate_front_matter
    try:
        source = raw.decode('utf-8', 'strict')
    except UnicodeError as exc:
        raise CatalogError('discovery: source must be UTF-8') from exc
    end = source.find('\n---\n', 4)
    if end < 0 or end > 65536:
        fail('source metadata is absent or oversized')
    try:
        facts, _, _, errors = validate_front_matter(Path('manuscript.md'), source)
    except RecursionError as exc:
        raise CatalogError('discovery: source metadata nesting exceeds its limit') from exc
    if errors:
        fail('; '.join(errors[:4]))
    if not isinstance(facts, dict) or facts.get('schema') != 'guavajam.work.v1':
        fail('source requires guavajam.work.v1 metadata')
    edition = facts.get('edition')
    if (not isinstance(edition, str) or not re.fullmatch(r'0|[1-9][0-9]{0,9}', edition)
            or int(edition) > 2147483647):
        fail('edition must be a quoted canonical integer from 0 to 2147483647')
    if not isinstance(facts.get('identifiers', {}), dict):
        fail('source identifiers require a mapping')
    return facts


def _entry(repo, revision, path):
    rows = run(repo, 'ls-tree', '-z', revision, '--', path, limit=8192).split(b'\0')
    if rows == [b'']:
        return None
    if len(rows) != 2 or rows[1]:
        fail('ambiguous tree entry: ' + path)
    record, name = rows[0].split(b'\t', 1)
    if name.decode('utf-8', 'strict') != path:
        fail('unexpected tree entry')
    mode, kind, object_id = record.decode('ascii').split()
    return mode, kind, object_id


def _snapshot(repo, revision, cache):
    """The Git subtree OID covers every byte, path, and file mode in a work."""
    works = {}
    root = _entry(repo, revision, 'works')
    if root:
        if root[:2] != ('040000', 'tree'):
            fail('works must be a directory')
        rows = run(repo, 'ls-tree', '-z', root[2], limit=1024 * 1024).split(b'\0')[:-1]
        if len(rows) > 256:
            fail('too many works')
        for row in rows:
            record, raw_slug = row.split(b'\t', 1)
            slug = raw_slug.decode('utf-8', 'strict')
            mode, kind, tree = record.decode('ascii').split()
            path = 'works/' + slug + '/manuscript.md'
            if not WORK.fullmatch(path) or (mode, kind) != ('040000', 'tree'):
                fail('works must contain only named work directories')
            if (slug, tree) not in cache:
                metadata = work_metadata(blob(repo, revision, path, maximum=8 * 1024 * 1024))
                if metadata.get('slug') != slug:
                    fail('source slug differs from its work path')
                cache[slug, tree] = int(metadata['edition'])
            works[slug] = (cache[slug, tree], tree)
    config = _entry(repo, revision, REPOSITORY_PATH)
    if config and config[:2] not in {('100644', 'blob'), ('100755', 'blob')}:
        fail('repository settings must be a committed regular data file')
    return works, config[2] if config else None


def _history_ids(repo, ref, additional_refs):
    if len(additional_refs) > 128:
        fail('too many history roots')
    tip = resolve(repo, ref)
    tips = list(dict.fromkeys([tip, *(resolve(repo, value) for value in additional_refs)]))
    ids = run(repo, 'rev-list', '--reverse', '--topo-order',
              '--max-count=' + str(MAX_HISTORY + 1), *tips).decode('ascii').splitlines()
    if len(ids) > MAX_HISTORY:
        fail('history exceeds 10,000 commits; use an explicitly reviewed protocol upgrade, not silent truncation')
    return tip, ids, tips


def publication_history(repo, ref='refs/heads/main', *, additional_refs=(), strict=True):
    """Return structural publication/configuration events, without assigning trust.

    Strict mode validates the complete selected DAG, including intermediate
    commits. Draft branches may be edited freely, but invalid numbered draft
    history must be squashed/fixed before acceptance. Non-strict mode is only an
    intent inventory for private previews and branch preservation, never proof.
    """
    repo = Path(repo).resolve(strict=True)
    tip, ids, tips = _history_ids(repo, ref, additional_refs)
    if strict:
        run(repo, 'fsck', '--strict', '--no-reflogs', '--no-dangling', *tips)
    states, cache, issued, events = {}, {}, {}, []
    for sha in ids:
        header, _ = commit(repo, sha)
        parents = re.findall(rb'^parent ([0-9a-f]{40})$', header, re.M)
        if any(parent.decode() not in states for parent in parents):
            fail('complete unmodified parent history is required')
        parent_states = [states[parent.decode()] for parent in parents]
        signed = bool(re.search(rb'^gpgsig ', header, re.M))
        works, config = _snapshot(repo, sha, cache)
        origins, new = {}, []
        slugs = set(works).union(*(set(state[0]) for state in parent_states))
        for slug in sorted(slugs):
            previous = [state[0][slug] for state in parent_states if slug in state[0]]
            maximum = max((state[0] for state in previous), default=0)
            current = works.get(slug)
            if current is None:
                if strict and maximum:
                    fail('a numbered work cannot be deleted: ' + slug)
                continue
            number, tree = current
            matches = [state for state in previous if state[:2] == current]
            if number == 0 and maximum == 0:
                origins[slug] = (*current, None)
                continue
            if matches and number == maximum:
                origins[slug] = matches[0]
                continue
            if not strict:
                # Only a signed bump expresses publication intent. Ordinary
                # signed edits to an unbumped draft are still just draft edits.
                origins[slug] = (*current, None)
                if signed and number > maximum:
                    new.append((slug, number))
                continue
            if number != maximum + 1:
                fail('every changed numbered work must advance its edition by exactly one: ' + slug)
            if not signed:
                fail('an edition bump must be in a signed commit: ' + slug)
            key = (slug, number)
            if key in issued and issued[key] != sha:
                fail('an edition number has distinct publication commits: ' + slug)
            issued[key] = sha
            origins[slug] = (number, tree, sha)
            new.append((slug, number))
        if strict and len(new) > 1:
            fail('a commit may introduce only one work edition')
        # A signed snapshot can bootstrap settings; later changes must be
        # signed where made. Merges may carry a previously authorized snapshot.
        authorities = {state[1][1] for state in parent_states if state[1][1]}
        matches = [state[1][1] for state in parent_states
                   if state[1][0] == config and state[1][1]]
        authority = None
        for candidate in dict.fromkeys(matches):
            try:
                for other in authorities - {candidate}:
                    ancestor(repo, other, candidate)
            except CatalogError:
                continue
            authority = candidate
            break
        approve = bool(config and signed and authority is None)
        if approve:
            authority = sha
        if strict and authorities and not authority:
            fail('repository settings changed without a new signed approval')
        if strict and authorities and config is None:
            fail('authorized repository settings cannot be deleted')
        states[sha] = (origins, (config, authority))
        event = None
        if new:
            slug, number = new[0]
            event = {'kind': 'publication', 'manuscript': 'works/' + slug + '/manuscript.md',
                     'edition': number, **commit_facts(repo, sha)}
            if approve:
                event['repository'] = True
        elif approve:
            event = {'kind': 'repository'}
        if event is not None:
            events.append((sha, event))
    return tip, events


def discover(repo, *, ref='refs/heads/main', fingerprints=(), allowed_signers=None, previous=None):
    """Derive a portable catalog from trusted ordinary commits, without a view."""
    tip, events = publication_history(repo, ref)
    publications, checkpoints = [], []
    for sha, event in events:
        verify_signature(repo, sha, fingerprints=fingerprints, allowed_signers=allowed_signers)
        if event['kind'] == 'publication':
            publications.append((sha, event))
        if event['kind'] == 'repository' or event.get('repository'):
            checkpoints.append(sha)
    if not checkpoints:
        fail('guavajam.json requires an independently trusted signed snapshot')
    # The tip may carry a side branch approval through an unsigned merge.
    approved = blob(repo, tip, REPOSITORY_PATH)
    candidates = [sha for sha in checkpoints if blob(repo, sha, REPOSITORY_PATH) == approved]
    if not candidates:
        fail('repository settings have no signed approval')
    checkpoint = candidates[-1]
    settings, catalog = policy(approved)
    works, concepts, revisions = {}, {}, set()
    for sha, event in publications:
        path = event['manuscript']; slug = WORK.fullmatch(path)[1]
        revisions.add(sha)
        edition = {'revision': sha, 'status': 'available', 'edition': event['edition'],
                   'published': event['published'], 'message': event['message']}
        override = settings.get('editions', {}).get(sha, {})
        fields(override, set(), {'published', 'label', 'doi', 'status', 'notices', 'successor_revision'}, 'edition override')
        if override.get('published', event['published']) != event['published'] and not override.get('notices'):
            fail('correcting the signed commit date requires an explanatory notice')
        edition.update(copy.deepcopy(override))
        concept = work_metadata(blob(repo, sha, path, maximum=8 * 1024 * 1024)).get('identifiers', {}).get('concept_doi')
        if concept:
            concepts.setdefault(slug, set()).add(concept)
        works.setdefault(slug, []).append(edition)
    if set(settings.get('editions', {})) - revisions or set(settings.get('works', {})) - set(works):
        fail('administrative overrides refer to undiscovered publications/works')
    for slug, editions in sorted(works.items()):
        ordered = sorted(editions, key=lambda e: e['edition'])
        item = {'slug': slug, 'metadata_revision': ordered[0]['revision'], 'editions': ordered}
        current = [edition for edition in ordered if edition['status'] == 'available']
        if current:
            item['recommended_revision'] = current[-1]['revision']
        names = concepts.get(slug, set())
        if len(names) > 1:
            fail('published sources disagree on the work DOI')
        if names:
            item['concept_doi'] = next(iter(names))
        override = settings.get('works', {}).get(slug, {})
        fields(override, set(), {'concept_doi', 'metadata_revision', 'recommended_revision'}, 'work override')
        if names and override.get('concept_doi', item['concept_doi']) != item['concept_doi']:
            fail('administrative work DOI differs from signed source')
        item.update(override)
        if item.get('recommended_revision', 'absent') is None:
            item.pop('recommended_revision')
        catalog['works'].append(item)
    validate(catalog)
    if previous is not None:
        compare(previous, catalog)
    return catalog, {'schema': 'guavajam.discovery.v1', 'hash_algorithm': 'sha1', 'tip': tip,
                     'repository_checkpoint': checkpoint, 'publications': [sha for sha, _ in publications],
                     'approvals': [sha for sha, _ in events], 'assets_verified': False}
