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
            self.assertEqual([Path('content/manuscript.md')], files)
            self.assertEqual('# Work\n', (root / files[0]).read_text())
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
