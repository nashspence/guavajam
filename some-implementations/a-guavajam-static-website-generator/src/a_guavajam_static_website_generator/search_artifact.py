"""Deterministic, immutable download representations of disposable search indexes."""
from __future__ import annotations

import gzip
import hashlib
from html import escape
from pathlib import Path
import tempfile

from guavajam.catalog import CatalogError

MAX_DATABASE_BYTES = 256 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024


def _digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def emit_download(database):
    """Keep the ordinary SQLite artifact and add one content-addressed gzip file.

    The filename hashes the compressed representation. Empty filename/mtime in
    the gzip header keep identical inputs stable across builds and directories.
    Native HTTP caching can therefore reuse the exact representation indefinitely.
    """
    database = Path(database).absolute()
    if (database.name not in {'index.sqlite', 'work.sqlite'} or not database.is_file()
            or database.resolve() != database):
        raise CatalogError('search download: expected an owned SQLite artifact')
    size = database.stat().st_size
    if not 0 < size <= MAX_DATABASE_BYTES:
        raise CatalogError('search download: database exceeds transfer bounds')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.search-download-', dir=database.parent, delete=False) as output:
            temporary = Path(output.name)
            with gzip.GzipFile(fileobj=output, mode='wb', filename='', mtime=0, compresslevel=9) as compressor:
                with database.open('rb') as source:
                    copied = 0
                    while chunk := source.read(CHUNK_BYTES):
                        copied += len(chunk)
                        if copied > size:
                            raise CatalogError('search download: database changed during compression')
                        compressor.write(chunk)
                    if copied != size:
                        raise CatalogError('search download: database changed during compression')
        transfer_size = temporary.stat().st_size
        if transfer_size > MAX_DATABASE_BYTES:
            raise CatalogError('search download: compressed artifact exceeds transfer bounds')
        digest = _digest(temporary)
        name = database.stem + '.' + digest + '.sqlite.gz'
        destination = database.with_name(name)
        if destination.is_symlink() or (destination.exists() and
                                       (not destination.is_file() or _digest(destination) != digest)):
            raise CatalogError('search download: existing immutable artifact differs')
        if destination.exists():
            temporary.unlink()
        else:
            temporary.chmod(0o644)
            temporary.replace(destination)
        return {'url': name, 'encoding': 'gzip', 'size': size, 'transfer_size': transfer_size}
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def download_attributes(descriptor, *, prefix=''):
    """Embed the small descriptor in existing HTML, without a manifest request."""
    attributes = {
        'data-database': prefix + descriptor['url'],
        'data-database-size': str(descriptor['size']),
        'data-database-transfer-size': str(descriptor['transfer_size']),
        'data-database-encoding': descriptor['encoding'],
    }
    return ' '.join(name + '="' + escape(value, quote=True) + '"' for name, value in attributes.items())
