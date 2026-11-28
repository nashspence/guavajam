"""Build isolated wheels and qualify the installed search implementation."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=['smoke', 'history-50'], default='history-50')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[3]
    with tempfile.TemporaryDirectory(prefix='guavajam-scale-installed-') as directory:
        root = Path(directory)
        for package in ['guavajam', 'a-guavajam-static-website-generator']:
            subprocess.run(['uv', 'build', '--package', package, '--wheel',
                            '--out-dir', str(root / 'wheels')], cwd=repo, check=True)
        environment = root / 'environment'
        subprocess.run(['uv', 'venv', str(environment)], cwd=repo, check=True)
        python = environment / 'bin/python'
        subprocess.run(['uv', 'pip', 'install', '--python', str(python),
                        *map(str, sorted((root / 'wheels').glob('*.whl')))], cwd=repo, check=True)
        return subprocess.run([str(python), str(Path(__file__).with_name('benchmark_search_scale.py')),
                               '--profile', args.profile, '--output', str(args.output.absolute()),
                               '--require-installed'], cwd=root, env={**os.environ, 'PYTHONPATH': ''}).returncode


if __name__ == '__main__':
    raise SystemExit(main())
