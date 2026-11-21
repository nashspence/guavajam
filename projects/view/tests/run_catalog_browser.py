"""Build signed catalog fixtures and run the actual script-free reading journey."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tests/support'))
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from catalog_fixture import make_catalog_fixture
from guavajam.site import build_site


def prepare(directory):
    fixture = make_catalog_fixture(directory)
    output = Path(directory) / 'site'
    # This harness qualifies the complete static journey independently of any
    # ambient SQLite runtime; the work-search harness covers its enhancement.
    build_site(fixture['repo'], output, search_assets='', **fixture['trust'])
    description = {'root': str(output), 'revisions': fixture['revisions'],
                   'citationAnchors': fixture['citation_anchors'], 'passages': fixture['passages']}
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
        return subprocess.run(['node', str(ROOT / 'tests/test_catalog_browser.mjs'),
                               str(fixture), str(output)], check=False).returncode


if __name__ == '__main__':
    sys.exit(main())
