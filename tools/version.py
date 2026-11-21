"""Keep distribution metadata and the exact generator dependency on one version line."""
from pathlib import Path
import json
import re
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / 'projects/core/pyproject.toml'
GENERATOR = ROOT / 'projects/a-guavajam-static-website-generator/pyproject.toml'
MANIFEST = ROOT / 'projects/core/src/guavajam_core/resources/chatgpt-capture/manifest.json'


def check():
    core, generator = [tomllib.loads(path.read_text())['project'] for path in (CORE, GENERATOR)]
    version = core['version']
    if generator['version'] != version or generator['dependencies'] != ['guavajam-core==' + version]:
        raise ValueError('core/generator versions and exact dependency must agree')
    if json.loads(MANIFEST.read_text())['version'] != version:
        raise ValueError('capture manifest must match the core version')
    return version


if __name__ == '__main__':
    if len(sys.argv) > 1:
        version = sys.argv[1]
        if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', version):
            raise SystemExit('use a canonical x.y.z version')
        for path in (CORE, GENERATOR):
            text = re.sub(r'(?m)^version = ".*"$', 'version = "' + version + '"', path.read_text())
            text = re.sub(r'guavajam-core==[^" ]+', 'guavajam-core==' + version, text)
            path.write_text(text)
        data = json.loads(MANIFEST.read_text()); data['version'] = version
        MANIFEST.write_text(json.dumps(data, indent=2) + '\n')
    print(check())
