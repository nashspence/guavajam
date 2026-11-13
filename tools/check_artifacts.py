"""Check build boundaries with clean environments outside the source checkout."""
from pathlib import Path
import argparse
import hashlib
import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from version import check

ROOT = Path(__file__).resolve().parents[1]


def run(*args, cwd=None, env=None):
    subprocess.run(args, cwd=cwd, env=env, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', choices=['core', 'view', 'both'], default='both')
    args = parser.parse_args()
    version = check()
    packages = ['core', 'view'] if args.package == 'both' else [args.package]
    env = {key: value for key, value in os.environ.items() if key not in {'PYTHONPATH', 'VIRTUAL_ENV', 'UV_PROJECT_ENVIRONMENT'}}
    env['UV_NO_PROGRESS'] = '1'
    with tempfile.TemporaryDirectory(prefix='guavajam-artifacts-') as directory:
        scratch = Path(directory)
        wheels = {}
        for package in ['core', 'view']:
            wheel = ROOT / f'dist/guavajam_{package}-{version}-py3-none-any.whl'
            if package in packages or package == 'core':
                assert wheel.is_file(), wheel
                wheels[package] = wheel
                with zipfile.ZipFile(wheel) as archive:
                    names = archive.namelist()
                    if package == 'core':
                        assert not any(name.startswith('guavajam/') for name in names)
                    else:
                        assert not any(name.startswith('guavajam_core/') for name in names)
                        assert any(name.endswith('styles/reading.css') for name in names)
        for package in packages:
            # A standalone sdist must build without the workspace root or sibling sources.
            sdist = ROOT / f'dist/guavajam_{package}-{version}.tar.gz'
            with tarfile.open(sdist) as archive:
                archive.extractall(scratch / 'sources', filter='data')
            source = scratch / 'sources' / f'guavajam_{package}-{version}'
            run('uv', 'build', '--no-sources', '--wheel', str(source), '--out-dir', str(scratch / 'rebuilt'), env=env)
            rebuilt = scratch / 'rebuilt' / wheels[package].name
            with zipfile.ZipFile(wheels[package]) as original, zipfile.ZipFile(rebuilt) as standalone:
                assert set(original.namelist()) == set(standalone.namelist()), 'sdist lost package files'
                for name in original.namelist():
                    assert original.read(name) == standalone.read(name), 'sdist changed ' + name
            work = scratch / package
            work.mkdir()
            virtual = work / 'environment'
            run('uv', 'venv', '--python', sys.executable, str(virtual), env=env)
            python = virtual / 'bin/python'
            requirements = [str(rebuilt)] if package == 'core' else [str(wheels['core']), str(rebuilt)]
            run('uv', 'pip', 'install', '--python', str(python), *requirements, env=env)
            # -I removes the checkout and inherited import paths from module resolution.
            run(str(python), '-I', str(ROOT / 'tools/artifact_smoke.py'), package, cwd=work, env=env)
            run(str(python), '-I', str(ROOT / 'tools/run_tests.py'), package, cwd=work, env=env)
        for artifact in sorted((ROOT / 'dist').glob('*')):
            if artifact.is_file() and any(artifact.name.startswith(f'guavajam_{package}-{version}') for package in packages):
                print(hashlib.sha256(artifact.read_bytes()).hexdigest(), artifact.name)


if __name__ == '__main__':
    main()
