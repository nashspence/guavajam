"""An extension ZIP has one runtime owner and deterministic legal artifacts."""
from pathlib import Path
import importlib.util
import tempfile
import unittest
import zipfile
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('archiver_build',ROOT/'tools/build.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)

class ExtensionBuildTests(unittest.TestCase):
    def test_archive_is_reproducible_and_contains_only_owned_runtime_and_legal_files(self):
        with tempfile.TemporaryDirectory() as scratch:
            first=builder.build(Path(scratch)/'first.zip');second=builder.build(Path(scratch)/'second.zip')
            self.assertEqual(first.read_bytes(),second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                self.assertEqual({'manifest.json','capture.mjs','popup.js','popup.html','REUSE.toml','LICENSES/Apache-2.0.txt'},set(archive.namelist()))
                self.assertEqual((ROOT/'src/capture.mjs').read_bytes(),archive.read('capture.mjs'))
