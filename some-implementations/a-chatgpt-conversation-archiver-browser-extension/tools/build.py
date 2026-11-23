"""Build and verify the independent unpacked-extension ZIP, without GuavaJam."""
from pathlib import Path
import argparse
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def contents():
    manifest = json.loads((ROOT / 'src/manifest.json').read_text())
    package = json.loads((ROOT / 'package.json').read_text())
    if manifest['version'] != package['version']:
        raise ValueError('extension versions disagree')
    if manifest['permissions'] != ['activeTab', 'scripting']:
        raise ValueError('extension authority requires an explicit active-tab action')
    files = {p.relative_to(ROOT / 'src').as_posix(): p.read_bytes()
             for p in sorted((ROOT / 'src').rglob('*')) if p.is_file()}
    for name in ['REUSE.toml', 'LICENSES/Apache-2.0.txt']:
        files[name] = (ROOT / name).read_bytes()
    return manifest['version'], files


def verify(path):
    _, expected = contents()
    with zipfile.ZipFile(path) as archive:
        assert len(archive.infolist()) == len(expected)
        assert set(archive.namelist()) == set(expected)
        assert archive.testzip() is None
        for name, raw in expected.items(): assert archive.read(name) == raw


def build(path):
    _, files = contents()
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, raw)
    verify(path)
    return path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--check', type=Path)
    args = parser.parse_args()
    if args.check: verify(args.check)
    else:
        version, _ = contents()
        print(build(args.output or ROOT / 'dist' / (ROOT.name + '-' + version + '.zip')))
