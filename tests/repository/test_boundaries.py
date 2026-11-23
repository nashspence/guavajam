"""Adversarial authority dependencies and single-implementation ownership."""
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
from workspace import PROJECTS, GENERATOR_NAME, EXTRACTOR_NAME, ARCHIVER_NAME


class PackageBoundaryTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(); self.addCleanup(scratch.cleanup)
        primary = Path(scratch.name)
        self.projects = {name: primary if name == 'guavajam' else primary / 'some-implementations' / name
                         for name in PROJECTS}
        for name, project in self.projects.items():
            (project / 'src').mkdir(parents=True)
            (project / ('tests/guavajam' if name == 'guavajam' else 'tests')).mkdir(parents=True)
            source = PROJECTS[name] / 'pyproject.toml'
            if source.exists(): shutil.copyfile(source, project / 'pyproject.toml')
        self.source = primary / 'src/example.py'

    def check(self):
        with redirect_stdout(io.StringIO()): check(self.projects)

    def test_core_is_valid_without_any_optional_project_present(self):
        for name, project in self.projects.items():
            if name != 'guavajam': shutil.rmtree(project)
        self.check()

    def test_core_cannot_import_or_dynamically_bind_any_optional_authority(self):
        for name in (GENERATOR_NAME, EXTRACTOR_NAME, ARCHIVER_NAME):
            module = name.replace('-', '_')
            for source in ['import ' + module, 'from ' + module + ' import main',
                           'importlib.import_module("' + module + '")', 'command = "' + name + '"']:
                with self.subTest(name=name, source=source):
                    self.source.write_text(source)
                    with self.assertRaises(AssertionError): self.check()

    def test_optional_runtime_build_and_extra_dependencies_follow_the_authority_graph(self):
        for owner, target in [('guavajam', GENERATOR_NAME), ('guavajam', EXTRACTOR_NAME),
                              (GENERATOR_NAME, EXTRACTOR_NAME), (EXTRACTOR_NAME, GENERATOR_NAME)]:
            metadata = self.projects[owner] / 'pyproject.toml'
            original = metadata.read_text()
            for source in [original + '\n[project.optional-dependencies]\nother = ["' + target + '==0.1.0"]\n',
                           original.replace('"setuptools==80.9.0"', '"' + target.replace('-', '.') + '==0.1.0"')]:
                with self.subTest(owner=owner, target=target):
                    metadata.write_text(source)
                    with self.assertRaises(AssertionError): self.check()
            metadata.write_text(original)

    def test_the_archiver_cannot_bind_core_or_other_optional_implementations(self):
        source = self.projects[ARCHIVER_NAME] / 'src/example.js'
        for target in ['guavajam', GENERATOR_NAME, EXTRACTOR_NAME]:
            source.write_text('const dependency = "' + target + '";')
            with self.subTest(target=target), self.assertRaises(AssertionError): self.check()

    def test_generator_tests_cannot_reintroduce_an_extractor_dependency(self):
        source = self.projects[GENERATOR_NAME] / 'tests/example.py'
        source.write_text('from a_chatgpt_conversation_archive_gfm_extractor.gfm import review_gfm')
        with self.assertRaises(AssertionError): self.check()

    def test_duplicate_runtime_implementations_are_rejected_across_all_owners(self):
        source = 'def shared():\n    return ' + repr('implementation' * 12) + '\n'
        self.source.write_text(source)
        for name in (GENERATOR_NAME, EXTRACTOR_NAME, ARCHIVER_NAME):
            target = self.projects[name] / 'src/copied.py'
            target.write_text(source)
            with self.subTest(name=name), self.assertRaisesRegex(AssertionError, 'copied implementation'):
                self.check()
            target.unlink()
