"""Public release inventory and notices checked by the scheduled archive audit."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from asset_policy import AssetError
from audit_assets import audit, main, verify_public_release
from editorial_notices import render_initial_body


class ArchiveAuditTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        manifest = {"title": "Sample", "tag": "works/sample/v1.0.0", "source_commit": "a" * 40,
                    "rights": "all-rights-reserved", "canonical_artifact": "sample-v1.0.0.html",
                    "assets": [], "record_url": "https://press.example.org/works/sample/v1.0.0"}
        manifest["initial_release_body"] = render_initial_body(manifest)
        names = [manifest["canonical_artifact"], "provenance.json", "SHA256SUMS"]
        self.record_path = Path("registrations/editions/sample/v1.0.0.json")
        self.release = {"tag_name": manifest["tag"], "name": "Sample — v1.0.0", "immutable": True, "draft": False, "prerelease": False, "published_at": "2026-11-04T13:00:00Z",
                        "body": manifest["initial_release_body"],
                        "assets": [{"name": name, "id": i, "state": "uploaded"}
                                   for i, name in enumerate(names, 1)]}
        self.reader_check = self.enterContext(patch("audit_assets.verify_reader_routes"))
        for name, result in (("release_records", [self.release]), ("gh", ""),
                             ("verify_edition", manifest), ("tag_commit", manifest["source_commit"]),
                             ("git_sources", [self.record_path]), ("check_issuance", {}),
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
            self.reader_check.assert_called_once()

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

    def test_unresolved_intent_fails_even_if_remote_release_has_disappeared(self):
        with patch("audit_assets.release_records", return_value=[]), patch("audit_assets.read_registration", return_value={}):
            with self.assertRaisesRegex(AssetError, "Unresolved publication intent.*works/sample/v1.0.0"):
                audit(self.root, "owner/press")

    def test_cancelled_intent_needs_no_public_release(self):
        with patch("audit_assets.release_records", return_value=[]), patch("audit_assets.read_registration", return_value={"cancellation_reason": "Retired"}):
            audit(self.root, "owner/press")

    def test_public_edition_must_have_independent_registration(self):
        with patch("audit_assets.git_sources", return_value=[]):
            with self.assertRaisesRegex(AssetError, "missing issuance record for public release"):
                audit(self.root, "owner/press")

    def test_audit_requires_exact_committed_notices(self):
        notices = ["- 2026-11-04 Correction: Equation 2 uses the real numbers."]
        with patch("audit_assets.edition_notices", return_value=notices):
            with self.assertRaisesRegex(AssetError, "release notes differ"):
                audit(self.root, "owner/press")
            self.release["body"] = "## Editorial notices\n\n" + notices[0] + "\n\n" + self.release["body"]
            audit(self.root, "owner/press")

    def test_public_audit_requires_registered_identifiers_in_body(self):
        with patch("audit_assets.check_issuance", return_value={"identifiers": {"oclc": "42"}}):
            with self.assertRaisesRegex(AssetError, "release notes differ"):
                audit(self.root, "owner/press")
            self.release["body"] = "## Edition identifiers\n\n- OCLC number: `42`\n\n" + self.release["body"]
            audit(self.root, "owner/press")

    def test_public_audit_requires_completed_receipt(self):
        with patch("audit_assets.check_issuance", return_value={}) as check:
            audit(self.root, "owner/press")
        self.assertTrue(check.call_args.kwargs["require_receipt"])

    def test_fresh_unapproved_draft_is_ignored(self):
        self.release["draft"] = True
        with patch("audit_assets.git_sources", return_value=[]), patch("audit_assets.check_issuance") as check:
            audit(self.root, "owner/press")
        check.assert_not_called()
