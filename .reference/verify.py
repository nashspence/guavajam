#!/usr/bin/env python3
"""Verify the published research inventory without rerunning benchmarks."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
BASE = '54fce07b619628a5e92be946be4b189873ddf1a0'


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main() -> None:
    manifest = json.loads((ROOT / 'inventory.json').read_text())
    for item in manifest['files']:
        path = ROOT / item['path']
        assert path.is_file() and not path.is_symlink(), item['path']
        assert path.stat().st_size == item['bytes'], item['path']
        assert digest(path) == item['sha256'], item['path']
    expected = {item['path'] for item in manifest['files']}
    assert len(expected) == len(manifest['files'])
    expected.update(manifest['excluded_identity_records'])
    actual = {str(path.relative_to(ROOT)) for path in ROOT.rglob('*')
              if path.is_file() and '__pycache__' not in path.parts and 'node_modules' not in path.parts}
    assert actual == expected, {'missing': sorted(expected-actual), 'extra': sorted(actual-expected)}

    corpus = ROOT / 'experiments/corpus/guavajam-documents.jsonl.gz'
    with gzip.open(corpus, 'rb') as stream:
        raw_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
    assert raw_hash == 'ecb80dfe215b458c119ca6e0265723cdab5e7210410f864ace5b2c6a059f610a'
    fidelity = json.loads((ROOT / 'results/storage/nav-v2latest/source-fidelity.json').read_text())
    assert fidelity['source_sha256_matches_actual_markdown']
    assert fidelity['documents_checked'] == 5947 and fidelity['anchors_checked'] == 36063

    # The reference branch may add only its own payload to the inspected base.
    repo = ROOT.parent
    if (repo / '.git').exists():
        changed = subprocess.check_output(['git','-C',str(repo),'diff','--name-only',BASE,'HEAD'],text=True).splitlines()
        assert all(name.startswith('.reference/') for name in changed), changed
        tracked_dirty = subprocess.check_output(['git','-C',str(repo),'diff','--name-only','HEAD'],text=True).splitlines()
        assert all(name.startswith('.reference/') for name in tracked_dirty), tracked_dirty

    for name in ['browser-chunks-nav-v2latest.json','browser-chunks-nav-v2five.json','browser-chunks-nav-v2latest-isolated.json']:
        report = json.loads((ROOT / 'results/range' / name).read_text())
        for sample in report['samples']:
            assert sample['ok'] and sample['oracleEqual'] and sample['rangeRequests'] == 0
            for cache in ['decodedCache','compressedCache']:
                assert sample[cache]['peak'] <= sample[cache]['budget']
    print(json.dumps({'result':'passed','files':len(manifest['files']),
                      'payload_bytes':sum(item['bytes'] for item in manifest['files']),
                      'corpus_sha256':raw_hash,
                      'scope':'Inventory/source-tree/recorded-receipt consistency only; benchmarks are not rerun.'},indent=2))


if __name__ == '__main__':
    main()
