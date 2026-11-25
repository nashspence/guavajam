"""Experimental bounded Git snapshot reader, not a production replacement.

Streams NUL tree records to a disk inventory, then exchanges one immutable blob
at a time with cat-file --batch. No checkout filters or archive attributes.
Per-item/type/path/deadline checks remain; aggregate disk/work budgets are
explicit operator settings, separate from publication source validity.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import tempfile
import time

from guavajam.asset_policy import AssetError

CHUNK = 64 * 1024


@dataclass(frozen=True)
class Budget:
    blob_bytes: int = 20 * 1024 * 1024
    path_record_bytes: int = 8192
    files: int | None = None
    total_bytes: int | None = None
    idle_seconds: float = 30
    phase_seconds: float = 600


class PipeReader:
    def __init__(self, process, budget):
        self.process = process
        self.budget = budget
        self.buffer = bytearray()
        self.end = time.monotonic() + budget.phase_seconds
        self.selector = selectors.DefaultSelector()
        self.selector.register(process.stdout, selectors.EVENT_READ)

    def close(self):
        self.selector.close()

    def chunk(self):
        timeout = min(self.budget.idle_seconds, self.end - time.monotonic())
        if timeout <= 0 or not self.selector.select(timeout):
            raise AssetError('Git stream exceeded operational deadline')
        return os.read(self.process.stdout.fileno(), CHUNK)

    def until(self, delimiter, maximum, *, eof_ok=False):
        while True:
            end = self.buffer.find(delimiter)
            if end >= 0:
                if end > maximum:
                    raise AssetError('Git record exceeds byte budget')
                result = bytes(self.buffer[:end])
                del self.buffer[:end + len(delimiter)]
                return result
            if len(self.buffer) > maximum:
                raise AssetError('Git record exceeds byte budget')
            data = self.chunk()
            if not data:
                if eof_ok and not self.buffer:
                    return None
                raise AssetError('truncated Git stream')
            self.buffer.extend(data)

    def exact_chunks(self, count):
        while count:
            if not self.buffer:
                data = self.chunk()
                if not data:
                    raise AssetError('truncated Git blob')
                self.buffer.extend(data)
            size = min(count, len(self.buffer), CHUNK)
            result = bytes(self.buffer[:size])
            del self.buffer[:size]
            count -= size
            yield result

    def exact(self, count):
        return b''.join(self.exact_chunks(count))

    def finish(self):
        if self.buffer or self.chunk():
            raise AssetError('unexpected trailing Git data')
        try:
            code = self.process.wait(timeout=max(0.001, self.end - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            raise AssetError('Git stream exceeded operational deadline') from exc
        if code:
            raise AssetError('Git object operation failed; details omitted')


@contextmanager
def child(repo, args, budget):
    process = subprocess.Popen(
        ['git', '--no-replace-objects', '-c', 'core.hooksPath=/dev/null', *args],
        cwd=repo, env={**os.environ, 'GIT_GRAFT_FILE': os.devnull},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        bufsize=0, start_new_session=True)
    reader = PipeReader(process, budget)
    try:
        yield process, reader
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        reader.close()
        for stream in (process.stdin, process.stdout):
            if stream:
                stream.close()


def tree_record(row):
    try:
        fields, raw = row.split(b'\t', 1)
        mode, kind, oid = fields.split()
        name = raw.decode('utf-8', 'strict')
    except (ValueError, UnicodeError) as exc:
        raise AssetError('invalid Git tree record') from exc
    if mode not in {b'100644', b'100755'} or kind != b'blob':
        raise AssetError('source tree must contain regular files only')
    if not re.fullmatch(b'[0-9a-f]{40}', oid):
        raise AssetError('invalid Git blob identity')
    if (not name.startswith('content/') or '\\' in name
            or any(part in {'', '.', '..'} for part in name.split('/'))):
        raise AssetError('unsafe source object path')
    return name, oid.decode('ascii')


class DiskPaths:
    def __init__(self, path, count):
        self.path = path
        self.count = count

    def __len__(self):
        return self.count

    def __iter__(self):
        with self.path.open(encoding='utf-8') as stream:
            for row in stream:
                yield Path(json.loads(row)[0])


@contextmanager
def snapshot(repo, revision, *, budget=Budget(), retain_paths=False):
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise AssetError('snapshot requires a full commit identity')
    with tempfile.TemporaryDirectory(prefix='guavajam-batch-experiment-') as temporary:
        directory = Path(temporary)
        root = directory / 'snapshot'
        root.mkdir()
        inventory = directory / 'inventory.jsonl'
        count = 0
        with inventory.open('x', encoding='utf-8') as saved:
            with child(repo, ['ls-tree', '-r', '-z', revision, '--', 'content/'], budget) as (process, reader):
                process.stdin.close()
                while (row := reader.until(b'\0', budget.path_record_bytes, eof_ok=True)) is not None:
                    name, oid = tree_record(row)
                    count += 1
                    if budget.files is not None and count > budget.files:
                        raise AssetError('snapshot exceeds operational file budget')
                    saved.write(json.dumps([name, oid], ensure_ascii=False) + '\n')
                reader.finish()
        total = 0
        with child(repo, ['cat-file', '--batch'], budget) as (process, reader):
            with inventory.open(encoding='utf-8') as entries:
                for row in entries:
                    name, oid = json.loads(row)
                    process.stdin.write((oid + '\n').encode('ascii'))
                    header = reader.until(b'\n', 128)
                    match = re.fullmatch(oid.encode() + rb' blob ([0-9]+)', header)
                    if not match:
                        raise AssetError('invalid Git batch blob header')
                    size = int(match[1])
                    total += size
                    if size > budget.blob_bytes:
                        raise AssetError('source Git blob exceeds byte budget')
                    if budget.total_bytes is not None and total > budget.total_bytes:
                        raise AssetError('snapshot exceeds operational total byte budget')
                    destination = root / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with destination.open('xb') as output:
                        for data in reader.exact_chunks(size):
                            output.write(data)
                    if reader.exact(1) != b'\n':
                        raise AssetError('invalid Git batch framing')
            process.stdin.close()
            reader.finish()
        files = DiskPaths(inventory, count)
        yield root, list(files) if retain_paths else files
