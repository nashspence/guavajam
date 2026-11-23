"""Export optional installed resources without a Git or view dependency."""
import argparse
import os
from pathlib import Path
import shutil
from .resources import ROOT as RESOURCES

EXPORTS = {'credentials': 'storage/credentials.env.example',
           'preview': 'storage/preview.svg'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('name', choices=EXPORTS)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = RESOURCES / EXPORTS[args.name]
    if args.output.exists() or args.output.is_symlink():
        parser.exit(1, 'output must be new\n')
    if source.is_dir():
        shutil.copytree(source, args.output)
    else:
        with open(args.output, 'xb', opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
            stream.write(source.read_bytes())
    return 0
