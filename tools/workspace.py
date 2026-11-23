"""Repository-only locations and dependency ownership of workspace projects."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR_NAME = 'a-guavajam-static-website-generator'
EXTRACTOR_NAME = 'a-chatgpt-conversation-archive-gfm-extractor'
ARCHIVER_NAME = 'a-chatgpt-conversation-archiver-browser-extension'
PYTHON_PROJECTS = {'guavajam': ROOT, **{
    name: ROOT / 'some-implementations' / name for name in (GENERATOR_NAME, EXTRACTOR_NAME)}}
PROJECTS = {**PYTHON_PROJECTS, ARCHIVER_NAME: ROOT / 'some-implementations' / ARCHIVER_NAME}
MODULES = {name: name.replace('-', '_') for name in PYTHON_PROJECTS}
DEPENDENCIES = {'guavajam': set(), GENERATOR_NAME: {'guavajam'},
                EXTRACTOR_NAME: {'guavajam'}, ARCHIVER_NAME: set()}
TESTS = {name: project / ('tests/guavajam' if name == 'guavajam' else 'tests')
         for name, project in PYTHON_PROJECTS.items()}
