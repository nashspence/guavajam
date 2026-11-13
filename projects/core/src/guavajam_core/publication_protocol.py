"""Press publication/site discovery v1. Shared data-only protocol.

No program, hook, signer policy or template is loaded from the indexed tree.
Publication v1 requires a publisher-declared date and permits an edition label.
A separately signed empty Press-Site commit authorizes press-site.json. That
administrative checkpoint does not issue or alter a canonical publication.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import subprocess
import tempfile

from .catalog import validate, compare, encoded, fields, text, day, CatalogError
from .trusted_git import verify_signature as trusted_signature

OID = re.compile(r'[0-9a-f]{40}\Z')
WORK = re.compile(r'works/([a-z0-9]+(?:-[a-z0-9]+)*)/manuscript\.md\Z')
SITE_PATH = 'press-site.json'
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


def declaration(message):
    """Strict terminal trailer block; a signature on an ordinary commit is not publication."""
    if not isinstance(message, str) or '\x00' in message or '\r' in message:
        fail('invalid declaration text')
    lines = message.rstrip('\n').split('\n')
    marked = [line for line in lines if line.startswith('Press-')]
    if not marked:
        return None
    if marked == ['Press-Site: 1']:
        result = {'kind': 'site', 'version': 1}
    elif len(marked) in {3, 4} and marked[0] == 'Press-Publication: 1':
        if not marked[1].startswith('Press-Work: '):
            fail('publication requires Press-Work')
        path = marked[1][len('Press-Work: '):]
        if not WORK.fullmatch(path):
            fail('publication work must use works/<slug>/manuscript.md')
        if not marked[2].startswith('Press-Published: '):
            fail('publication requires an explicit Press-Published: YYYY-MM-DD')
        result = {'kind': 'publication', 'version': 1, 'manuscript': path,
                  'published': day(marked[2][len('Press-Published: '):], 'signed publication date')}
        if len(marked) == 4:
            if not marked[3].startswith('Press-Label: '):
                fail('unknown publication trailer')
            result['label'] = text(marked[3][len('Press-Label: '):], 'signed edition label')
    else:
        fail('unknown, duplicated or mixed Press declaration')
    if lines[-len(marked):] != marked or (len(lines) > len(marked) and lines[-len(marked)-1] != ''):
        fail('Press declarations must form one final block separated from the message by a blank line')
    return result


def publication_trailers(path, published, label=None):
    if not WORK.fullmatch(path):
        fail('invalid publication manuscript path')
    result = ['Press-Publication: 1', 'Press-Work: ' + path,
              'Press-Published: ' + day(published, 'publication date')]
    if label is not None:
        result.append('Press-Label: ' + text(label, 'publication label'))
    return result


def verify_shape(repo, revision, expected=None):
    header, message = commit(repo, revision)
    if not re.search(rb'^gpgsig ', header, re.M):
        fail('declaration must be in a signed commit')
    declared = declaration(message)
    if declared is None or (expected and declared['kind'] != expected):
        fail('revision lacks the expected explicit declaration')
    parents = re.findall(rb'^parent ([0-9a-f]{40})$', header, re.M)
    tree = re.search(rb'^tree ([0-9a-f]{40})$', header, re.M)
    if len(parents) != 1 or tree is None or run(repo, 'rev-parse', parents[0].decode() + '^{tree}').strip() != tree[1]:
        fail('declaration must be an empty, single-parent commit preserving its reviewed tree')
    return declared


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
        fail('site settings exceed 1 MiB')
    data = _json(raw)
    fields(data, {'schema', 'publisher', 'base_url'},
           {'title', 'language', 'description', 'source_view', 'works', 'editions'}, 'site settings')
    if data['schema'] != 'press.site.v1':
        fail('unsupported publisher site settings schema')
    for key in ('works', 'editions'):
        if not isinstance(data.get(key, {}), dict):
            fail(key + ' overrides must be an object')
    # Reuse the exact consumer schema rather than accept arbitrary templates.
    catalog = {k: data[k] for k in ('publisher', 'base_url', 'description', 'source_view') if k in data}
    catalog.update(schema='guavajam.catalog.v1', title=data.get('title', 'Publications'),
                   language=data.get('language', 'en'), works=[])
    validate(catalog)
    return data, catalog


def _concept_doi(raw):
    # Only bounded bibliographic data is inspected here. Full source validation
    # remains in the reader/publication checker; no template or code executes.
    import yaml
    source = raw.decode('utf-8', 'strict')
    if not source.startswith('---\n'):
        fail('publication lacks source front matter')
    end = source.find('\n---\n', 4)
    if end < 0 or end > 65536:
        fail('source metadata is absent or oversized')
    front = source[4:end]
    try:
        for token in yaml.scan(front):
            if isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken, yaml.tokens.TagToken)):
                fail('source metadata aliases/tags are not accepted')
        class UniqueLoader(yaml.SafeLoader):
            pass
        def mapping(loader, node):
            result = {}
            for key_node, value_node in node.value:
                key = loader.construct_object(key_node, deep=True)
                if not isinstance(key, str) or key in result:
                    fail('source metadata has duplicate or non-string keys')
                result[key] = loader.construct_object(value_node, deep=True)
            return result
        UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
        facts = yaml.load(front, Loader=UniqueLoader)
    except yaml.YAMLError as exc:
        raise CatalogError('discovery: malformed source metadata') from exc
    if not isinstance(facts, dict):
        fail('source metadata requires a mapping')
    identifiers = facts.get('identifiers', {})
    if not isinstance(identifiers, dict):
        fail('source identifiers require a mapping')
    return identifiers.get('concept_doi')


def discover(repo, *, ref='refs/heads/main', fingerprints=(), allowed_signers=None, previous=None):
    """Return the exact guavajam.catalog.v1 input and non-authoritative evidence.

    Enumerate ONLY the selected history. Invalid declarations fail closed; no
    tag/branch name, commit time, author field or JSON boolean establishes trust.
    """
    repo = Path(repo).resolve(strict=True)
    tip = resolve(repo, ref)
    ids = run(repo, 'rev-list', '--max-count=' + str(MAX_HISTORY + 1), tip).splitlines()
    if len(ids) > MAX_HISTORY:
        fail('history exceeds 10,000 commits; use an explicitly reviewed protocol upgrade, not silent truncation')
    raw = run(repo, 'log', '--no-show-signature', '--reverse', '--topo-order', '--format=%H%x00%B%x00', tip)
    parts = raw.split(b'\x00')
    if parts[-1].strip():
        fail('invalid Git message framing')
    publications, checkpoints, declarations_seen = [], [], []
    for i in range(0, len(parts)-1, 2):
        sha = oid(parts[i].strip().decode('ascii'))
        message = parts[i+1].decode('utf-8', 'strict')
        if not re.search(r'^Press-(?:Publication|Work|Site|Published|Label):', message, re.M):
            continue
        candidate = verify_shape(repo, sha)
        verify_signature(repo, sha, fingerprints=fingerprints, allowed_signers=allowed_signers)
        declarations_seen.append(sha)
        if candidate['kind'] == 'publication':
            publications.append((sha, candidate))
        else:
            if checkpoints:
                ancestor(repo, checkpoints[-1], sha)
            checkpoints.append(sha)
    if not checkpoints:
        fail('press-site.json requires a trusted signed empty commit ending with Press-Site: 1')
    checkpoint = checkpoints[-1]
    approved = blob(repo, checkpoint, SITE_PATH)
    if blob(repo, tip, SITE_PATH) != approved:
        fail('site settings changed without a new signed Press-Site checkpoint')
    settings, catalog = policy(approved)
    works, concepts, revisions = {}, {}, set()
    for sha, declared in publications:
        path = declared['manuscript']; slug = WORK.fullmatch(path)[1]
        revisions.add(sha)
        edition = {'revision': sha, 'status': 'available', 'published': declared['published']}
        if 'label' in declared:
            edition['label'] = declared['label']
        override = settings.get('editions', {}).get(sha, {})
        fields(override, set(), {'published', 'label', 'doi', 'status', 'notices', 'successor_revision'}, 'edition override')
        if any(key in declared and override.get(key, declared[key]) != declared[key] for key in ('published', 'label')) and not override.get('notices'):
            fail('correcting signed date/label requires an explanatory notice')
        edition.update(copy.deepcopy(override))
        concept = _concept_doi(blob(repo, sha, path, maximum=8 * 1024 * 1024))
        if concept:
            concepts.setdefault(slug, set()).add(concept)
        works.setdefault(slug, []).append(edition)
    if set(settings.get('editions', {})) - revisions or set(settings.get('works', {})) - set(works):
        fail('administrative overrides refer to undiscovered publications/works')
    for slug, editions in sorted(works.items()):
        # Default chronology is publisher-declared date, with accepted topological
        # history breaking same-day ties. Never use author/committer timestamps.
        ordered = sorted(editions, key=lambda e: e['published'])
        item = {'slug': slug, 'metadata_revision': editions[0]['revision'], 'editions': ordered}
        current = [e for e in ordered if e['status'] == 'available']
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
    return catalog, {'schema': 'press.discovery.v1', 'hash_algorithm': 'sha1', 'tip': tip,
                     'site_checkpoint': checkpoint, 'publications': [sha for sha, _ in publications],
                     'declarations': declarations_seen, 'assets_verified': False}
