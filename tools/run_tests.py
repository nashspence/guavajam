"""Run one package's contracts with workspace-only shared fixture support."""
from pathlib import Path
import argparse
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace import ROOT, TESTS

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', choices=['guavajam', 'a-guavajam-static-website-generator'])
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / 'tests/support'))
    suite = unittest.defaultTestLoader.discover(str(TESTS[args.package]))
    raise SystemExit(not unittest.TextTestRunner().run(suite).wasSuccessful())
