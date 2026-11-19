"""The installed authoring command creates exactly one content tree."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml


class StarterTests(unittest.TestCase):
    def test_new_work_is_one_content_tree_and_has_no_edition_bookkeeping(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(['git', 'init', '-q', '-b', 'main'], cwd=repo, check=True)
            command = [sys.executable, '-m', 'guavajam_core', 'new-work',
                       '--title', 'Independent Work', '--author', 'Example Author']
            result = subprocess.run(command, cwd=repo, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            manuscript = repo / 'content/manuscript.md'
            source = manuscript.read_text()
            metadata = yaml.safe_load(source.split('---', 2)[1])
            self.assertEqual('Independent Work', metadata['title'])
            self.assertFalse({'slug', 'status', 'edition', 'published'} & metadata.keys())
            self.assertTrue((repo / 'content/assets/preview.svg').is_file())
            self.assertTrue((repo / 'content/supporting/note.md').is_file())
            self.assertIn('guavajam_core.resources', (repo / 'Makefile').read_text())
            self.assertFalse((repo / 'works').exists())
            self.assertFalse((repo / 'guavajam.json').exists())
            subprocess.run(['git', 'add', 'content'], cwd=repo, check=True)
            checked = subprocess.run([sys.executable, '-m', 'guavajam_core', 'validate-sources'],
                                     cwd=repo / 'content', capture_output=True, text=True)
            self.assertEqual(0, checked.returncode, checked.stdout + checked.stderr)
            repeated = subprocess.run(command, cwd=repo, capture_output=True)
            self.assertNotEqual(0, repeated.returncode)
            self.assertEqual(source, manuscript.read_text())

    def test_resource_export_needs_neither_a_work_nor_the_optional_view(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'capture'
            result = subprocess.run([sys.executable, '-m', 'guavajam_core', 'resources',
                                     'capture', '--output', str(destination)],
                                    cwd=directory, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue((destination / 'capture.mjs').is_file())


if __name__ == '__main__':
    unittest.main()
