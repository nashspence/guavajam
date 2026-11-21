"""Repository-only locations of independently packaged projects."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR_NAME = 'a-guavajam-static-website-generator'
PROJECTS = {'guavajam': ROOT, GENERATOR_NAME: ROOT / 'some-implementations' / GENERATOR_NAME}
TESTS = {name: project / ('tests/guavajam' if name == 'guavajam' else 'tests')
         for name, project in PROJECTS.items()}
