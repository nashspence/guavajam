"""Compressed immutable physical chunks of one logical, globally ranked database."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
from guavajam.catalog import CatalogError

CHUNK_BYTES = 1024 * 1024
MAX_LOGICAL_BYTES = 8 * 1024 ** 3
MAX_MANIFEST_BYTES = 4 * 1024 ** 2


def _immutable(parent, name, data):
    path = parent / name
    if path.is_symlink() or path.exists() and (not path.is_file() or path.read_bytes() != data):
        raise CatalogError('search chunks: existing immutable artifact differs')
    if not path.exists():
        temporary = parent / ('.' + name + '.tmp')
        try:
            with temporary.open('xb') as output:
                output.write(data)
            temporary.chmod(0o644)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return name


def emit_chunks(database):
    database = Path(database)
    size = database.stat().st_size
    if not 0 < size <= MAX_LOGICAL_BYTES:
        raise CatalogError('search chunks: logical database exceeds 8-GiB operating budget')
    with sqlite3.connect(database) as connection:
        metadata = dict(connection.execute('SELECT key,value FROM metadata'))
        page_size = connection.execute('PRAGMA page_size').fetchone()[0]
    if size % page_size or page_size > 65536 or CHUNK_BYTES % page_size:
        raise CatalogError('search chunks: invalid SQLite page alignment')
    manifest = {'schema': 'guavajam.search-chunks.v1',
                'generation': metadata['generation'], 'scope': metadata['scope'],
                'source': metadata.get('revision', ''), 'size': size,
                'page_size': page_size, 'chunk_size': CHUNK_BYTES, 'chunks': []}
    digest = hashlib.sha256()
    with database.open('rb') as source:
        offset = 0
        while data := source.read(CHUNK_BYTES):
            digest.update(data)
            encoded = gzip.compress(data, compresslevel=9, mtime=0)
            sha = hashlib.sha256(data).hexdigest()
            transfer_sha = hashlib.sha256(encoded).hexdigest()
            name = _immutable(database.parent, transfer_sha + '.sqlite-chunk.gz', encoded)
            manifest['chunks'].append({'offset': offset, 'size': len(data),
                'transfer_size': len(encoded), 'sha256': sha, 'transfer_sha256': transfer_sha, 'url': name})
            offset += len(data)
    if offset != size:
        raise CatalogError('search chunks: database changed during compression')
    manifest['sha256'] = digest.hexdigest()
    payload = json.dumps(manifest, separators=(',', ':'), ensure_ascii=True).encode()
    if len(payload) > MAX_MANIFEST_BYTES:
        raise CatalogError('search chunks: manifest exceeds 4-MiB operating budget')
    identity = hashlib.sha256(payload).hexdigest()
    name = _immutable(database.parent, database.stem + '.' + identity + '.chunks.json', payload)
    return {'url': name, 'encoding': 'sqlite-chunks', 'size': size,
            'transfer_size': len(payload), 'sha256': manifest['sha256'],
            'transfer_sha256': identity, 'generation': metadata['generation']}
