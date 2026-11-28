"""Qualify physically scoped artifacts and deliberate browser activation over HTTP."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO_ROOT / 'tests/support'), str(Path(__file__).parent),
               str(REPO_ROOT / 'src'),
               str(REPO_ROOT / 'some-implementations/a-guavajam-static-website-generator/src')]

from test_scoped_search import ScopedSearchTests
from a_guavajam_static_website_generator.search_index import emit_browser_assets, homepage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('GUAVAJAM_SQLITE_WASM_DIR', str(REPO_ROOT / 'node_modules/@sqlite.org/sqlite-wasm/dist'))
    fixture = ScopedSearchTests
    try:
        fixture.setUpClass()
        emit_browser_assets(fixture.first)
        (fixture.first / 'index.html').write_text(homepage(fixture.first, interactive=True))
        manifest = {'root': str(fixture.first), 'work': 'sources/work/', 'latest': fixture.latest,
                    'older': fixture.older, 'editions': 3}
        descriptor = fixture.root / 'scoped-browser-fixture.json'
        descriptor.write_text(json.dumps(manifest))
        return subprocess.run(['node', str(Path(__file__).with_name('test_scoped_search_browser.mjs')),
                               str(descriptor), str(args.output)], check=False).returncode
    finally:
        fixture.doClassCleanups()


if __name__ == '__main__':
    raise SystemExit(main())
