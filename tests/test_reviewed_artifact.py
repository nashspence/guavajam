"""Cross-run promotion must use a reviewed artifact from the trusted staging workflow."""
import hashlib
import json
import sys
import tempfile
import unittest

import yaml
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from asset_policy import AssetError
from reviewed_artifact import download_reviewed, staging_builder, verify_reviewed


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
        self.jobs = [{"name": "build", "conclusion": "success"}]
        self.api = self.enterContext(patch("reviewed_artifact.api", side_effect=self.lookup))
        self.gh = self.enterContext(patch("reviewed_artifact.gh", side_effect=self.download))

    def lookup(self, repo, path):
        self.assertEqual(self.repo, repo)
        if path == "actions/runs/42":
            return self.run
        self.assertEqual("actions/runs/42/jobs?per_page=100", path)
        return {"jobs": self.jobs}

    def download(self, *args):
        self.assertEqual(("run", "download", "42", "--repo", self.repo,
                          "--name", "verified-edition", "--dir", str(self.output)), args)
        self.output.mkdir()
        (self.output / "provenance.json").write_bytes(self.raw)

    def test_original_staging_run_still_works_after_main_moves(self):
        download_reviewed(self.repo, "42", self.digest, self.output)
        self.assertEqual(2, self.api.call_count)
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

    def test_unavailable_original_never_rebuilds_implicitly(self):
        self.gh.side_effect = AssetError("artifact expired")
        with self.assertRaisesRegex(AssetError, "artifact expired"):
            download_reviewed(self.repo, "42", self.digest, self.output)
        self.assertFalse(self.output.exists())
        self.gh.assert_called_once()

    def test_recovered_bytes_require_the_original_staging_builder_and_digest(self):
        self.output.mkdir()
        (self.output / "provenance.json").write_bytes(self.raw)
        builder = staging_builder(self.repo, "42", self.digest)
        verify_reviewed(self.output, self.digest, builder)
        with self.assertRaisesRegex(AssetError, "reviewed digest"):
            verify_reviewed(self.output, "c" * 64, builder)
        with self.assertRaisesRegex(AssetError, "builder differs"):
            verify_reviewed(self.output, self.digest, "b" * 40)
        self.gh.assert_not_called()

    def test_promotion_only_run_cannot_supply_historical_builder(self):
        self.jobs[:] = [{"name": "release", "conclusion": "success"}, {"name": "build", "conclusion": "skipped"}]
        with self.assertRaisesRegex(AssetError, "successful proof build"):
            staging_builder(self.repo, "42", self.digest)

    def test_historical_code_runs_only_in_explicit_read_only_job(self):
        path = Path(__file__).resolve().parents[1] / ".github/workflows/prepare-edition.yml"
        jobs = yaml.safe_load(path.read_text())["jobs"]
        recovery, release = jobs["recover"], jobs["release"]
        self.assertIn("inputs.publish && inputs.rebuild_reviewed_proof", recovery["if"])
        self.assertEqual({"actions": "read", "contents": "read"}, recovery["permissions"])
        self.assertNotIn("environment", recovery)
        self.assertEqual("recover", release["needs"])
        self.assertIn("needs.recover.result == 'success'", release["if"])
        self.assertIn("needs.recover.result == 'skipped'", release["if"])
        self.assertEqual("press-release", release["environment"])
        scripts = "\n".join(step.get("run", "") for step in release["steps"])
        self.assertNotIn("original-builder", scripts)
        self.assertIn("command=download", scripts)
        self.assertIn('if [[ "$REBUILD" == true ]]; then command=verify; fi', scripts)

    def test_invalid_inputs_rejected_before_lookup(self):
        for run, digest in (("", self.digest), ("42/attempts/1", self.digest), ("42", "")):
            with self.subTest(run=run), self.assertRaises(AssetError):
                download_reviewed(self.repo, run, digest, self.output)
        self.api.assert_not_called()
