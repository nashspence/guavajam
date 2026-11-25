"""Focused extraction correctness and bounded-I/O failure experiments."""
from contextlib import contextmanager
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
BASE = Path(os.environ.get('GUAVAJAM_REPO', HERE.parents[1] / 'repos/guavajam')).resolve()
sys.path.insert(0, str(BASE / 'src'))
from guavajam.asset_policy import AssetError
import guavajam.source_snapshot as stock
import bounded_snapshot as candidate


@contextmanager
def stream_process(code, budget):
    process = subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               bufsize=0, start_new_session=True)
    reader = candidate.PipeReader(process, budget)
    try:
        yield process, reader
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        reader.close()
        process.stdin.close()
        process.stdout.close()


class StreamFailures(unittest.TestCase):
    def test_truncated_header(self):
        with stream_process("import os; os.write(1,b'123 blob 4')", candidate.Budget()) as (_, reader):
            with self.assertRaisesRegex(AssetError, 'truncated'):
                reader.until(b'\n', 128)

    def test_oversized_header(self):
        with stream_process("import os; os.write(1,b'x'*129+b'\\n')", candidate.Budget()) as (_, reader):
            with self.assertRaisesRegex(AssetError, 'record exceeds'):
                reader.until(b'\n', 128)

    def test_truncated_body(self):
        with stream_process("import os; os.write(1,b'abc')", candidate.Budget()) as (_, reader):
            with self.assertRaisesRegex(AssetError, 'truncated'):
                reader.exact(4)

    def test_stalled_child_hits_deadline_and_is_reaped(self):
        start = time.monotonic()
        with stream_process('import time; time.sleep(10)', candidate.Budget(idle_seconds=0.05, phase_seconds=0.1)) as (process, reader):
            with self.assertRaisesRegex(AssetError, 'deadline'):
                reader.until(b'\n', 128)
        self.assertIsNotNone(process.returncode)
        self.assertLess(time.monotonic() - start, 2)

    def test_nonzero_exit_is_rejected(self):
        with stream_process('raise SystemExit(1)', candidate.Budget()) as (_, reader):
            with self.assertRaisesRegex(AssetError, 'operation failed'):
                reader.finish()

    def test_path_and_entry_rejection(self):
        oid = b'a' * 40
        for name in (b'/content/file', b'content/../file', b'content/./file', b'content//file', b'content/dir\\file', b'content/\xfffile', b'outside/file'):
            with self.subTest(name=name), self.assertRaises(AssetError):
                candidate.tree_record(b'100644 blob ' + oid + b'\t' + name)
        for kind in (b'120000 blob ', b'160000 commit ', b'040000 tree '):
            with self.subTest(kind=kind), self.assertRaisesRegex(AssetError, 'regular files'):
                candidate.tree_record(kind + oid + b'\tcontent/file')
        self.assertEqual(candidate.tree_record(b'100755 blob ' + oid + b'\tcontent/line\nbreak'), ('content/line\nbreak', oid.decode()))


