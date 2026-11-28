"""The publication snapshot is exactly content/, independent of checkout policy."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from guavajam.asset_policy import AssetError
from guavajam.source_snapshot import snapshot


class SourceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.test')
        (self.root / 'content').mkdir()
        (self.root / 'content/manuscript.md').write_text('# Work\n')

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, stderr=subprocess.DEVNULL).decode().strip()

    def commit(self):
        self.git('add', '-A')
        self.git('commit', '-qm', 'Ordinary Git commit')
        return self.git('rev-parse', 'HEAD')

    def test_snapshot_excludes_infrastructure_untracked_files_and_external_symlinks(self):
        (self.root / 'README.md').write_text('Infrastructure\n')
        (self.root / 'outside-link').symlink_to('/does/not/exist')
        revision = self.commit()
        (self.root / 'content/untracked.md').write_text('Not published\n')
        (self.root / 'content/manuscript.md').write_text('Uncommitted changes\n')
        with snapshot(self.root, revision) as (root, files):
            self.assertEqual([Path('content/manuscript.md')], list(files))
            self.assertEqual('# Work\n', (root / next(iter(files))).read_text())
            self.assertFalse((root / 'README.md').exists())
            self.assertFalse((root / 'outside-link').is_symlink())

    def test_git_attributes_do_not_remove_or_rewrite_signed_source(self):
        (self.root / '.gitattributes').write_text('content/manuscript.md export-ignore\ncontent/data.txt filter=hostile\n')
        (self.root / 'content/data.txt').write_text('Unfiltered data\n')
        revision = self.commit()
        self.git('config', 'filter.hostile.smudge', 'exit 99')
        with snapshot(self.root, revision) as (root, files):
            self.assertIn(Path('content/manuscript.md'), files)
            self.assertEqual('Unfiltered data\n', (root / 'content/data.txt').read_text())

    def test_source_symlink_cannot_include_bytes_outside_content(self):
        (self.root / 'README.md').write_text('Outside the work\n')
        (self.root / 'content/linked.md').symlink_to('../README.md')
        with self.assertRaisesRegex(AssetError, 'regular files'):
            with snapshot(self.root, self.commit()):
                pass

    def test_source_submodule_is_not_a_self_contained_source_blob(self):
        first = self.commit()
        self.git('update-index', '--add', '--cacheinfo', '160000,' + first + ',content/dependency')
        self.git('commit', '-qm', 'Gitlink inside source')
        with self.assertRaisesRegex(AssetError, 'regular files'):
            with snapshot(self.root, self.git('rev-parse', 'HEAD')):
                pass

    def test_archive_bundle_excludes_unrelated_private_branch_and_preserves_source_refs(self):
        from guavajam.source_archive import bundle_revision
        revision = self.commit()
        self.git('switch', '-qc', 'private-draft')
        (self.root / 'private-material.txt').write_text('Not selected for the archive\n')
        private_revision = self.commit()
        original_refs = self.git('show-ref')
        bundle = self.root / 'selected.bundle'
        bundle_revision(self.root, revision, bundle)
        self.assertEqual(original_refs, self.git('show-ref'))
        restored = self.root / 'restored.git'
        self.git('clone', '--bare', '--quiet', str(bundle), str(restored))
        self.assertEqual(revision, self.git('--git-dir', str(restored), 'rev-parse', 'HEAD'))
        missing = subprocess.run(['git', '--git-dir', str(restored), 'cat-file', '-e', private_revision], capture_output=True)
        self.assertNotEqual(0, missing.returncode)

    def test_executable_supplement_is_preserved_as_data(self):
        path = self.root / 'content/calculation.py'
        path.write_text('raise RuntimeError("must not execute")\n')
        path.chmod(0o755)
        with snapshot(self.root, self.commit()) as (root, files):
            self.assertIn(Path('content/calculation.py'), files)
            self.assertEqual(path.read_bytes(), (root / 'content/calculation.py').read_bytes())

    def test_many_sources_use_one_tree_and_one_framed_blob_process(self):
        from unittest.mock import patch
        import guavajam.source_snapshot as implementation
        for number in range(275):
            (self.root / 'content' / ('record-' + str(number).zfill(4) + '.txt')).write_text('Record ' + str(number) + '\n')
        revision = self.commit()
        original = implementation.subprocess.Popen
        commands = []
        def start(command, *args, **kwargs):
            commands.append(command)
            return original(command, *args, **kwargs)
        with patch.object(implementation.subprocess, 'Popen', side_effect=start):
            with snapshot(self.root, revision) as (root, files):
                self.assertEqual(276, len(files))
                self.assertEqual(276, len(list(files)))
                self.assertEqual(list(files), list(files))
                self.assertEqual('Record 274\n', (root / 'content/record-0274.txt').read_text())
        self.assertEqual(2, len(commands))
        self.assertIn('ls-tree', commands[0])
        self.assertEqual('--batch', commands[1][-1])

    def test_total_snapshot_larger_than_old_aggregate_limit_is_streamed(self):
        data = b'bounded snapshot fixture\n' * (1024 * 40)
        for number in range(22):
            with (self.root / 'content' / ('data-' + str(number) + '.bin')).open('wb') as stream:
                for _ in range(13):
                    stream.write(data)
        revision = self.commit()
        with snapshot(self.root, revision) as (root, files):
            total = sum((root / path).stat().st_size for path in files)
            self.assertGreater(total, 256 * 1024 * 1024)
            self.assertEqual((self.root / 'content/data-0.bin').stat().st_size,
                             (root / 'content/data-21.bin').stat().st_size)
        with self.assertRaisesRegex(AssetError, 'explicit file/byte operation budget'):
            with snapshot(self.root, revision, maximum_bytes=1024):
                pass

    def test_individual_size_file_count_and_deadline_fail_precisely_and_clean_children(self):
        from unittest.mock import patch
        import guavajam.source_snapshot as implementation
        (self.root / 'content/another.txt').write_text('Another source\n')
        revision = self.commit()
        with self.assertRaisesRegex(AssetError, 'explicit file/byte operation budget'):
            with snapshot(self.root, revision, maximum_files=1):
                pass
        original = implementation.subprocess.Popen
        children = []
        def start(command, *args, **kwargs):
            process = original(command, *args, **kwargs)
            children.append(process)
            return process
        with patch.object(implementation.subprocess, 'Popen', side_effect=start):
            with self.assertRaisesRegex(AssetError, 'deadline'):
                with snapshot(self.root, revision, timeout=0.000001):
                    pass
        self.assertTrue(children)
        self.assertTrue(all(child.poll() is not None for child in children))
        with (self.root / 'content/oversized.bin').open('wb') as stream:
            stream.truncate(20 * 1024 * 1024 + 1)
        with self.assertRaisesRegex(AssetError, '20-MiB individual limit'):
            with snapshot(self.root, self.commit()):
                pass

    def test_truncated_or_mismatched_batch_header_is_rejected_and_child_reaped(self):
        from unittest.mock import patch
        import sys
        import guavajam.source_snapshot as implementation
        revision = self.commit()
        original = implementation.subprocess.Popen
        for payload in ["b'wrong blob 8\\n'", "b'incomplete'"]:
            children = []
            def start(command, *args, **kwargs):
                if command[-1] == '--batch':
                    command = [sys.executable, '-c',
                               "import sys;sys.stdin.buffer.readline();sys.stdout.buffer.write(" + payload + ");sys.stdout.buffer.flush()"]
                process = original(command, *args, **kwargs)
                children.append(process)
                return process
            with patch.object(implementation.subprocess, 'Popen', side_effect=start):
                with self.assertRaisesRegex(AssetError, 'identity, type or size differs|truncated frame'):
                    with snapshot(self.root, revision):
                        pass
            self.assertTrue(all(child.poll() is not None for child in children))
