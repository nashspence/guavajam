#!/usr/bin/env python3
"""Reproducible local source-extraction experiment; never edits target source."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
BASE = Path(os.environ.get('GUAVAJAM_REPO', HERE.parents[1] / 'repos/guavajam')).resolve()
sys.path.insert(0, str(BASE / 'src'))


def git(repo, *args):
    return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(repo), *args], stderr=subprocess.DEVNULL).decode().strip()


def digest(root, files):
    value = hashlib.sha256()
    count = size = 0
    for relative in files:
        value.update(str(relative).encode() + b'\0')
        file_hash = hashlib.sha256()
        with (root / relative).open('rb') as stream:
            while data := stream.read(65536):
                file_hash.update(data)
                size += len(data)
        value.update(file_hash.digest())
        count += 1
    return value.hexdigest(), count, size


def prepare(name, count, payload_bytes=None):
    repo = HERE / 'fixtures' / name
    receipt = repo / 'fixture.json'
    if receipt.exists():
        return json.loads(receipt.read_text())
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, 'init', '-q', '-b', 'main')
    git(repo, 'config', 'user.name', 'Snapshot experiment')
    git(repo, 'config', 'user.email', 'fixture@example.test')
    directory = repo / 'content/supporting'
    directory.mkdir(parents=True)
    for i in range(count):
        raw = (f'---\ntitle: Observation {i}\n---\n\n<a name="observation"></a>\n\nA generic supporting document number {i}.\n').encode()
        if payload_bytes:
            raw = raw + b'x' * (payload_bytes - len(raw))
        (directory / f'page-{i:05d}.md').write_bytes(raw)
    git(repo, 'add', 'content')
    git(repo, 'commit', '-qm', 'Deterministic local fixture')
    revision = git(repo, 'rev-parse', 'HEAD')
    expected, actual_count, size = digest(repo, (Path('content/supporting') / f'page-{i:05d}.md' for i in range(count)))
    result = dict(name=name, path=str(repo), revision=revision, files=actual_count, bytes=size, inventory_sha256=expected)
    receipt.write_text(json.dumps(result, indent=2) + '\n')
    return result


def worker(fixture, mode):
    from guavajam.source_snapshot import snapshot as stock
    from bounded_snapshot import snapshot as batch
    fixture = json.loads(Path(fixture).read_text())
    options = {'retain_paths': mode == 'batch-list'}
    snapshot = stock if mode == 'stock' else batch
    start = time.perf_counter()
    result = {'mode': mode, 'fixture': fixture['name'], 'source_commit': git(BASE, 'rev-parse', 'HEAD'), 'python': sys.version, 'git': subprocess.check_output(['git', '--version'], text=True).strip()}
    try:
        with snapshot(Path(fixture['path']), fixture['revision'], **({} if mode == 'stock' else options)) as (root, files):
            elapsed = time.perf_counter() - start
            actual, count, size = digest(root, files)
            assert (actual, count, size) == (fixture['inventory_sha256'], fixture['files'], fixture['bytes'])
            result.update(result='passed', extraction_seconds=elapsed, files=count, bytes=size, inventory_sha256=actual)
    except Exception as exc:
        result.update(result='rejected', error=type(exc).__name__ + ': ' + str(exc), extraction_seconds=time.perf_counter() - start)
    result.update(total_seconds=time.perf_counter() - start, peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, child_peak_rss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    print(json.dumps(result), flush=True)


def run():
    fixtures = [prepare('4000-files', 4000), prepare('16000-files', 16000), prepare('260-mib', 260, 1024 * 1024)]
    results = []
    output = HERE / 'snapshot-results.json'
    for fixture in fixtures:
        for mode in ('stock', 'batch-list', 'batch-disk'):
            completed = subprocess.run([sys.executable, str(__file__), '--worker', str(Path(fixture['path']) / 'fixture.json'), '--mode', mode], capture_output=True, text=True, check=True, timeout=600)
            row = json.loads(completed.stdout)
            results.append(row)
            output.write_text(json.dumps({'fixtures': fixtures, 'measurements': results, 'notes': ['One execution per scenario; no CPU throttling or cold-cache control.', 'peak_rss_kib is Python process high-water mark, child_peak_rss_kib is largest child, not simultaneous process-tree aggregate.', 'Extraction timer stops on context entry; exact byte/ordered path identity verification and cleanup are in total_seconds.', 'batch-list retains O(file count) path objects for API compatibility. batch-disk spools paths; downstream validator/renderers are not thereby made bounded.', '260MiB fixture measures snapshot transport only; it is not a source-validity or end-to-end rendered-work fixture.']}, indent=2) + '\n')
            print(json.dumps(row), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker')
    parser.add_argument('--mode')
    args = parser.parse_args()
    worker(args.worker, args.mode) if args.worker else run()
