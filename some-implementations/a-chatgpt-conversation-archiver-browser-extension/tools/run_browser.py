"""Exercise the built extension using real Chromium extension permissions."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile
from build import build
from verify_archives import verify

ROOT = Path(__file__).resolve().parents[1]

def run(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='chatgpt-archiver-') as scratch:
        package = build(Path(scratch) / 'extension.zip')
        unpacked = Path(scratch) / 'extension'
        with zipfile.ZipFile(package) as archive: archive.extractall(unpacked)
        subprocess.run(['node', '--test', str(ROOT / 'tests/test_chatgpt_capture_browser.mjs')],
            cwd=ROOT, env={**os.environ, 'CHATGPT_ARCHIVER_EXTENSION_DIR':str(unpacked),
            'CHATGPT_ARCHIVER_ARCHIVE_DIR':str(directory)}, check=True)
    verify(directory)

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.output: run(args.output.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix='chatgpt-archive-downloads-') as scratch: run(Path(scratch))
