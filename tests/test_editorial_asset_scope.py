"""Exercise the exact Git range that can omit online image fetching in CI."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from verify_source_assets import main, notice_only_change


class NoticeAssetCheckTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.git("init", "-q")
        self.git("config", "user.name", "Press test")
        self.git("config", "user.email", "press@example.invalid")
        self.write("works/essay/manuscript.md")
        self.base = self.commit()
        self.write("notices/essay/v1.0.0/0001.md")
        self.write("records/editions/essay/v1.0.0/index.html")
        self.write("records/works/essay/index.html")
        self.notice = self.commit()

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, text=True).strip()

    def write(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name + "\n")

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "Test change")
        return self.git("rev-parse", "HEAD")

    def test_notice_merge_does_not_fetch_images_but_full_check_does(self):
        with (patch("verify_source_assets.__file__", str(self.root / "scripts/verify_source_assets.py")),
              patch.dict(os.environ, PRESS_NOTICE_BASE_SHA=self.base),
              patch("verify_source_assets.verify_source_assets", side_effect=OSError("origin offline")) as verify):
            self.assertEqual(0, main())
            verify.assert_not_called()
            os.environ["PRESS_NOTICE_BASE_SHA"] = ""
            self.assertEqual(1, main())
            verify.assert_called_once_with(self.root)

    def test_incomplete_or_mixed_comparisons_use_the_full_check(self):
        for base in ("", "main", "0" * 40, self.notice):
            with self.subTest(base=base):
                self.assertFalse(notice_only_change(self.root, base))
        self.git("mv", "works/essay/manuscript.md", "notices/essay/v1.0.0/0002.md")
        self.commit()
        self.assertFalse(notice_only_change(self.root, self.base))

    def test_check_requires_a_notice_and_exact_generated_paths(self):
        self.write("records/works/another/index.html")
        self.commit()
        self.assertFalse(notice_only_change(self.root, self.notice))
        for file in ("records/worker.js", "asset-policy.json", ".github/workflows/validate-source.yml",
                     "records/editions/essay/v1.0.0/provenance.json", "notices/essay/v1.0.0/0002.md.png"):
            with self.subTest(file=file):
                self.git("reset", "--hard", self.notice)
                self.write(file)
                self.commit()
                self.assertFalse(notice_only_change(self.root, self.base))
