"""Coordinated versions, separately prepared project artifacts, no releases."""
import json
import re
import sys
import tomllib
from workspace import PROJECTS, PYTHON_PROJECTS, ARCHIVER_NAME


def check():
    data = {name: tomllib.loads((project / 'pyproject.toml').read_text())['project']
            for name, project in PYTHON_PROJECTS.items()}
    version = data['guavajam']['version']
    for name, metadata in data.items():
        if metadata['version'] != version or (name != 'guavajam' and metadata['dependencies'] != ['guavajam==' + version]):
            raise ValueError('Python project versions and exact GuavaJam dependencies must agree')
    for name in ['package.json', 'src/manifest.json']:
        if json.loads((PROJECTS[ARCHIVER_NAME] / name).read_text())['version'] != version:
            raise ValueError('archiver package and manifest versions must match the coordinated version')
    return version


if __name__ == '__main__':
    if len(sys.argv) > 1:
        version = sys.argv[1]
        if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', version):
            raise SystemExit('use a canonical x.y.z version')
        for project in PYTHON_PROJECTS.values():
            path = project / 'pyproject.toml'
            text = re.sub(r'(?m)^version = ".*"$', 'version = "' + version + '"', path.read_text())
            path.write_text(re.sub(r'guavajam==[^" ]+', 'guavajam==' + version, text))
        for name in ['package.json', 'src/manifest.json']:
            path = PROJECTS[ARCHIVER_NAME] / name
            data = json.loads(path.read_text()); data['version'] = version
            path.write_text(json.dumps(data, indent=2) + '\n')
    print(check())
