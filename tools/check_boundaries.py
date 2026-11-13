"""Enforce runtime dependency direction and a single owner for implementation files."""
from pathlib import Path
import ast
import hashlib
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def check():
    core = ROOT / 'projects/core'
    metadata = tomllib.loads((core / 'pyproject.toml').read_text())
    dependencies = list(metadata['project']['dependencies'])
    for extra in metadata['project'].get('optional-dependencies', {}).values():
        dependencies.extend(extra)
    for dependency in dependencies:
        name = re.split(r'[<>=!~;\[ @]', dependency, maxsplit=1)[0].replace('_', '-').lower()
        assert name not in {'guavajam', 'guavajam-view'}, 'core cannot depend on view'
    for file in (core / 'src').rglob('*.py'):
        for node in ast.walk(ast.parse(file.read_text())):
            modules = ([node.module] if isinstance(node, ast.ImportFrom) and node.level == 0 else
                       [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
            for module in modules:
                assert module and module.split('.')[0] != 'guavajam', str(file) + ' imports view'
    owners = {}
    for project in ['core', 'view']:
        for file in (ROOT / 'projects' / project / 'src').rglob('*'):
            if file.is_file() and file.suffix in {'.py', '.js', '.mjs', '.css'} and file.stat().st_size > 100:
                digest = hashlib.sha256(file.read_bytes()).digest()
                assert digest not in owners, f'copied implementation: {file} and {owners.get(digest)}'
                owners[digest] = file
    print('Core is independent of view; implementation files have one owner.')


if __name__ == '__main__':
    check()
