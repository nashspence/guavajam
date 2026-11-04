"""Cross-run promotion must use a reviewed artifact from the trusted staging workflow."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from asset_policy import AssetError
from reviewed_artifact import download_reviewed


class ReviewedArtifactTests(unittest.TestCase):
    def setUp(self):
        self.repo = "owner/press"
        self.output = Path(self.enterContext(tempfile.TemporaryDirectory())) / "edition"
        sha = "a" * 40
        self.run = {"event": "workflow_dispatch", "conclusion": "success", "head_branch": "main",
                    "path": ".github/workflows/prepare-edition.yml", "head_sha": sha,
                    "head_repository": {"full_name": self.repo}}
        self.raw = json.dumps({"builder_commit": sha}).encode()
        self.digest = hashlib.sha256(self.raw).hexdigest()
        self.api = self.enterContext(patch("reviewed_artifact.api", return_value=self.run))
        self.gh = self.enterContext(patch("reviewed_artifact.gh", side_effect=self.download))

    def download(self, *args):
        self.assertEqual(("run", "download", "42", "--repo", self.repo,
                          "--name", "verified-edition", "--dir", str(self.output)), args)
        self.output.mkdir()
        (self.output / "provenance.json").write_bytes(self.raw)

    def test_original_staging_run_still_works_after_main_moves(self):
        download_reviewed(self.repo, "42", self.digest, self.output)
        self.api.assert_called_once_with(self.repo, "actions/runs/42")
        self.gh.assert_called_once()

    def test_untrusted_or_failed_runs_never_download(self):
        for field, value in (("event", "pull_request"), ("head_branch", "topic"),
                             ("head_repository", {"full_name": "other/press"}),
                             ("path", ".github/workflows/other.yml"), ("conclusion", "failure")):
            with self.subTest(field=field), patch.dict(self.run, {field: value}), self.assertRaises(AssetError):
                download_reviewed(self.repo, "42", self.digest, self.output)
        self.gh.assert_not_called()

    def test_modified_provenance_and_wrong_builder_are_rejected(self):
        for digest, builder in (("f" * 64, self.run["head_sha"]), (self.digest, "b" * 40)):
            with self.subTest(digest=digest), patch.dict(self.run, head_sha=builder), self.assertRaises(AssetError):
                download_reviewed(self.repo, "42", digest, self.output)
            (self.output / "provenance.json").unlink()
            self.output.rmdir()

    def test_invalid_inputs_rejected_before_lookup(self):
        for run, digest in (("", self.digest), ("42/attempts/1", self.digest), ("42", "")):
            with self.subTest(run=run), self.assertRaises(AssetError):
                download_reviewed(self.repo, run, digest, self.output)
        self.api.assert_not_called()
