"""Pre-publication gate and per-push asset verification tests; never publish."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_assets as fixtures
from asset_policy import AssetError
from publish_edition import gh, release_records, require_source_check, publish_edition
from verify_source_assets import verify_source_assets


class PublicationTests(fixtures.EditionTests):
    def test_push_check_downloads_and_verifies_unique_references(self):
        fetcher = unittest.mock.Mock(return_value=(fixtures.SVG, "image/svg+xml"))
        self.assertEqual(1, verify_source_assets(self.repo, fetcher=fetcher))
        fetcher.assert_called_once()
        with self.assertRaisesRegex(AssetError, "SHA-256"):
            verify_source_assets(self.repo, fetcher=lambda *_: (fixtures.SVG + b" ", "image/svg+xml"))

    def test_push_check_rejects_unapproved_destination_before_network(self):
        path = self.repo / "works/sample/manuscript.md"
        path.write_text(path.read_text().replace(fixtures.URL, "https://evil.test/image.svg"))
        fetcher = unittest.mock.Mock()
        with self.assertRaises(AssetError):
            verify_source_assets(self.repo, fetcher=fetcher)
        fetcher.assert_not_called()

class PublicationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.commit = "a" * 40
        self.main_tip = self.commit
        self.tag = "works/sample/v1.0.0"
        self.ref = None
        self.release = None
        self.remote = {}
        self.operations = []
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.output = Path(temp.name)
        manifest = {"title": "Test", "rights": "Reserved", "tag": self.tag,
                    "source_commit": self.commit, "canonical_artifact": "sample-v1.0.0.html"}
        for name in [manifest["canonical_artifact"], "provenance.json", "SHA256SUMS"]:
            (self.output / name).write_text(name)
        for name, options in [("verify_edition", {"return_value": manifest}),
                              ("require_source_check", {}), ("check_issuance", {"return_value": {}}),
                              ("tag_commit", {"side_effect": lambda *_: self.ref}),
                              ("gh", {"side_effect": self.fake_gh})]:
            mock = patch("publish_edition." + name, **options)
            setattr(self, name, mock.start())
            self.addCleanup(mock.stop)

    def fake_gh(self, *args):
        self.operations.append(args)
        if args[:2] == ("api", "--paginate"):
            return json.dumps([self.release] if self.release else [])
        if args[:3] == ("api", "--method", "POST"):
            params = dict(value.split("=", 1) for value in args[5::2])
            self.release = {"id": 123, "tag_name": params["tag_name"], "draft": True,
                            "prerelease": False, "target_commitish": params["target_commitish"],
                            "assets": [], "body": params["body"], "name": params["name"]}
        elif args[:3] == ("api", "--method", "PATCH"):
            self.assertEqual("repos/owner/press/releases/123", args[3])
            self.assertIn("target_commitish=" + self.commit, args)
            self.release["draft"] = False
            self.release["immutable"] = True
            self.ref = self.release["target_commitish"]
        elif args == ("api", "repos/owner/press/git/ref/heads/main"):
            return json.dumps({"object": {"sha": self.main_tip}})
        elif args[0] == "api":
            self.assertEqual("repos/owner/press/releases/123", args[1])
        elif args[:2] == ("release", "upload"):
            for path in map(Path, args[3:args.index("--repo")]):
                self.remote[path.name] = path.read_bytes()
                self.release["assets"].append({"id": len(self.remote), "name": path.name, "state": "uploaded"})
        elif args[:2] == ("release", "download"):
            name = args[args.index("--pattern") + 1]
            (Path(args[args.index("--dir") + 1]) / name).write_bytes(self.remote[name])
        else:
            self.fail(f"unexpected gh request {args}")
        return json.dumps(self.release)

    def publish(self, issuer=None):
        return publish_edition("owner/press", self.output, self.commit, self.tag,
                               issuer_commit=issuer or self.commit)

    def existing_draft(self):
        """Seed an already-uploaded draft left by an interrupted promotion."""
        from editorial_notices import release_notes
        manifest = self.verify_edition.return_value
        self.remote = {path.name: path.read_bytes() for path in self.output.iterdir()}
        self.release = {"id": 123, "tag_name": self.tag, "draft": True, "prerelease": False,
                        "target_commitish": self.commit, "body": release_notes(manifest),
                        "name": "Test — v1.0.0", "assets": [
                            {"id": idx, "name": name, "state": "uploaded"}
                            for idx, name in enumerate(self.remote, 1)]}

    def test_fresh_publication_reads_back_draft_and_public_bytes(self):
        self.publish()
        self.assertEqual(self.commit, self.ref)
        promotion = next(i for i, args in enumerate(self.operations) if "PATCH" in args)
        self.assertEqual(("api", "repos/owner/press/git/ref/heads/main"), self.operations[promotion - 1])
        for operations in [self.operations[:promotion], self.operations[promotion+1:]]:
            self.assertEqual(3, sum(args[:2] == ("release", "download") for args in operations))

    def test_initial_notes_and_issuance_are_required_before_any_publish(self):
        self.verify_edition.return_value["initial_notes"] = "Plate I used by permission.\n"
        self.existing_draft()
        self.assertTrue(self.release["body"].endswith("\n\nPlate I used by permission."))
        with patch.dict(self.release, {"body": self.release["body"].replace("used by permission", "is public domain")}):
            with self.assertRaisesRegex(AssetError, "release notes differ"):
                self.publish()
        self.check_issuance.side_effect = AssetError("unapproved")
        with self.assertRaisesRegex(AssetError, "unapproved"):
            self.publish()
        self.assertFalse(any("PATCH" in args for args in self.operations))

    def test_approved_later_notices_are_checked_on_public_retry(self):
        from editorial_notices import edition_notices, release_notes
        self.existing_draft()
        self.publish()
        directory = self.output / "notices/sample/v1.0.0"
        directory.mkdir(parents=True)
        note = "2026-10-10 Correction: corrected attribution"
        (directory / "0001.md").write_text(note + "\n")
        notices = edition_notices(self.output, self.tag)
        self.assertEqual(["- " + note], notices)
        with patch("publish_edition.edition_notices", return_value=notices):
            with self.assertRaisesRegex(AssetError, "release notes differ"):
                self.publish()
            self.release["body"] = release_notes(self.verify_edition.return_value, notices)
            self.publish()
        (directory / "0003.md").write_text(note + "\n")
        with self.assertRaisesRegex(AssetError, "unexpected notice file"):
            edition_notices(self.output, self.tag)

    def test_later_identifiers_are_required_on_public_retry(self):
        from editorial_notices import release_notes
        self.publish()
        initial = self.release["body"]
        identifiers = {"version_doi": "10.1234/x_(a);42", "oclc": "1234"}
        self.check_issuance.return_value = {"identifiers": identifiers, "published_at": "2026-11-04T12:00:00Z"}
        with self.assertRaisesRegex(AssetError, "release notes differ"):
            self.publish()
        self.release["body"] = release_notes(self.verify_edition.return_value, identifiers=identifiers)
        self.assertEqual(initial + "\n\n## Edition identifiers\n\n- OCLC number: `1234`"
                         "\n- Edition DOI: `10.1234/x_(a);42`", self.release["body"])
        self.publish()
        self.assertEqual(initial, release_notes(self.verify_edition.return_value))

    def test_promotion_sends_reviewed_initial_prose(self):
        self.verify_edition.return_value["initial_notes"] = "Plate I © Artist.\n\nUsed by permission.\n"
        self.existing_draft()
        approved = self.release["body"]
        self.publish()
        request = next(args for args in self.operations if "PATCH" in args)
        self.assertIn("body=" + approved, request)
        self.assertIn("Plate I © Artist.\n\nUsed by permission.", approved)

    def test_uncertain_promotion_recovers_without_a_second_write(self):
        self.existing_draft()
        def lose_response(*args):
            result = self.fake_gh(*args)
            if "PATCH" in args:
                raise AssetError("connection closed")
            return result
        self.gh.side_effect = lose_response
        self.publish()
        self.publish()
        self.assertEqual(1, sum("PATCH" in args for args in self.operations))
        self.assertFalse(self.release["draft"])
        self.remote["SHA256SUMS"] = b"wrong"
        with self.assertRaisesRegex(AssetError, "attachments differ"):
            self.publish()

    def test_uncertain_promotion_still_draft(self):
        self.existing_draft()
        def never_applied(*args):
            if "PATCH" in args:
                raise AssetError("connection closed")
            return self.fake_gh(*args)
        self.gh.side_effect = never_applied
        with self.assertRaisesRegex(AssetError, "remains a draft"):
            self.publish()

    def test_interrupted_promotion_completes_matching_draft_without_overwriting(self):
        self.existing_draft()
        missing = self.release["assets"].pop()["name"]
        self.publish()
        uploads = [args for args in self.operations if args[:2] == ("release", "upload")]
        self.assertEqual(1, len(uploads))
        self.assertEqual([str(self.output / missing)], list(uploads[0][3:uploads[0].index("--repo")]))
        self.assertFalse(self.release["draft"])

    def test_partial_draft_with_conflicting_bytes_is_left_intact(self):
        self.existing_draft()
        self.release["assets"].pop()
        existing = self.release["assets"][0]["name"]
        self.remote[existing] = b"different proof"
        with self.assertRaisesRegex(AssetError, "attachments differ"):
            self.publish()
        self.assertFalse(any(args[:2] == ("release", "upload") for args in self.operations))
        self.assertTrue(self.release["draft"])

    def test_no_draft_created_without_issuance(self):
        self.check_issuance.side_effect = AssetError("unapproved")
        with self.assertRaisesRegex(AssetError, "unapproved"):
            self.publish()
        self.assertIsNone(self.release)

    def test_public_readback_requires_exact_title_and_immutable_assets(self):
        self.publish()
        for field, value, message in [("name", "Different work", "release title"),
                                      ("immutable", False, "immutable GitHub release")]:
            with self.subTest(field=field), patch.dict(self.release, {field: value}):
                with self.assertRaisesRegex(AssetError, message):
                    self.publish()

    def test_promotion_requires_full_issuer_sha(self):
        for issuer in [None, "main"]:
            with self.subTest(issuer=issuer), self.assertRaisesRegex(AssetError, "full issuer commit"):
                publish_edition("owner/press", self.output, self.commit, self.tag, issuer_commit=issuer)
        self.assertEqual([], self.operations)

    def test_moved_main_prevents_promotion(self):
        self.existing_draft()
        self.main_tip = "b" * 40
        with self.assertRaisesRegex(AssetError, "re-dispatch promotion"):
            self.publish()
        self.assertTrue(self.release["draft"])
        self.assertFalse(any("PATCH" in args for args in self.operations))

    def test_main_moves_during_reviewed_byte_readback(self):
        def move_main(*args):
            result = self.fake_gh(*args)
            if args[:2] == ("release", "download"):
                self.main_tip = "b" * 40
            return result
        self.gh.side_effect = move_main
        with self.assertRaisesRegex(AssetError, "re-dispatch promotion"):
            self.publish()
        self.assertTrue(self.release["draft"])
        self.assertFalse(any("PATCH" in args for args in self.operations))

    def test_distinct_issuer_must_have_passing_checks(self):
        self.existing_draft()
        self.main_tip = "b" * 40
        self.require_source_check.reset_mock()
        self.require_source_check.side_effect = [None, AssetError("issuer check failed")]
        with self.assertRaisesRegex(AssetError, "issuer check failed"):
            self.publish(issuer=self.main_tip)
        self.assertEqual([(self.commit,), (self.main_tip,)],
                         [call.args[1:] for call in self.require_source_check.call_args_list])
        self.assertTrue(self.release["draft"])

    def test_invalid_drafts_cannot_be_promoted(self):
        self.existing_draft()
        for field, value in [("assets", self.release["assets"] + [{"id": 4, "name": "stray.pdf", "state": "uploaded"}]),
                             ("body", self.release["body"] + "Rights: other terms\n"),
                             ("name", "Other"), ("target_commitish", "main"), ("draft", False), ("prerelease", True)]:
            with self.subTest(field=field), patch.dict(self.release, {field: value}), self.assertRaises(AssetError):
                self.publish()
        self.remote["SHA256SUMS"] = b"modified"
        with self.assertRaisesRegex(AssetError, "attachments differ"):
            self.publish()
        self.assertTrue(self.release["draft"])
        self.assertFalse(any("PATCH" in args for args in self.operations))

    def test_draft_changes_during_download_are_rejected(self):
        self.existing_draft()
        def replace_attachment(*args):
            result = self.fake_gh(*args)
            if args[:2] == ("release", "download"):
                self.release["assets"][0]["id"] += 1
            return result
        self.gh.side_effect = replace_attachment
        with self.assertRaisesRegex(AssetError, "changed during verification"):
            self.publish()
        self.assertTrue(self.release["draft"])

    def test_postpublication_readback_reports_public_failure(self):
        self.existing_draft()
        def corrupt_after_promotion(*args):
            result = self.fake_gh(*args)
            if "PATCH" in args:
                self.remote["SHA256SUMS"] = b"wrong"
            return result
        self.gh.side_effect = corrupt_after_promotion
        with self.assertRaisesRegex(AssetError, "publication submitted.*attachments differ"):
            self.publish()

    def test_failed_source_check_and_wrong_tag_prevent_mutations(self):
        self.require_source_check.side_effect = AssetError("failed check")
        with self.assertRaisesRegex(AssetError, "failed check"):
            self.publish()
        self.require_source_check.side_effect = None
        self.ref = "b" * 40
        with self.assertRaisesRegex(AssetError, "different commit"):
            self.publish()
        self.assertEqual([], self.operations)


class RequiredCheckTests(unittest.TestCase):
    def checks(self):
        return [{"id": i + 1, "name": name, "app": {"slug": "github-actions"}, "conclusion": "success"}
                for i, name in enumerate(["Validate manuscript sources", "Verify manuscript assets"])]

    def test_gh_errors_report_operation_and_http_status_only(self):
        result = subprocess.CompletedProcess([], 1, "", "gh: private detail (HTTP 403)")
        with patch("publish_edition.subprocess.run", return_value=result), self.assertRaises(AssetError) as caught:
            gh("api", "--method", "PATCH", "private-request")
        self.assertEqual("gh api PATCH failed (HTTP 403)", str(caught.exception))

    def test_paginated_release_inventory_works_with_existing_gh(self):
        with patch("publish_edition.gh", return_value='[{"id":1}]\n[{"id":2}]\n'):
            self.assertEqual([{"id":1},{"id":2}], release_records("owner/press"))

    def test_both_checks_required_at_reviewed_commit(self):
        checks = self.checks()
        with patch("publish_edition.api", side_effect=[{"status": "ahead"}, {"check_runs": checks}]):
            require_source_check("owner/press", "a" * 40)
        with patch("publish_edition.api", side_effect=[{"status": "ahead"}, {"check_runs": checks[:1]}]):
            with self.assertRaisesRegex(AssetError, "Verify manuscript assets"):
                require_source_check("owner/press", "a" * 40)

    def test_latest_failed_check_cannot_use_older_success(self):
        checks = self.checks()
        checks.append({**checks[1], "id": 99, "conclusion": "failure"})
        with patch("publish_edition.api", side_effect=[{"status": "identical"}, {"check_runs": checks}]):
            with self.assertRaises(AssetError):
                require_source_check("owner/press", "a" * 40)

    def test_commit_must_belong_to_main(self):
        with patch("publish_edition.api", return_value={"status": "diverged"}):
            with self.assertRaises(AssetError):
                require_source_check("owner/press", "a" * 40)


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(RequiredCheckTests))
    suite.addTests(loader.loadTestsFromTestCase(PublicationLifecycleTests))
    for name in PublicationTests.__dict__:
        if name.startswith("test_"):
            suite.addTest(PublicationTests(name))
    return suite
