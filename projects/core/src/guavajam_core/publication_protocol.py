"""One work per repository; editions are signed changes to its content/ tree.

The accepted history is a caller-selected ref's first-parent chain. A merge
publishes its result once, relative to its first parent; side-branch commits
are drafts. Commit messages are opaque ordinary Git messages. No executable,
signer policy, edition counter or publication declaration is read from source.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from .catalog import CatalogError, compare, validate
from .trusted_git import verify_signature as trusted_signature

OID = re.compile(r'[0-9a-f]{40}\Z')
CONTENT = 'content'
MANUSCRIPT = 'content/manuscript.md'
MAX_HISTORY = 10000
MAX_POLICY = 1024 * 1024
MAX_MESSAGE = 65536


def fail(message):
    raise CatalogError('discovery: ' + message)


def run(repo, *args, limit=32 * 1024 * 1024):
    """Read Git objects with bounded output, without replacement objects/hooks."""
    command = ['git', '--no-replace-objects', '-c', 'core.hooksPath=/dev/null', *args]
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            process = subprocess.run(command, cwd=repo, stdout=out, stderr=err,
                                     env={**os.environ, 'GIT_GRAFT_FILE': os.devnull},
                                     timeout=60, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CatalogError('discovery: Git operation failed or timed out') from exc
        if process.returncode or out.tell() > limit or err.tell() > 65536:
            fail('Git operation rejected source, output budget or ancestry')
        out.seek(0)
        return out.read(limit + 1)


def oid(value):
    if not isinstance(value, str) or not OID.fullmatch(value):
        fail('expected a full lowercase SHA-1 commit')
    return value


def resolve(repo, ref='refs/heads/main'):
    if not isinstance(ref, str) or not (OID.fullmatch(ref) or re.fullmatch(
            r'refs/(?:heads|remotes|guavajam)/[A-Za-z0-9._/-]+', ref)):
        fail('select a full commit or explicit publication branch ref')
    if run(repo, 'rev-parse', '--show-object-format').strip() != b'sha1':
        fail('this protocol implements Git SHA-1')
    if run(repo, 'rev-parse', '--is-shallow-repository').strip() != b'false':
        fail('complete history is required; unshallow the publication repository')
    result = run(repo, 'rev-parse', '--verify', ref + '^{commit}').strip().decode('ascii')
    return oid(result)


def ancestor(repo, before, after):
    oid(before); oid(after)
    run(repo, 'merge-base', '--is-ancestor', before, after)


def history(repo, ref='refs/heads/main'):
    """Return the selected tip and its complete oldest-first publication chain."""
    tip = resolve(repo, ref)
    revisions = run(repo, 'rev-list', '--first-parent', '--reverse',
                    '--max-count=' + str(MAX_HISTORY + 1), tip).decode('ascii').splitlines()
    if len(revisions) > MAX_HISTORY:
        fail('history exceeds 10,000 commits; refusing silent truncation')
    return tip, revisions


def first_parent_contains(repo, before, after):
    oid(before); oid(after)
    if before not in history(repo, after)[1]:
        fail('accepted revision must remain on the first-parent history')


def blob(repo, revision, path, maximum=MAX_POLICY):
    oid(revision)
    if path.startswith('/') or '\\' in path or '..' in Path(path).parts or '\x00' in path:
        fail('invalid data path')
    row = run(repo, 'ls-tree', '-z', revision, '--', path, limit=8192)
    if not row.startswith((b'100644 blob ', b'100755 blob ')) or row.count(b'\0') != 1:
        fail('expected a committed regular data file: ' + path)
    object_id = row.split(b'\t', 1)[0].split()[-1].decode('ascii')
    if int(run(repo, 'cat-file', '-s', object_id, limit=64)) > maximum:
        fail('data object exceeds limit: ' + path)
    return run(repo, 'cat-file', 'blob', object_id, limit=maximum)


def content_tree(repo, revision):
    oid(revision)
    row = run(repo, 'ls-tree', '-z', revision, '--', CONTENT, limit=8192)
    if not row:
        return None
    if not row.startswith(b'040000 tree ') or row.count(b'\0') != 1:
        fail('content/ must be a Git directory tree')
    return oid(row.split(b'\t', 1)[0].split()[-1].decode('ascii'))


def commit(repo, revision):
    oid(revision)
    if int(run(repo, 'cat-file', '-s', revision, limit=64)) > MAX_MESSAGE:
        fail('commit exceeds 64 KiB')
    raw = run(repo, 'cat-file', 'commit', revision, limit=MAX_MESSAGE)
    try:
        header, message = raw.split(b'\n\n', 1)
        return header, message.decode('utf-8', 'strict')
    except (ValueError, UnicodeError) as exc:
        raise CatalogError('discovery: invalid UTF-8 commit') from exc


def edition_history(repo, ref='refs/heads/main'):
    """Find changed content trees, without assigning trust or validating source."""
    tip, revisions = history(repo, ref)
    candidates = []
    previous = None
    for revision in revisions:
        tree = content_tree(repo, revision)
        if tree != previous:
            if tree is None:
                fail('published content/ cannot be deleted')
            candidates.append((revision, tree))
        previous = tree
    return tip, candidates


def verify_shape(repo, revision):
    """Identify a signed content-changing commit, independently of a branch name."""
    header, message = commit(repo, revision)
    if not re.search(rb'^gpgsig ', header, re.M):
        fail('content-changing edition must be a signed commit')
    tree = content_tree(repo, revision)
    if tree is None:
        fail('edition requires content/')
    parents = re.findall(rb'^parent ([0-9a-f]{40})$', header, re.M)
    if parents and tree == content_tree(repo, parents[0].decode('ascii')):
        fail('edition must change content/ relative to its first parent')
    # The timestamp is a signed publisher claim, not evidence of public receipt.
    stamp = re.search(rb'^committer .+ (-?\d+) [+-]\d{4}$', header, re.M)
    try:
        published = datetime.fromtimestamp(int(stamp[1]), timezone.utc).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError) as exc:
        raise CatalogError('discovery: invalid committer timestamp') from exc
    return {'manuscript': MANUSCRIPT, 'tree': tree, 'published': published, 'message': message}


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


def discover(repo, *, ref='refs/heads/main', fingerprints=(), allowed_signers=None, previous=None):
    """Verify one work's editions; a catalog is derived data, never authority.

    Source validation is offline. Asset delivery/retention is qualified separately.
    Every content change on the selected first-parent chain must verify. Nothing
    is silently skipped, and timestamps never determine edition ordering.
    """
    from .publication_commit import verify
    repo = Path(repo).resolve(strict=True)
    tip, candidates = edition_history(repo, ref)
    if previous is not None:
        validate(previous)
        first_parent_contains(repo, previous['tip'], tip)
    editions = []
    for revision, tree in candidates:
        proof = verify(repo, revision, fingerprints=fingerprints,
                       allowed_signers=allowed_signers, online_media=False)
        editions.append({'revision': revision, 'tree': tree,
                         'published': proof['published'], 'message': proof['message']})
    catalog = {'schema': 'guavajam.catalog.v1', 'hash_algorithm': 'sha1',
               'tip': tip, 'editions': editions}
    validate(catalog)
    if previous is not None:
        compare(previous, catalog)
    return catalog, {'schema': 'guavajam.discovery.v1', 'hash_algorithm': 'sha1',
                     'tip': tip, 'publications': [item['revision'] for item in editions],
                     'assets_verified': False}
