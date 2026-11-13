"""Keep onboarding short and route enforceable contracts to tests and recipes."""
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DocumentationPolicyTests(unittest.TestCase):
    def test_markdown_is_entrypoints_legal_text_or_publication_material(self):
        tracked = subprocess.check_output(
            ["git", "ls-files", "-z"], cwd=ROOT, text=True).split("\0")
        markdown = {Path(name) for name in tracked
                    if Path(name).suffix.lower() in {".md", ".markdown"}}
        entrypoints = {Path("README.md"), Path("AGENTS.md"), Path("COPYRIGHT.md")}
        material_roots = (Path("works"), Path("templates/work"), Path("tests/fixtures"))
        manuals = {path for path in markdown - entrypoints
                   if not any(path.is_relative_to(root) for root in material_roots)}
        self.assertEqual(set(), manuals,
                         "Put operational guidance in AGENTS links or make help, and contracts in tests")
        self.assertEqual({Path("README.md")},
                         {path for path in markdown if path.name.lower() == "readme.md"},
                         "Keep one minimal README entrypoint")

    def test_entrypoints_have_small_word_budgets(self):
        for name, maximum in (("README.md", 50), ("AGENTS.md", 180)):
            with self.subTest(name=name):
                words = (ROOT / name).read_text(encoding="utf-8").split()
                self.assertLessEqual(len(words), maximum,
                                     "Link to contracts and commands instead of restating them")


if __name__ == "__main__":
    unittest.main()


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(loader.loadTestsFromName(name,cls) for cls in (DocumentationPolicyTests,) for name in cls.__dict__ if name.startswith("test_"))