class SnapshotFailures(unittest.TestCase):
    def setUp(self):
        self.repo = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.test')
        (self.repo / 'content').mkdir()
        (self.repo / 'content/manuscript.md').write_bytes(b'original\n')

    def git(self, *args):
        return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(self.repo), *args], stderr=subprocess.DEVNULL).decode().strip()

    def commit(self):
        self.git('add', '-A')
        self.git('commit', '-qm', 'Fixture')
        return self.git('rev-parse', 'HEAD')

    def test_raw_selected_blob_filters_attributes_replacements_and_untracked(self):
        (self.repo / '.gitattributes').write_text('content/manuscript.md export-ignore filter=hostile\n')
        (self.repo / 'outside-link').symlink_to('/missing')
        revision = self.commit()
        self.git('config', 'filter.hostile.smudge', 'exit 99')
        original_oid = self.git('rev-parse', revision + ':content/manuscript.md')
        (self.repo / 'other').write_bytes(b'replacement\n')
        replacement = self.git('hash-object', '-w', 'other')
        self.git('replace', original_oid, replacement)
        (self.repo / 'content/untracked.md').write_text('not selected')
        (self.repo / 'content/manuscript.md').write_text('checkout changed')
        with candidate.snapshot(self.repo, revision) as (root, files):
            self.assertEqual(list(files), [Path('content/manuscript.md')])
            self.assertEqual((root / 'content/manuscript.md').read_bytes(), b'original\n')
            self.assertEqual(list(files), [Path('content/manuscript.md')])
            self.assertFalse((root / 'outside-link').exists())
            extracted = root
        self.assertFalse(extracted.exists())

    def test_symlink(self):
        (self.repo / 'content/link').symlink_to('../outside')
        with self.assertRaisesRegex(AssetError, 'regular files'):
            with candidate.snapshot(self.repo, self.commit()):
                pass

    def test_gitlink(self):
        revision = self.commit()
        self.git('update-index', '--add', '--cacheinfo', '160000,' + revision + ',content/dependency')
        self.git('commit', '-qm', 'Submodule')
        with self.assertRaisesRegex(AssetError, 'regular files'):
            with candidate.snapshot(self.repo, self.git('rev-parse', 'HEAD')):
                pass

    def test_individual_and_operator_budgets(self):
        revision = self.commit()
        cases = [(candidate.Budget(blob_bytes=8), 'blob exceeds'),
                 (candidate.Budget(total_bytes=8), 'operational total'),
                 (candidate.Budget(files=0), 'operational file')]
        for budget, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(AssetError, message):
                with candidate.snapshot(self.repo, revision, budget=budget):
                    pass
        with candidate.snapshot(self.repo, revision, budget=candidate.Budget(blob_bytes=9, total_bytes=9, files=1)) as (root, files):
            self.assertEqual(len(files), 1)

    def test_real_20_mib_item_budget_retained(self):
        (self.repo / 'content/oversized.bin').write_bytes(b'x' * (20 * 1024 * 1024 + 1))
        revision = self.commit()
        for snapshot in (stock.snapshot, candidate.snapshot):
            with self.subTest(implementation=snapshot.__module__), self.assertRaisesRegex(AssetError, 'byte budget'):
                with snapshot(self.repo, revision):
                    pass

    def test_invalid_exact_revision(self):
        for revision in ('HEAD', 'A' * 40, 'a' * 39, 'a' * 41):
            with self.subTest(revision=revision), self.assertRaisesRegex(AssetError, 'full commit'):
                with candidate.snapshot(self.repo, revision):
                    pass

    def test_executable_stored_as_data(self):
        executable = self.repo / 'content/executable.py'
        executable.write_text('raise RuntimeError("must never execute")\n')
        executable.chmod(0o755)
        with candidate.snapshot(self.repo, self.commit()) as (root, files):
            self.assertEqual((root / 'content/executable.py').read_bytes(), executable.read_bytes())

    def test_batch_wrong_identity_kind_missing_and_framing(self):
        revision = self.commit()
        oid = self.git('rev-parse', revision + ':content/manuscript.md').encode()
        original_child = candidate.child
        cases = [b'a' * 40 + b' blob 1\nx\n', oid + b' tree 1\nx\n', oid + b' missing\n', oid + b' blob 1\nxX', oid + b' blob 9999999999\n']
        for response in cases:
            @contextmanager
            def substituted_child(repo, args, budget):
                if args == ['cat-file', '--batch']:
                    code = 'import os; os.read(0,41); os.write(1,' + repr(response) + ')'
                    with stream_process(code, budget) as result:
                        yield result
                else:
                    with original_child(repo, args, budget) as result:
                        yield result
            with self.subTest(response=response), patch.object(candidate, 'child', substituted_child), self.assertRaises(AssetError):
                with candidate.snapshot(self.repo, revision):
                    pass


def run_original_snapshot_contracts():
    spec = importlib.util.spec_from_file_location('original_snapshot_tests', BASE / 'tests/guavajam/test_source_snapshot.py')
    module = importlib.util.module_from_spec(spec)
    def compatible_snapshot(*args, **kwargs):
        return candidate.snapshot(*args, retain_paths=True, **kwargs)
    with patch.object(stock, 'snapshot', compatible_snapshot):
        spec.loader.exec_module(module)
        return unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(module))


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    old = run_original_snapshot_contracts()
    receipt = {'new_checks_run': result.testsRun, 'new_failures': len(result.failures), 'new_errors': len(result.errors), 'original_checks_run': old.testsRun, 'original_failures': len(old.failures), 'original_errors': len(old.errors), 'python': sys.version, 'notes': 'Original source snapshot tests use list-compatible adapter; other GuavaJam modules remain pristine at baseline. Not full source or installed-artifact qualification.'}
    (HERE / 'snapshot-correctness.json').write_text(json.dumps(receipt, indent=2) + '\n')
    raise SystemExit(not (result.wasSuccessful() and old.wasSuccessful()))
