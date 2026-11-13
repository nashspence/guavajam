"""One periodic update: fetch, verify, derive, reuse views, atomically switch site.

No scheduler is installed. No source checkout, publication, DOI record or
remote branch is changed. Only an operator-selected local fetch ref is updated.
Serve OUTPUT/current; never serve the private cache or generation parent.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import uuid

from .catalog import CatalogError, encoded, read_catalog
from .publication_protocol import ancestor, discover, resolve, run
from .site import build_site

MARKER = 'guavajam.periodic-root.v1\n'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_json(path, maximum=2 * 1024 * 1024):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
        raise CatalogError('invalid private updater state')
    return json.loads(path.read_text('utf-8'))


def atomic(path, data):
    fd, temporary = tempfile.mkstemp(prefix='.state-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def locked_root(output, repo):
    try:
        import fcntl
    except ImportError as exc:
        raise CatalogError('periodic updates require POSIX file locking and atomic symlinks') from exc
    output = Path(output).absolute()
    if output.resolve() != output or output.is_relative_to(repo):
        raise CatalogError('updater output must be outside the source repository, without symlink ancestors')
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        output.mkdir()
        created = True
    except FileExistsError:
        created = False
    if not output.is_dir():
        raise CatalogError('updater root must be a directory')
    marker = output / '.guavajam-root'
    # A new lock file may be shared by two simultaneous first runs. No process
    # may replace an arbitrary populated website with this managed root.
    if not created and not marker.exists() and any(p.name != '.update.lock' for p in output.iterdir()):
        raise CatalogError('nonempty destination is not an updater-managed root; use a fresh output root')
    lock = output / '.update.lock'
    if lock.is_symlink():
        raise CatalogError('lock path is a symlink')
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield None
            return
        if marker.exists():
            if marker.is_symlink() or marker.read_text('utf-8') != MARKER:
                raise CatalogError('unrecognized updater root')
        else:
            if any(p.name != '.update.lock' for p in output.iterdir()):
                raise CatalogError('destination changed during initialization')
            atomic(marker, MARKER.encode())
        for name in ('generations', '.cache'):
            directory = output / name
            if directory.is_symlink():
                raise CatalogError('updater directories must not be symlinks')
            directory.mkdir(exist_ok=True, mode=0o700 if name == '.cache' else 0o755)
        yield output
    finally:
        os.close(fd)


def current(output):
    link = output / 'current'
    if not link.is_symlink():
        if link.exists() or (output / '.last.json').exists():
            raise CatalogError('managed current link is absent or replaced; restore it, never reset the baseline')
        return None, None
    relative = os.readlink(link)
    if not re.fullmatch(r'generations/[a-f0-9]{32}/site', relative):
        raise CatalogError('current link is outside managed generations')
    site = output / relative
    if not site.is_dir() or site.is_symlink() or site.resolve() != site:
        raise CatalogError('current generation is missing or redirected')
    receipt = read_json(site.parent / 'STATE.json')
    if receipt.get('schema') != 'guavajam.update.v1':
        raise CatalogError('unrecognized generation receipt')
    if digest((site / 'CATALOG.json').read_bytes()) != receipt.get('catalog_sha256'):
        raise CatalogError('deployed catalog differs from its retained baseline')
    return site, receipt


def renderer_identity():
    import yaml
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for file in sorted(root.rglob('*')):
        if file.is_file() and file.suffix in {'.py', '.css'}:
            h.update(file.relative_to(root).as_posix().encode() + b'\0' + file.read_bytes())
    try:
        result = subprocess.run(['pandoc', '--version'], capture_output=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CatalogError('required pinned Pandoc is unavailable') from exc
    if len(result.stdout) > 65536:
        raise CatalogError('unexpected compiler version output')
    h.update(platform.python_version().encode() + yaml.__version__.encode() + result.stdout)
    return h.hexdigest()


def trust_identity(fingerprints, allowed_signers):
    if bool(fingerprints) == bool(allowed_signers):
        raise CatalogError('supply external signing trust once; source metadata cannot authorize itself')
    if fingerprints:
        return digest(encoded(sorted(set(f.upper() for f in fingerprints))))
    path = Path(allowed_signers).expanduser().resolve(strict=True)
    if not path.is_file() or path.stat().st_size > 65536:
        raise CatalogError('invalid external SSH trust file')
    return digest(path.read_bytes())


def fetch(repo, output, *, ref='refs/heads/main', remote='origin'):
    if not re.fullmatch(r'refs/heads/[A-Za-z0-9._/-]+', ref) or '..' in ref:
        raise CatalogError('fetch requires an explicit source branch')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', remote):
        raise CatalogError('remote must be an operator-configured Git remote name')
    destination = 'refs/guavajam/' + digest(str(output).encode())[:24] + '/source'
    # No force, pruning, tag auto-follow, submodule recursion or checkout. An
    # interrupted or failed fetch cannot switch the deployed presentation.
    run(repo, '-c', 'protocol.ext.allow=never', 'fetch', '--atomic', '--no-tags',
        '--no-recurse-submodules', remote, ref + ':' + destination, limit=65536)
    return resolve(repo, destination)


def update(repo=Path('../press'), output=Path('.build/site'), *, ref='refs/heads/main', remote='origin',
           no_fetch=False, fingerprints=(), allowed_signers=None, baseline=None, rebuild=False):
    repo = Path(repo).expanduser().resolve(strict=True)
    with locked_root(output, repo) as root:
        if root is None:
            return {'status': 'busy', 'changed': False}
        site, receipt = current(root)
        if baseline is not None and site is not None:
            raise CatalogError('a migration baseline is only allowed before the first managed build')
        previous = read_catalog(site / 'CATALOG.json') if site else (read_catalog(baseline) if baseline else None)
        tip = resolve(repo, ref) if no_fetch else fetch(repo, root, ref=ref, remote=remote)
        if receipt:
            ancestor(repo, receipt['source_tip'], tip)
        last = root / '.last.json'
        if last.exists():
            recorded = read_json(last)
            ancestor(repo, recorded['source_tip'], tip)
        # Signatures are reverified on every poll, including no-ops. Revoked or
        # removed authorization never becomes a cached `verified: true` flag.
        catalog, evidence = discover(repo, ref=tip, fingerprints=fingerprints,
                                     allowed_signers=allowed_signers, previous=previous)
        renderer = renderer_identity()
        trusted = trust_identity(fingerprints, allowed_signers)
        catalog_hash = digest(encoded(catalog))
        identity = digest(encoded({'catalog': catalog_hash, 'renderer': renderer, 'trust': trusted}))
        last_record = {'source_tip': tip, 'identity': identity}
        if receipt and receipt.get('identity') == identity and not rebuild:
            atomic(last, encoded(last_record))
            return {'status': 'unchanged', 'changed': False, 'source_tip': tip,
                    'site': str(root / 'current'), 'publications': len(evidence['publications'])}
        namespace = digest(encoded({'renderer': renderer, 'trust': trusted}))
        if rebuild:
            namespace = digest((namespace + uuid.uuid4().hex).encode())
        elif receipt and receipt.get('renderer') == renderer and receipt.get('trust_sha256') == trusted:
            namespace = receipt.get('cache_namespace', namespace)
        if not re.fullmatch(r'[a-f0-9]{64}', namespace):
            raise CatalogError('invalid retained cache namespace')
        cache = root / '.cache' / namespace
        generation_id = uuid.uuid4().hex
        destination = root / 'generations' / generation_id
        with tempfile.TemporaryDirectory(prefix='.staging-', dir=root) as temporary:
            temporary = Path(temporary)
            catalog_path = temporary / 'catalog.json'
            catalog_path.write_bytes(encoded(catalog))
            prior_path = None
            if previous is not None:
                prior_path = temporary / 'previous.json'; prior_path.write_bytes(encoded(previous))
            staged = temporary / 'generation'; staged.mkdir()
            build_site(repo, catalog_path, staged / 'site', fingerprints=fingerprints,
                       allowed_signers=allowed_signers, previous_catalog=prior_path,
                       initial_catalog=previous is None, view_cache=cache)
            state = {'schema': 'guavajam.update.v1', 'source_tip': tip, 'identity': identity,
                     'catalog_sha256': catalog_hash, 'renderer': renderer, 'trust_sha256': trusted,
                     'site_checkpoint': evidence['site_checkpoint'], 'publications': evidence['publications'],
                     'cache_namespace': namespace}
            (staged / 'STATE.json').write_bytes(encoded(state))
            # Flush completed files before the sole public switch point.
            for saved in staged.rglob('*'):
                if saved.is_file():
                    with saved.open('rb') as stream:
                        os.fsync(stream.fileno())
            staged.rename(destination)
            descriptor = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        pending = root / ('.current-' + generation_id)
        try:
            pending.symlink_to('generations/' + generation_id + '/site')
            os.replace(pending, root / 'current')
        finally:
            pending.unlink(missing_ok=True)
        atomic(last, encoded(last_record))
        return {'status': 'updated', 'changed': True, 'source_tip': tip,
                'site': str(root / 'current'), 'publications': len(evidence['publications'])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path('../press'))
    parser.add_argument('--output', type=Path, default=Path('.build/site'))
    parser.add_argument('--ref', default='refs/heads/main')
    parser.add_argument('--remote', default='origin')
    parser.add_argument('--no-fetch', action='store_true', help='read the selected local branch, e.g. an independently refreshed mirror')
    parser.add_argument('--baseline', type=Path, help='last deployed catalog when migrating an existing manual site')
    parser.add_argument('--rebuild', action='store_true', help='requalify views without using the private render cache')
    trust = parser.add_mutually_exclusive_group()
    trust.add_argument('--trusted-gpg-fingerprint', action='append', default=[])
    trust.add_argument('--ssh-allowed-signers', type=Path)
    args = parser.parse_args(argv)
    if not args.trusted_gpg_fingerprint and args.ssh_allowed_signers is None:
        if os.environ.get('TRUSTED_GPG_FINGERPRINT') and os.environ.get('SSH_ALLOWED_SIGNERS'):
            parser.error('choose exactly one external signing trust policy')
        if os.environ.get('TRUSTED_GPG_FINGERPRINT'):
            args.trusted_gpg_fingerprint = os.environ['TRUSTED_GPG_FINGERPRINT'].split()
        else:
            args.ssh_allowed_signers = Path(os.environ.get('SSH_ALLOWED_SIGNERS', '~/.config/guavajam/allowed_signers')).expanduser()
    try:
        result = update(args.repo, args.output, ref=args.ref, remote=args.remote, no_fetch=args.no_fetch,
                        fingerprints=args.trusted_gpg_fingerprint, allowed_signers=args.ssh_allowed_signers,
                        baseline=args.baseline, rebuild=args.rebuild)
    except (OSError, ValueError, KeyError) as exc:
        print('GuavaJam update failed; no partial generation is selected. Inspect current before retrying: ' + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
