"""Enforce authority dependencies and one owner for each implementation file."""
import ast
import hashlib
import re
import tomllib
from workspace import ROOT, PROJECTS, PYTHON_PROJECTS, MODULES, DEPENDENCIES


def normalized(value):
    return re.sub(r'[-_.]+', '-', re.split(r'[<>=!~;\[ @]', value, maxsplit=1)[0]).lower()


def check(projects=PROJECTS):
    core = tomllib.loads((projects['guavajam'] / 'pyproject.toml').read_text())
    assert core['project']['name'] == 'guavajam'
    assert core['project']['scripts'] == {'guavajam': 'guavajam.cli:main'}
    assert core['tool']['uv']['workspace']['members'] == [
        'some-implementations/' + name for name in PYTHON_PROJECTS if name != 'guavajam']
    owners = {}
    for name, project in projects.items():
        allowed = DEPENDENCIES[name] | {name}
        metadata = project / 'pyproject.toml'
        if metadata.exists():
            data = tomllib.loads(metadata.read_text())
            assert data['project']['name'] == name
            dependencies = data['project'].get('dependencies', []) + data['build-system']['requires']
            for extra in data['project'].get('optional-dependencies', {}).values():
                dependencies.extend(extra)
            for dependency in dependencies:
                target = normalized(dependency)
                assert target not in PROJECTS or target in allowed, name + ' has a forbidden dependency on ' + target
        for directory in [project / 'src', project / ('tests/guavajam' if name == 'guavajam' else 'tests')]:
            for file in directory.rglob('*'):
                if not file.is_file() or file.suffix in {'.pyc', '.wasm'}:
                    continue
                try: source = file.read_text()
                except UnicodeDecodeError: continue
                for target in PROJECTS.keys() - allowed:
                    assert target not in source and target.replace('-', '_') not in source, str(file) + ' binds ' + name + ' to ' + target
                if file.suffix == '.py':
                    for node in ast.walk(ast.parse(source)):
                        modules = ([node.module] if isinstance(node, ast.ImportFrom) and node.level == 0 else
                                   [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
                        for module in modules:
                            owner = next((n for n, package in MODULES.items() if module and module.split('.')[0] == package), None)
                            assert owner is None or owner in allowed, str(file) + ' imports a forbidden authority'
                if directory.name == 'src' and file.suffix in {'.py', '.js', '.mjs', '.css'} and file.stat().st_size > 100:
                    digest = hashlib.sha256(file.read_bytes()).digest()
                    assert digest not in owners, f'copied implementation: {file} and {owners.get(digest)}'
                    owners[digest] = file
    print('GuavaJam is independent; generator/extractor consume its contracts; the archiver is independent; implementation files have one owner.')


if __name__ == '__main__': check()
