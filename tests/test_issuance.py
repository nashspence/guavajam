"""The issuer's edition registration pins the bytes approved after staging."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from asset_policy import AssetError
from identifier_schema import validate_registrations
from issuance import authorization, check_issuance, public_receipt


class IssuanceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.package = self.root / "package"
        self.package.mkdir()
        self.manifest = {"title": "Example", "tag": "works/example/v1.2.0", "html_sha256": "f" * 64,
                         "source_commit": "a" * 40, "canonical_artifact": "example-v1.2.0.html",
                         "rights": "all-rights-reserved", "record_url": "https://press.example.org/works/example/v1.2.0"}
        (self.package / "provenance.json").write_text(json.dumps(self.manifest) + "\n")
        self.record = authorization(self.package, self.manifest)
        self.path = Path("registrations/editions/example/v1.2.0.json")
        (self.root / self.path).parent.mkdir(parents=True)

    def write(self):
        (self.root / self.path).write_text(json.dumps(self.record, indent=2, sort_keys=True) + "\n")

    def check(self, **options):
        check_issuance(self.root, self.package, self.manifest, **options)

    def test_generated_record_validates_in_separate_issuer_checkout(self):
        self.write()
        work = Path("works/example/manuscript.md")
        self.assertEqual([], validate_registrations(self.root, {self.path, work}, {work: {}}))
        self.assertEqual(self.record["provenance_sha256"],
                         hashlib.sha256((self.package / "provenance.json").read_bytes()).hexdigest())
        self.check()
        self.check(pending=True)

    def test_receipt_cannot_omit_or_corrupt_a_review_commitment(self):
        work = Path("works/example/manuscript.md")
        original = {**self.record, "published_at": "2026-11-04T13:00:00Z"}
        for key in ("source_commit", "initial_notes_sha256", "provenance_sha256", "artifact_sha256"):
            for value in (None, "xyz"):
                with self.subTest(key=key, value=value):
                    self.record = dict(original)
                    if value is None:
                        del self.record[key]
                    else:
                        self.record[key] = value
                    self.write()
                    errors = validate_registrations(self.root, {self.path, work}, {work: {}})
                    self.assertTrue(any(key in error for error in errors))

    def test_approval_digest_binds_edition_specific_rights_prose(self):
        self.manifest["initial_notes"] = "Plate I used by permission.\n"
        self.record = authorization(self.package, self.manifest)
        self.write()
        self.manifest["initial_notes"] = "Plate I is in the public domain.\n"
        with self.assertRaisesRegex(AssetError, "initial_notes_sha256 differs"):
            self.check()

    def test_absent_record_and_substituted_package_fail(self):
        with self.assertRaisesRegex(AssetError, "missing issuance"):
            self.check()
        self.write()
        (self.package / "provenance.json").write_text("replacement")
        with self.assertRaisesRegex(AssetError, "provenance_sha256 differs"):
            self.check()

    def test_initial_prose_cannot_override_reserved_rights(self):
        manifest = {**self.manifest, "initial_notes": "Rights: contradictory terms\n"}
        with self.assertRaisesRegex(AssetError, "reserved release field"):
            authorization(self.package, manifest)

    def test_record_fields_must_match_package(self):
        original = dict(self.record)
        for key in original:
            with self.subTest(key=key):
                self.record = {**original, key: "incorrect"}
                self.write()
                with self.assertRaisesRegex(AssetError, f"{key} differs"):
                    self.check()

    def test_publication_receipt_before_and_after_confirmation(self):
        self.write()
        timestamp = "2026-11-01T10:20:00Z"
        self.check(published_at=timestamp)
        with self.assertRaisesRegex(AssetError, "record the public release timestamp"):
            self.check(published_at=timestamp, require_receipt=True)
        self.record["published_at"] = timestamp
        self.write()
        self.check(published_at=timestamp, require_receipt=True)
        with self.assertRaisesRegex(AssetError, "published_at differs"):
            self.check(published_at="2026-11-01T10:20:01Z")
        with self.assertRaisesRegex(AssetError, "reverted to draft"):
            self.check(pending=True)

    def test_receipt_comes_from_verified_public_edition(self):
        self.write()
        release = {'tag_name': self.manifest['tag'], 'published_at': '2026-11-04T12:00:00Z'}
        with patch('publish_edition.api', return_value=release), patch('audit_assets.verify_public_release') as verify:
            result = public_receipt(self.root, self.package, self.manifest, 'owner/press')
            self.assertEqual(result, {**self.record, 'published_at': release['published_at']})
            verify.assert_called_once_with('owner/press', release, root=self.root)
            verify.side_effect = AssetError('readback failed')
            with self.assertRaisesRegex(AssetError, 'readback failed'):
                public_receipt(self.root, self.package, self.manifest, 'owner/press')

    def test_cancelled_intent_is_not_promotable(self):
        self.record['cancellation_reason'] = 'Permission was declined.'
        self.write()
        with self.assertRaisesRegex(AssetError, 'cancelled'):
            self.check(pending=True)
