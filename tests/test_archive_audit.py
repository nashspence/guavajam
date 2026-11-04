"""Public release inventory and notices checked by the scheduled archive audit."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from asset_policy import AssetError
from audit_assets import audit, main, verify_public_release
from publish_edition import edition_fields


class ArchiveAuditTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        manifest = {"title": "Sample", "tag": "works/sample/v1.0.0", "source_commit": "a" * 40,
                    "rights": "all-rights-reserved", "canonical_artifact": "sample-v1.0.0.html",
                    "assets": []}
        names = [manifest["canonical_artifact"], "provenance.json", "SHA256SUMS"]
        self.record_path = Path("registrations/editions/sample/v1.0.0.json")
        self.release = {"tag_name": manifest["tag"], "draft": False, "prerelease": False, "published_at": "2026-11-04T13:00:00Z",
                        "body": "\n".join(f"{k}: {v}" for k, v in edition_fields(manifest).items()),
                        "assets": [{"name": name, "id": i, "state": "uploaded"}
                                   for i, name in enumerate(names, 1)]}
        for name, result in (("release_records", [self.release]), ("gh", ""),
                             ("verify_edition", manifest), ("tag_commit", manifest["source_commit"]),
                             ("git_sources", [self.record_path]), ("check_issuance", None),
                             ("read_registration", {"published_at": self.release["published_at"]})):
            self.enterContext(patch("audit_assets." + name, return_value=result))

    def test_extra_public_attachment(self):
        self.release["assets"].append({"name": "draft.pdf", "id": 4, "state": "uploaded"})
        with self.assertRaisesRegex(AssetError, "unexpected or incomplete release attachment"):
            audit(self.root, "owner/press")

    def test_public_rights_contradict_verified_manifest(self):
        self.release["body"] += "\nRights: CC0-1.0"
        with self.assertRaisesRegex(AssetError, "release notes differ"):
            audit(self.root, "owner/press")

    def test_missing_public_attachment(self):
        self.release["assets"].pop()
        with self.assertRaisesRegex(AssetError, "missing release attachments"):
            audit(self.root, "owner/press")

    def test_release_id_reads_one_published_release(self):
        with patch.object(sys, "argv", ["audit_assets.py", "--repo", "owner/press", "--release-id", "123"]), \
             patch("audit_assets.api", return_value=self.release) as api, \
             patch("audit_assets.release_records") as releases:
            self.assertEqual(0, main())
            api.assert_called_once_with("owner/press", "releases/123")
            releases.assert_not_called()

    def test_explicit_verification_rejects_draft_and_prerelease(self):
        for state in ("draft", "prerelease"):
            with self.subTest(state=state), self.assertRaisesRegex(AssetError, "published official edition"):
                verify_public_release("owner/press", dict(self.release, **{state: True}))

    def test_registered_release_disappears(self):
        with patch("audit_assets.release_records", return_value=[]):
            with self.assertRaisesRegex(AssetError, "missing registered edition: works/sample/v1.0.0"):
                audit(self.root, "owner/press")

    def test_registered_release_becomes_prerelease(self):
        self.release["prerelease"] = True
        with self.assertRaisesRegex(AssetError, "missing registered edition"):
            audit(self.root, "owner/press")

    def test_unpublished_authorization_is_reported_without_draft_access(self):
        output = StringIO()
        with patch("audit_assets.release_records", return_value=[]), \
             patch("audit_assets.read_registration", return_value={}), redirect_stdout(output):
            audit(self.root, "owner/press")
        self.assertIn("Pending publication: works/sample/v1.0.0", output.getvalue())

    def test_public_edition_must_have_independent_registration(self):
        with patch("audit_assets.git_sources", return_value=[]):
            with self.assertRaisesRegex(AssetError, "missing issuance record for public release"):
                audit(self.root, "owner/press")

    def test_audit_requires_exact_committed_notices(self):
        notices = ["- 2026-11-04 Correction: Equation 2 uses the real numbers."]
        with patch("audit_assets.edition_notices", return_value=notices):
            with self.assertRaisesRegex(AssetError, "release notes differ"):
                audit(self.root, "owner/press")
            self.release["body"] += "\n\n## Editorial notices\n\n" + notices[0]
            audit(self.root, "owner/press")

    def test_public_audit_requires_completed_receipt(self):
        with patch("audit_assets.check_issuance") as check:
            audit(self.root, "owner/press")
        self.assertTrue(check.call_args.kwargs["require_receipt"])

    def test_fresh_unapproved_draft_is_ignored(self):
        self.release["draft"] = True
        with patch("audit_assets.git_sources", return_value=[]), patch("audit_assets.check_issuance") as check:
            audit(self.root, "owner/press")
        check.assert_not_called()
