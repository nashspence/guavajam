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
        assert name not in {'a-guavajam-static-website-generator'}, 'core cannot depend on the optional generator'
    for file in (core / 'src').rglob('*.py'):
        for node in ast.walk(ast.parse(file.read_text())):
            modules = ([node.module] if isinstance(node, ast.ImportFrom) and node.level == 0 else
                       [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
            for module in modules:
                assert module and module.split('.')[0] != 'a_guavajam_static_website_generator', str(file) + ' imports the optional generator'
    owners = {}
    for project in ['core', 'a-guavajam-static-website-generator']:
        for file in (ROOT / 'projects' / project / 'src').rglob('*'):
            if file.is_file() and file.suffix in {'.py', '.js', '.mjs', '.css'} and file.stat().st_size > 100:
                digest = hashlib.sha256(file.read_bytes()).digest()
                assert digest not in owners, f'copied implementation: {file} and {owners.get(digest)}'
                owners[digest] = file
    print('Core is independent of the optional generator; implementation files have one owner.')


if __name__ == '__main__':
    check()
