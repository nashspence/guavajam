"""Build signed catalog fixtures and run the actual script-free reading journey."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from catalog_fixture import make_catalog_fixture
from guavajam.site import build_site


def prepare(directory):
    fixture = make_catalog_fixture(directory)
    # Exercise preservation in the real three-engine browser route suite: an
    # older edition is withdrawn, while the latest recommendation remains.
    first = fixture['catalog']['works'][0]['editions'][0]
    first.update(status='withdrawn',
                 successor_revision=fixture['revisions'][1],
                 notices=[{'date': '2026-11-05',
                           'text': 'Withdrawn after review. The exact original remains readable.'}])
    fixture['catalog_path'].write_text(json.dumps(fixture['catalog'], indent=2) + '\n')
    output = Path(directory) / 'site'
    with patch.dict(os.environ, {'GNUPGHOME': str(fixture['gpg_home'])}):
        build_site(fixture['repo'], fixture['catalog_path'], output,
                   fingerprints=[fixture['fingerprint']], initial_catalog=True)
    description = {'root': str(output), 'revisions': fixture['revisions']}
    target = Path(directory) / 'browser.json'
    target.write_text(json.dumps(description))
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.prepare:
        print(prepare(args.prepare))
        return 0
    with tempfile.TemporaryDirectory(prefix='guavajam-catalog-browser-') as directory:
        fixture = prepare(Path(directory) / 'fixture')
        output = args.output or Path(directory) / 'evidence'
        return subprocess.run(['mise', 'exec', '--', 'node', str(ROOT / 'tests/test_catalog_browser.mjs'),
                               str(fixture), str(output)], check=False).returncode


if __name__ == '__main__':
    sys.exit(main())
