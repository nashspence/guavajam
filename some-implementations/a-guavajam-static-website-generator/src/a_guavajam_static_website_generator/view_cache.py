"""Private disposable cache of undecorated views, never publication evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from guavajam.catalog import CatalogError, encoded
from .renderer import render
from .icons import normalize_site_origin


def inventory(directory):
    files = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise CatalogError('view cache contains a symlink')
        if path.is_file() and path.name != 'CACHE.json':
            files[path.relative_to(directory).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def cached_render(cache, repo, sha, output, *, fingerprints=(), allowed_signers=None, site_origin=None):
    # Caller verifies signatures EVERY run and namespaces this cache by renderer,
    # toolchain and external trust. Never deserialize Python objects from it.
    site_origin = normalize_site_origin(site_origin)
    cache = Path(cache)
    if cache.resolve() != cache.absolute():
        raise CatalogError('cache namespace must not traverse a symlink')
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = cache / hashlib.sha256(encoded({'revision': sha, 'site_origin': site_origin})).hexdigest()
    if target.is_symlink():
        raise CatalogError('view cache entry must not be a symlink')
    if not target.exists():
        with tempfile.TemporaryDirectory(prefix='.view-', dir=cache) as temporary:
            view = Path(temporary) / 'view'
            render(repo, sha, view, fingerprints=fingerprints, allowed_signers=allowed_signers, site_origin=site_origin)
            (view / 'CACHE.json').write_bytes(encoded(inventory(view)))
            view.rename(target)
    try:
        check = (target / 'CACHE.json')
        if check.is_symlink() or check.stat().st_size > 1024 * 1024:
            raise ValueError('invalid cache inventory')
        expected = json.loads(check.read_text('utf-8'))
        if not isinstance(expected, dict) or expected != inventory(target):
            raise ValueError('cache hashes differ')
        view = json.loads((target / 'VIEW.json').read_text('utf-8'))
        if view['preview'] or view['canonical'] != {'hash_algorithm': 'sha1', 'revision': sha,
                                                   'manuscript': 'content/manuscript.md'}:
            raise ValueError('cache source identity differs')
    except (OSError, ValueError, KeyError) as exc:
        raise CatalogError('corrupt view cache; remove this private cache entry and retry; live site was not changed') from exc
    shutil.copytree(target, output, ignore=shutil.ignore_patterns('CACHE.json'))
