"""Adversarial examples for the primary package/optional implementation boundary."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from check_boundaries import check

GENERATOR = 'a-guavajam-static-website-generator'


class PackageBoundaryTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(); self.addCleanup(scratch.cleanup)
        primary = Path(scratch.name)
        implementation = primary / 'some-implementations' / GENERATOR
        self.projects = {'guavajam': primary, GENERATOR: implementation}
        shutil.copyfile(ROOT / 'pyproject.toml', primary / 'pyproject.toml')
        for project in self.projects.values():
            (project / 'src').mkdir(parents=True)
        self.source = primary / 'src/example.py'

    def check(self):
        with redirect_stdout(io.StringIO()):
            check(self.projects)

    def test_guavajam_is_valid_without_the_optional_implementation_present(self):
        shutil.rmtree(self.projects[GENERATOR])
        self.check()

    def test_reverse_imports_and_dynamic_implementation_bindings_are_rejected(self):
        for source in ['import a_guavajam_static_website_generator',
                       'from a_guavajam_static_website_generator import renderer',
                       'importlib.import_module("a_guavajam_static_website_generator")',
                       'command = "a-guavajam-static-website-generator preview-docker"']:
            with self.subTest(source=source):
                self.source.write_text(source)
                with self.assertRaises(AssertionError):
                    self.check()

    def test_optional_runtime_and_build_dependencies_cannot_reintroduce_the_generator(self):
        metadata = self.projects['guavajam'] / 'pyproject.toml'
        original = metadata.read_text()
        for source in [original.replace('"PyYAML==6.0.3"', '"a_guavajam_static_website_generator==0.1.0"'),
                       original.replace('"PyYAML==6.0.3"', '"A.Guavajam.Static.Website.Generator==0.1.0"'),
                       original + '\n[project.optional-dependencies]\nwebsite = ["a-guavajam-static-website-generator==0.1.0"]\n',
                       original.replace('"setuptools==80.9.0"', '"a-guavajam-static-website-generator==0.1.0"')]:
            metadata.write_text(source)
            with self.assertRaises(AssertionError):
                self.check()

    def test_duplicate_runtime_implementations_are_rejected(self):
        source = 'def shared():\n    return ' + repr('implementation' * 12) + '\n'
        self.source.write_text(source)
        (self.projects[GENERATOR] / 'src/copied.py').write_text(source)
        with self.assertRaisesRegex(AssertionError, 'copied implementation'):
            self.check()
