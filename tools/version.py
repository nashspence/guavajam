"""Keep distribution metadata and the exact generator dependency on one version line."""
from pathlib import Path
import json
import re
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
GUAVAJAM = ROOT / 'pyproject.toml'
GENERATOR = ROOT / 'some-implementations/a-guavajam-static-website-generator/pyproject.toml'
MANIFEST = ROOT / 'src/guavajam/resources/chatgpt-capture/manifest.json'


def check():
    guavajam, generator = [tomllib.loads(path.read_text())['project'] for path in (GUAVAJAM, GENERATOR)]
    version = guavajam['version']
    if generator['version'] != version or generator['dependencies'] != ['guavajam==' + version]:
        raise ValueError('GuavaJam/generator versions and exact dependency must agree')
    if json.loads(MANIFEST.read_text())['version'] != version:
        raise ValueError('capture manifest must match the GuavaJam version')
    return version


if __name__ == '__main__':
    if len(sys.argv) > 1:
        version = sys.argv[1]
        if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', version):
            raise SystemExit('use a canonical x.y.z version')
        for path in (GUAVAJAM, GENERATOR):
            text = re.sub(r'(?m)^version = ".*"$', 'version = "' + version + '"', path.read_text())
            text = re.sub(r'guavajam==[^" ]+', 'guavajam==' + version, text)
            path.write_text(text)
        data = json.loads(MANIFEST.read_text()); data['version'] = version
        MANIFEST.write_text(json.dumps(data, indent=2) + '\n')
    print(check())
