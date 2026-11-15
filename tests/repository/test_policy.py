"""Repository documentation contracts; run with make test-repository."""
from pathlib import Path
import re
import subprocess
import unittest
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
ONBOARDING = {'README.md', 'AGENTS.md'}
# Actual source documents consumed by the authoring, GFM and browser contracts.
MARKDOWN_DATA = {
    'projects/core/src/guavajam_core/resources/templates/work/manuscript.md',
    'projects/core/src/guavajam_core/resources/templates/work/supporting/note.md',
    'tests/fixtures/chatgpt/lantern-current.gfm.md',
    'tests/fixtures/chatgpt/lantern-alternate.gfm.md',
}
PROSE_SUFFIXES = {'.md', '.markdown', '.mdown', '.mkd', '.rst', '.adoc', '.asciidoc', '.txt'}


class DocumentationPolicyTests(unittest.TestCase):
    def test_only_readme_and_agents_are_prose_documentation(self):
        paths = subprocess.check_output(
            ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT
        ).decode().split('\0')
        documents = {path for path in paths if Path(path).suffix.lower() in PROSE_SUFFIXES
                     and (ROOT / path).is_file()}
        self.assertEqual(ONBOARDING | MARKDOWN_DATA, documents,
                         'Keep prose in README.md/AGENTS.md; other Markdown is exercised source/fixture data')

    def test_onboarding_links_resolve_to_current_checkout_files(self):
        for name in sorted(ONBOARDING):
            source = ROOT / name
            for target in re.findall(r'\[[^\]]+\]\(([^)]+)\)', source.read_text()):
                url = urlsplit(target)
                if url.scheme or url.netloc or not url.path:
                    continue
                with self.subTest(source=name, target=target):
                    destination = (source.parent / unquote(url.path)).resolve()
                    self.assertTrue(destination.is_relative_to(ROOT))
                    self.assertTrue(destination.exists(), 'Onboarding must link to existing contracts')


if __name__ == '__main__':
    unittest.main(verbosity=2)
