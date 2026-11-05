"""The issuer's edition registration pins the bytes approved after staging."""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from asset_policy import AssetError
from editorial_notices import edition_notices, release_notes, render_initial_body
from identifier_schema import validate_registrations
from issuance import authorization, check_issuance, public_receipt
from reader_routes import RecordText


class IssuanceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.package = self.root / "package"
        self.package.mkdir()
        self.manifest = {"title": "Example", "tag": "works/example/v1.2.0", "html_sha256": "f" * 64,
                         "source_commit": "a" * 40, "canonical_artifact": "example-v1.2.0.html",
                         "rights": "all-rights-reserved", "record_url": "https://press.example.org/works/example/v1.2.0"}
        self.manifest["initial_release_body"] = render_initial_body(self.manifest)
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
        self.manifest["initial_release_body"] = render_initial_body(self.manifest, "Plate I used by permission.\n")
        self.record = authorization(self.package, self.manifest)
        self.write()
        self.manifest["initial_release_body"] = render_initial_body(self.manifest, "Plate I is in the public domain.\n")
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
        with self.assertRaisesRegex(AssetError, "reserved release field"):
            render_initial_body(self.manifest, "Rights: contradictory terms\n")

    def test_new_renderer_can_still_read_and_annotate_an_old_receipt(self):
        old_body = self.manifest["initial_release_body"]
        self.record["published_at"] = "2026-11-04T12:00:00Z"
        self.write()
        with patch("editorial_notices.edition_fields", side_effect=AssertionError("re-rendered old body")):
            self.check(require_receipt=True)
            self.assertEqual(release_notes(self.manifest), old_body)
            current = release_notes(self.manifest, ["- 2026-11-05 Withdrawal: See replacement."],
                                    identifiers={"oclc": "42"})
        self.assertTrue(current.startswith("## Editorial notices\n\n- 2026-11-05 Withdrawal: See replacement."))
        self.assertTrue(current.endswith("## Edition identifiers\n\n- OCLC number: `42`\n\n" + old_body))

    def test_cannot_authorize_edition_missing_the_literal_reviewed_body(self):
        del self.manifest["initial_release_body"]
        with self.assertRaisesRegex(AssetError, "exact reviewed initial release body"):
            authorization(self.package, self.manifest)

    def test_unclosed_markdown_and_false_official_sections_are_rejected(self):
        directory = self.root / "notices/example/v1.2.0"
        directory.mkdir(parents=True)
        for markup in ("<!--", "<details>", "```", "~~~gfm", "## Editorial notices", "    indented code"):
            with self.subTest(markup=markup):
                with self.assertRaises(AssetError):
                    render_initial_body(self.manifest, markup + "\n")
                (directory / "0001.md").write_text("2026-11-05 Correction: " + markup + "\n")
                with self.assertRaises(AssetError):
                    edition_notices(self.root, self.manifest["tag"])
        with self.assertRaises(AssetError):
            render_initial_body(self.manifest, "Edition identifiers\n===\n")

    def test_initial_prose_list_markers_remain_literal_in_the_reader_record(self):
        for prose in ("- 2026-11-04 Withdrawal: Quoted historical text.\n",
                      "  + Edition DOI: a quoted example\n", "1. OCLC number: 42\n",
                      "123456789. An ordered example\n", "1) Another example\n",
                      "* A literal example\n"):
            with self.subTest(prose=prose):
                manifest = {**self.manifest, "initial_release_body": render_initial_body(self.manifest, prose)}
                notice = "2026-11-05 Correction: The actual editorial notice."
                rendered = subprocess.check_output(
                    ["pandoc", "--from=gfm", "--to=html"],
                    input=release_notes(manifest, ["- " + notice], identifiers={"oclc": "42"}), text=True)
                parser = RecordText()
                parser.feed(rendered)
                self.assertEqual(parser.items, [notice, "OCLC number: 42"])
                self.assertIn(" ".join(prose.split()), " ".join("".join(parser.text).split()))

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
        with (patch('publish_edition.api', return_value=release), patch('audit_assets.verify_public_release') as verify,
              patch('reader_routes.verify_reader_routes') as readers):
            result = public_receipt(self.root, self.package, self.manifest, 'owner/press')
            self.assertEqual(result, {**self.record, 'published_at': release['published_at']})
            verify.assert_called_once_with('owner/press', release, root=self.root)
            readers.assert_called_once_with(self.manifest, 'owner/press', notices=[], identifiers=None,
                                            published_at=release['published_at'], pending=True)
            self.record = result
            self.write()
            public_receipt(self.root, self.package, self.manifest, 'owner/press')
            self.assertFalse(readers.call_args.kwargs['pending'])
            verify.side_effect = AssetError('readback failed')
            with self.assertRaisesRegex(AssetError, 'readback failed'):
                public_receipt(self.root, self.package, self.manifest, 'owner/press')

    def test_cancelled_intent_is_not_promotable(self):
        self.record['cancellation_reason'] = 'Permission was declined.'
        self.write()
        with self.assertRaisesRegex(AssetError, 'cancelled'):
            self.check(pending=True)
