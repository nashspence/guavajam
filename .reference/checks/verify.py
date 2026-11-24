"""Verify reference patch bytes and reconstruct their recorded Git trees.

Run from a checkout of this handoff: python .reference/checks/verify.py
Uses temporary indexes. Does not apply changes to the working tree or imply
acceptance. Validate proffer.json separately with the pinned Proffer checker.
"""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / '.reference'


def git(environment, *arguments):
    return subprocess.check_output(['git', *arguments], cwd=ROOT,
                                   env=environment, text=True).strip()


def main():
    manifest = json.loads((REFERENCE / 'patches.json').read_text())
    entries = manifest['patches']
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith('GIT_')}
    paths = []
    for entry in entries:
        path = (ROOT / entry['path']).resolve(strict=True)
        if not path.is_relative_to(REFERENCE) or not path.is_file():
            raise ValueError('Patch path must be a file inside .reference')
        raw = path.read_bytes()
        if (len(raw) != entry['bytes']
                or hashlib.sha256(raw).hexdigest() != entry['sha256']):
            raise ValueError('Patch byte identity differs: ' + entry['path'])
        if entry['base_commit'] != manifest['base_commit']:
            raise ValueError('Patch base differs from inventory base')
        paths.append(path)
    if [path.name for path in paths] != manifest['combined_evaluation']['patch_order']:
        raise ValueError('Combined evaluation order differs from inventory')
    with tempfile.TemporaryDirectory(prefix='proffer-reference-index-') as temporary:
        for number, (entry, path) in enumerate(zip(entries, paths)):
            environment['GIT_INDEX_FILE'] = str(Path(temporary) / str(number))
            git(environment, 'read-tree', manifest['base_commit'])
            git(environment, 'apply', '--cached', '--whitespace=error', str(path))
            tree = git(environment, 'write-tree')
            if tree != entry['independently_tested_tree']:
                raise ValueError('Independent tree differs: ' + entry['concern'])
        environment['GIT_INDEX_FILE'] = str(Path(temporary) / 'combined')
        git(environment, 'read-tree', manifest['base_commit'])
        for path in paths:
            git(environment, 'apply', '--cached', '--whitespace=error', str(path))
        tree = git(environment, 'write-tree')
        if tree != manifest['combined_evaluation']['tree']:
            raise ValueError('Combined tree differs from recorded evaluation')
    print(json.dumps({'result': 'matched', 'patches': len(entries),
                      'base': manifest['base_commit'], 'combined_tree': tree}))


if __name__ == '__main__':
    main()
