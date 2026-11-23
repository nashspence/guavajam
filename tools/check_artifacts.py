"""Check build boundaries with clean environments outside the source checkout."""
from pathlib import Path
import argparse
from email.parser import BytesParser
import hashlib
import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile
import tomllib
from version import check
from workspace import PROJECTS, PYTHON_PROJECTS, MODULES, GENERATOR_NAME, ARCHIVER_NAME

ROOT = Path(__file__).resolve().parents[1]
DISTRIBUTIONS = MODULES

def license_files(package):
    project = PROJECTS[package]
    return tomllib.loads((project / 'pyproject.toml').read_text())['project']['license-files']


def legal_source(package, name):
    project = PROJECTS[package]
    return project / name


def run(*args, cwd=None, env=None):
    subprocess.run(args, cwd=cwd, env=env, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', choices=[*PYTHON_PROJECTS, 'all'], default='all')
    args = parser.parse_args()
    version = check()
    packages = list(PYTHON_PROJECTS) if args.package == 'all' else [args.package]
    env = {key: value for key, value in os.environ.items() if key not in {'PYTHONPATH', 'VIRTUAL_ENV', 'UV_PROJECT_ENVIRONMENT'}}
    env['UV_NO_PROGRESS'] = '1'
    with tempfile.TemporaryDirectory(prefix='guavajam-artifacts-') as directory:
        scratch = Path(directory)
        wheels = {}
        for package in PYTHON_PROJECTS:
            wheel = ROOT / f'dist/{DISTRIBUTIONS[package]}-{version}-py3-none-any.whl'
            if package in packages or package == 'guavajam':
                assert wheel.is_file(), wheel
                wheels[package] = wheel
                with zipfile.ZipFile(wheel) as archive:
                    names = archive.namelist()
                    info = f'{DISTRIBUTIONS[package]}-{version}.dist-info/'
                    metadata = BytesParser().parsebytes(archive.read(info + 'METADATA'))
                    assert metadata['License-Expression'] == 'Apache-2.0', 'wheel license expression differs'
                    assert set(metadata.get_all('License-File', [])) == set(license_files(package))
                    for name in license_files(package):
                        assert archive.read(info + 'licenses/' + name) == legal_source(package, name).read_bytes(), 'wheel lost legal artifact: ' + name
                    assert all(name.startswith((DISTRIBUTIONS[package] + '/', info)) for name in names), 'wheel crossed a package boundary'
                    if package == 'guavajam':
                        assert not any(name.startswith('a_guavajam_static_website_generator/') for name in names)
                    else:
                        assert not any(name.startswith('guavajam/') for name in names)
                        if package == GENERATOR_NAME:
                            assert any(name.endswith('styles/reading.css') for name in names)
                        else:
                            assert any(name.endswith('/extractor.py') for name in names)
        for package in packages:
            # A standalone sdist must build without the workspace root or sibling sources.
            sdist = ROOT / f'dist/{DISTRIBUTIONS[package]}-{version}.tar.gz'
            with tarfile.open(sdist) as archive:
                assert not any('/some-implementations/' in name or '/tests/' in name for name in archive.getnames()), 'sdist bundled another project or test support'
                for name in license_files(package):
                    legal = archive.extractfile(f'{DISTRIBUTIONS[package]}-{version}/' + name)
                    assert legal is not None and legal.read() == legal_source(package, name).read_bytes(), 'sdist lost legal artifact: ' + name
                archive.extractall(scratch / 'sources', filter='data')
            source = scratch / 'sources' / f'{DISTRIBUTIONS[package]}-{version}'
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
            requirements = [str(rebuilt)] if package == 'guavajam' else [str(wheels['guavajam']), str(rebuilt)]
            run('uv', 'pip', 'install', '--python', str(python), *requirements, env=env)
            # -I removes the checkout and inherited import paths from module resolution.
            run(str(python), '-I', str(ROOT / 'tools/artifact_smoke.py'), package, cwd=work, env=env)
            run(str(python), '-I', str(ROOT / 'tools/run_tests.py'), package, cwd=work, env=env)
        if args.package == 'all':
            project = PROJECTS[ARCHIVER_NAME]
            run(sys.executable, str(project / 'tools/build.py'), '--output', str(ROOT / 'dist' / (ARCHIVER_NAME + '-' + version + '.zip')))
        for artifact in sorted((ROOT / 'dist').glob('*')):
            if artifact.is_file() and any(artifact.name.startswith(f'{DISTRIBUTIONS[package]}-{version}') for package in packages):
                print(hashlib.sha256(artifact.read_bytes()).hexdigest(), artifact.name)


if __name__ == '__main__':
    main()
