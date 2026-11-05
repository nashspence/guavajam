"""Exercise the exact Git range that can omit online image fetching in CI."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from verify_source_assets import main, editorial_only_change


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
              patch.dict(os.environ, PRESS_NOTICE_BASE_SHA=self.base, GITHUB_EVENT_NAME=""),
              patch("verify_source_assets.verify_source_assets", side_effect=OSError("origin offline")) as verify):
            self.assertEqual(0, main())
            verify.assert_not_called()
            os.environ["PRESS_NOTICE_BASE_SHA"] = ""
            self.assertEqual(1, main())
            verify.assert_called_once_with(self.root)

    def test_first_branch_push_uses_trusted_main_and_missing_or_mixed_bases_fetch(self):
        self.git("update-ref", "refs/remotes/origin/main", self.base)
        with (patch("verify_source_assets.__file__", str(self.root / "scripts/verify_source_assets.py")),
              patch.dict(os.environ, PRESS_NOTICE_BASE_SHA="0" * 40, GITHUB_EVENT_NAME="push",
                         GITHUB_REF="refs/heads/emergency-notice"),
              patch("verify_source_assets.verify_source_assets", side_effect=OSError("origin offline")) as verify):
            self.assertEqual(0, main())
            verify.assert_not_called()
            # Main and tag pushes keep their event baseline and fail closed when it is unknown.
            for ref in ("refs/heads/main", "refs/tags/works/essay/v1.0.0"):
                with self.subTest(ref=ref):
                    os.environ["GITHUB_REF"] = ref
                    self.assertEqual(1, main())
            os.environ["GITHUB_REF"] = "refs/heads/emergency-notice"
            self.git("update-ref", "-d", "refs/remotes/origin/main")
            self.assertEqual(1, main())
            self.git("update-ref", "refs/remotes/origin/main", self.base)
            (self.root / "works/essay/manuscript.md").write_text("Changed manuscript.\n")
            self.commit()
            self.assertEqual(1, main())
            self.assertEqual(4, verify.call_count)

    def test_incomplete_or_mixed_comparisons_use_the_full_check(self):
        for base in ("", "main", "0" * 40, self.notice):
            with self.subTest(base=base):
                self.assertFalse(editorial_only_change(self.root, base))
        self.git("mv", "works/essay/manuscript.md", "notices/essay/v1.0.0/0002.md")
        self.commit()
        self.assertFalse(editorial_only_change(self.root, self.base))

    def test_claim_receipt_and_later_identifier_updates(self):
        registration = "registrations/editions/essay/v1.0.0.json"
        self.write(registration)
        receipt = {"issuing": True}
        (self.root / registration).write_text(json.dumps(receipt))
        claim = self.commit()
        self.assertFalse(editorial_only_change(self.root, self.notice))
        receipt["published_at"] = "2026-11-04T00:00:00Z"
        (self.root / registration).write_text(json.dumps(receipt))
        self.write("records/editions/essay/v1.0.0/provenance.json")
        published = self.commit()
        self.assertTrue(editorial_only_change(self.root, claim))
        receipt["identifiers"] = {"version_doi": "10.1234/essay.v1"}
        (self.root / registration).write_text(json.dumps(receipt))
        self.commit()
        self.assertTrue(editorial_only_change(self.root, published))
        self.git("rm", registration)
        self.commit()
        self.assertFalse(editorial_only_change(self.root, published))

    def test_stopping_an_existing_issuing_claim_is_metadata_only(self):
        registration = "registrations/editions/essay/v1.0.0.json"
        self.write(registration)
        path = self.root / registration
        path.write_text(json.dumps({"issuing": True}))
        claim = self.commit()
        path.write_text(json.dumps({"issuing": True, "stop_reason": "Incorrect caption"}))
        self.commit()
        self.assertTrue(editorial_only_change(self.root, claim))
        self.write("records/editions/essay/v1.0.0/provenance.json")
        self.commit()
        self.assertFalse(editorial_only_change(self.root, claim))

    def test_work_registration_and_mixed_change(self):
        self.write("registrations/works/essay.json")
        self.commit()
        self.assertTrue(editorial_only_change(self.root, self.notice))
        self.write("templates/edition.html")
        self.commit()
        self.assertFalse(editorial_only_change(self.root, self.notice))

    def test_check_requires_a_notice_and_exact_generated_paths(self):
        self.write("records/works/another/index.html")
        self.commit()
        self.assertFalse(editorial_only_change(self.root, self.notice))
        for file in ("records/worker.js", "asset-policy.json", ".github/workflows/validate-source.yml",
                     "records/editions/essay/v1.0.0/provenance.json", "notices/essay/v1.0.0/0002.md.png"):
            with self.subTest(file=file):
                self.git("reset", "--hard", self.notice)
                self.write(file)
                self.commit()
                self.assertFalse(editorial_only_change(self.root, self.base))
