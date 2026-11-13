"""Run one package's contracts with workspace-only shared fixture support."""
from pathlib import Path
import argparse
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', choices=['core', 'view'])
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / 'tests/support'))
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'projects' / args.package / 'tests'))
    raise SystemExit(not unittest.TextTestRunner().run(suite).wasSuccessful())
