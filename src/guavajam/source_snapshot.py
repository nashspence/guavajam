"""Materialize framed regular Git blobs through one bounded object-reader process."""
from contextlib import contextmanager
from pathlib import Path
import os
import re
import select
import sqlite3
import subprocess
import tempfile
import time

from .asset_policy import AssetError

MAX_BLOB_BYTES = 20 * 1024 * 1024
DEFAULT_SNAPSHOT_BYTES = 16 * 1024**3
DEFAULT_SNAPSHOT_FILES = 100_000
DEFAULT_TIMEOUT = 900
IO_BYTES = 64 * 1024


def git(repo, *args):
    # Small ancillary object operations share the core bounded Git primitive.
    from .publication_protocol import run
    try:
        return run(repo, *args)
    except (ValueError, OSError) as exc:
        raise AssetError('Git object operation failed; details omitted') from exc


class _Pipe:
    def __init__(self, stream, errors, deadline):
        self.stream, self.errors, self.deadline = stream, errors, deadline
        self.pending = bytearray()

    def _more(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0 or not select.select([self.stream], [], [], remaining)[0]:
            raise AssetError('source Git snapshot exceeded its operation deadline')
        if os.fstat(self.errors.fileno()).st_size > 65536:
            raise AssetError('source Git snapshot exceeded its diagnostic budget')
        chunk = os.read(self.stream.fileno(), IO_BYTES)
        self.pending.extend(chunk)
        return bool(chunk)

    def frame(self, separator, maximum):
        while True:
            found = self.pending.find(separator)
            if found >= 0:
                if found > maximum:
                    raise AssetError('source Git snapshot frame exceeds its byte budget')
                value = bytes(self.pending[:found])
                del self.pending[:found + len(separator)]
                return value
            if len(self.pending) > maximum:
                raise AssetError('source Git snapshot frame exceeds its byte budget')
            if not self._more():
                if self.pending:
                    raise AssetError('source Git snapshot has a truncated frame')
                return None

    def exact(self, size):
        while len(self.pending) < size:
            if not self._more():
                raise AssetError('source Git snapshot has a truncated blob')
        value = bytes(self.pending[:size])
        del self.pending[:size]
        return value


@contextmanager
def _reader(repo, args, deadline, *, input_pipe=False):
    command = ['git', '--no-replace-objects', '-c', 'core.hooksPath=/dev/null', *args]
    with tempfile.TemporaryFile() as errors:
        process = None
        try:
            process = subprocess.Popen(
                command, cwd=repo, stdin=subprocess.PIPE if input_pipe else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=errors,
                env={**os.environ, 'GIT_GRAFT_FILE': os.devnull})
            pipe = _Pipe(process.stdout, errors, deadline)
            yield process, pipe
            if process.stdin:
                process.stdin.close()
            if pipe.frame(b'\n', 0) is not None:
                raise AssetError('source Git snapshot has unexpected trailing data')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssetError('source Git snapshot exceeded its operation deadline')
            if process.wait(timeout=remaining) or os.fstat(errors.fileno()).st_size > 65536:
                raise AssetError('source Git snapshot object operation failed')
        except (OSError, subprocess.TimeoutExpired, BrokenPipeError) as exc:
            raise AssetError('source Git snapshot object operation failed or timed out') from exc
        finally:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()
                process.stdout.close()


class SourcePaths:
    """A repeatable ordered path inventory without retaining all paths in Python."""
    def __init__(self, database):
        self.database = database

    def __iter__(self):
        for path, in self.database.execute('SELECT path FROM files ORDER BY path'):
            yield Path(path)

    def __len__(self):
        return self.database.execute('SELECT count(*) FROM files').fetchone()[0]


@contextmanager
def snapshot(repo, revision, *, maximum_bytes=DEFAULT_SNAPSHOT_BYTES,
             maximum_files=DEFAULT_SNAPSHOT_FILES, timeout=DEFAULT_TIMEOUT):
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise AssetError('snapshot requires a full commit identity')
    if (not isinstance(maximum_bytes, int) or maximum_bytes <= 0
            or not isinstance(maximum_files, int) or maximum_files <= 0
            or not isinstance(timeout, (int, float)) or not 0 < timeout <= 43200):
        raise AssetError('snapshot requires positive explicit operation budgets')
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryDirectory(prefix='guavajam-source-') as directory:
        root = Path(directory)
        with sqlite3.connect(root / '.inventory.sqlite') as inventory:
            inventory.execute('PRAGMA cache_size=-2048')
            inventory.execute('PRAGMA temp_store=FILE')
            inventory.execute('CREATE TABLE files (path TEXT PRIMARY KEY, oid TEXT, size INTEGER)')
            count, total = 0, 0
            with _reader(repo, ['ls-tree', '-r', '-l', '-z', revision, '--', 'content/'], deadline) as (_, tree):
                while (row := tree.frame(b'\0', 8192)) is not None:
                    try:
                        fields, name = row.split(b'\t', 1)
                        mode, kind, oid, size = fields.split()
                        relative = Path(name.decode('utf-8', 'strict'))
                    except (ValueError, UnicodeError) as exc:
                        raise AssetError('source Git snapshot has an invalid tree frame') from exc
                    if mode not in {b'100644', b'100755'} or kind != b'blob':
                        raise AssetError('source tree must contain regular files only')
                    try:
                        size = int(size)
                    except ValueError as exc:
                        raise AssetError('source Git snapshot has an invalid object size') from exc
                    if (relative.is_absolute() or '..' in relative.parts or relative.parts[:1] != ('content',)
                            or '\\' in str(relative) or any(ord(char) < 32 for char in str(relative))
                            or not re.fullmatch(b'[0-9a-f]{40}', oid) or size < 0):
                        raise AssetError('unsafe source object path or identity')
                    if size > MAX_BLOB_BYTES:
                        raise AssetError('source Git snapshot blob exceeds the 20-MiB individual limit: ' + str(relative))
                    count += 1
                    total += size
                    if count > maximum_files or total > maximum_bytes:
                        raise AssetError('source Git snapshot exceeds its explicit file/byte operation budget')
                    inventory.execute('INSERT INTO files VALUES (?,?,?)', (str(relative), oid.decode('ascii'), size))
            inventory.commit()
            with _reader(repo, ['cat-file', '--batch'], deadline, input_pipe=True) as (process, blobs):
                for path, oid, size in inventory.execute('SELECT path,oid,size FROM files ORDER BY path'):
                    process.stdin.write(oid.encode('ascii') + b'\n')
                    process.stdin.flush()
                    header = blobs.frame(b'\n', 128)
                    if header != (oid + ' blob ' + str(size)).encode('ascii'):
                        raise AssetError('source Git snapshot object identity, type or size differs')
                    destination = root / path
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with destination.open('xb') as output:
                        remaining = size
                        while remaining:
                            amount = min(remaining, IO_BYTES)
                            output.write(blobs.exact(amount))
                            remaining -= amount
                    if blobs.exact(1) != b'\n':
                        raise AssetError('source Git snapshot blob framing differs')
            yield root, SourcePaths(inventory)
