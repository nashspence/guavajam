"""A merged decision changes only the verified release body and can be safely retried."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from asset_policy import AssetError
from sync_editorial_record import affected_tags, sync


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.tag = "works/essay/v1.0.0"
        self.provenance = json.dumps({"tag": self.tag, "initial_release_body": "Reviewed body"}).encode()
        self.provenance_file = self.root / "records/editions/essay/v1.0.0/provenance.json"
        self.provenance_file.parent.mkdir(parents=True)
        self.provenance_file.write_bytes(self.provenance)
        self.record = {"published_at": "2026-11-04T12:00:00Z",
                       "provenance_sha256": hashlib.sha256(self.provenance).hexdigest()}
        self.release = {"id": 42, "draft": False, "prerelease": False, "immutable": True,
                        "tag_name": self.tag, "published_at": self.record["published_at"], "body": "stale"}
        self.writes = []
        self.main = "a" * 40
        self.enterContext(patch("build_edition.git", return_value=self.main))
        self.enterContext(patch("issuance.read_registration", return_value=self.record))
        self.enterContext(patch("publish_edition.api", side_effect=self.api))
        self.enterContext(patch("publish_edition.gh", side_effect=self.gh))
        self.validate_main = self.enterContext(patch("publish_edition.require_source_check"))
        self.verify_package = self.enterContext(patch("audit_assets.verify_public_release"))
        self.verify_reader = self.enterContext(patch("reader_routes.verify_reader_routes"))
        self.enterContext(patch("sync_editorial_record.time.sleep"))

    def api(self, repo, path):
        if path == "git/ref/heads/main":
            return {"object": {"sha": self.main}}
        self.assertIn(path, ("releases/tags/works%2Fessay%2Fv1.0.0", "releases/42"))
        return dict(self.release)

    def gh(self, *args):
        if args[:2] == ("release", "download"):
            (Path(args[-1]) / "provenance.json").write_bytes(self.provenance)
        else:
            self.assertEqual(args[:-1], ("api", "--method", "PATCH", "repos/owner/press/releases/42", "--input"))
            self.verify_package.assert_called()
            self.validate_main.assert_called()
            fields = json.loads(Path(args[-1]).read_text())
            self.assertEqual(set(fields), {"body"})
            self.writes.append(fields)
            self.release.update(fields)

    def run_sync(self):
        sync(self.root, "owner/press", self.tag)

    def test_one_job_per_affected_edition(self):
        paths = (b"notices/essay/v1.0.0/0001.md\0registrations/editions/essay/v1.0.0.json\0"
                 b"notices/other/v2.0.0/0001.md\0records/editions/other/v2.0.0/index.html\0")
        self.assertEqual(affected_tags(paths), [self.tag, "works/other/v2.0.0"])
        with self.assertRaises(AssetError):
            affected_tags(b"notices/essay/not-a-version/0001.md\0")

    def test_workflow_scopes_writes_to_main_and_serializes_each_edition(self):
        path = Path(__file__).resolve().parents[1] / ".github/workflows/sync-editorial-record.yml"
        workflow = yaml.safe_load(path.read_text())
        jobs = workflow["jobs"]
        self.assertEqual(jobs["select"]["if"], "github.ref == 'refs/heads/main'")
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertEqual(jobs["synchronize"]["needs"], "select")
        self.assertIn("matrix.tag", jobs["synchronize"]["concurrency"]["group"])

    def test_merged_withdrawal_syncs_once_and_is_checked_on_retry(self):
        notice = self.root / "notices/essay/v1.0.0/0001.md"
        notice.parent.mkdir(parents=True)
        notice.write_text("2026-11-04 Withdrawal: See the next edition.\n")
        self.run_sync()
        self.run_sync()
        self.assertEqual(len(self.writes), 1)
        self.assertIn("Withdrawal: See the next edition.", self.release["body"])
        self.assertEqual(self.verify_reader.call_count, 2)

    def test_pending_registration_skips_release(self):
        del self.record["published_at"]
        self.run_sync()
        self.verify_package.assert_not_called()
        self.verify_reader.assert_not_called()
        self.assertFalse(self.writes)

    def test_bad_provenance_or_changed_main_blocks_write(self):
        self.main = "b" * 40
        with self.assertRaisesRegex(AssetError, "main moved"):
            self.run_sync()
        self.provenance_file.write_bytes(self.provenance + b" ")
        with self.assertRaisesRegex(AssetError, "provenance differs"):
            self.run_sync()
        self.assertFalse(self.writes)

    def test_unverified_package_blocks_write(self):
        self.verify_package.side_effect = AssetError("unexpected attachment")
        with self.assertRaisesRegex(AssetError, "unexpected attachment"):
            self.run_sync()
        self.assertFalse(self.writes)

    def test_failed_main_history_check_blocks_write(self):
        self.validate_main.side_effect = AssetError("history check failed")
        with self.assertRaisesRegex(AssetError, "history check failed"):
            self.run_sync()
        self.assertFalse(self.writes)

    def test_permanent_reader_failure_blocks_mirror_update(self):
        self.verify_reader.side_effect = AssetError("reader record is stale")
        with self.assertRaisesRegex(AssetError, "reader record is stale"):
            self.run_sync()
        self.assertEqual(len(self.writes), 0)

    def test_publisher_record_is_verified_when_github_refuses_body_write(self):
        with patch("publish_edition.gh", side_effect=AssetError("GitHub denied the release edit")):
            with self.assertRaisesRegex(AssetError, "denied"):
                self.run_sync()
        self.verify_reader.assert_called_once()
