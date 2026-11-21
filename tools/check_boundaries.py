"""Enforce GuavaJam independence, distribution ownership and no copied implementations."""
import ast
import hashlib
import re
import tomllib
from workspace import ROOT, PROJECTS, GENERATOR_NAME


def check(projects=PROJECTS):
    primary = projects['guavajam']
    metadata = tomllib.loads((primary / 'pyproject.toml').read_text())
    assert metadata['project']['name'] == 'guavajam'
    assert metadata['project']['scripts'] == {'guavajam': 'guavajam.cli:main'}
    assert metadata['tool']['uv']['workspace']['members'] == ['some-implementations/' + GENERATOR_NAME]
    dependencies = list(metadata['project']['dependencies'])
    dependencies += metadata['build-system']['requires']
    for extra in metadata['project'].get('optional-dependencies', {}).values():
        dependencies.extend(extra)
    for dependency in dependencies:
        name = re.sub(r'[-_.]+', '-', re.split(r'[<>=!~;\[ @]', dependency, maxsplit=1)[0]).lower()
        assert name != GENERATOR_NAME, 'GuavaJam cannot depend on the optional generator'
    for file in (primary / 'src').rglob('*'):
        if not file.is_file() or file.suffix in {'.pyc', '.wasm'}:
            continue
        try:
            source = file.read_text()
        except UnicodeDecodeError:
            continue
        assert GENERATOR_NAME not in source and GENERATOR_NAME.replace('-', '_') not in source, str(file) + ' binds GuavaJam to an implementation'
        if file.suffix == '.py':
            for node in ast.walk(ast.parse(source)):
                modules = ([node.module] if isinstance(node, ast.ImportFrom) and node.level == 0 else
                           [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
                for module in modules:
                    assert module and module.split('.')[0] != GENERATOR_NAME.replace('-', '_'), str(file)
    owners = {}
    for project in projects.values():
        for file in (project / 'src').rglob('*'):
            if file.is_file() and file.suffix in {'.py', '.js', '.mjs', '.css'} and file.stat().st_size > 100:
                digest = hashlib.sha256(file.read_bytes()).digest()
                assert digest not in owners, f'copied implementation: {file} and {owners.get(digest)}'
                owners[digest] = file
    print('GuavaJam is independent; optional implementations consume it; implementation files have one owner.')


if __name__ == '__main__':
    check()
