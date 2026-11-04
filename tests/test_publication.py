"""Pre-publication gate and per-push asset verification tests; never publish."""
import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

import test_assets as fixtures
from asset_policy import AssetError
from publish_edition import release_records, require_source_check, stage_edition
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

    def fake_gh(self, *args, tamper=False):
        self.operations.append(args)
        if args[0] == "api":
            return "[]"
        if args[:2] == ("release", "download"):
            name = args[args.index("--pattern") + 1]
            directory = Path(args[args.index("--dir") + 1])
            shutil.copyfile(self.output / name, directory / name)
            if tamper and name.endswith(".html"):
                (directory / name).write_text("tampered")
        return ""

    def stage(self, *, publish=False, tamper=False):
        self.operations = []
        with patch("publish_edition.require_source_check"), \
             patch("publish_edition.tag_commit", return_value=self.commit), \
             patch("publish_edition.gh", side_effect=lambda *args: self.fake_gh(*args, tamper=tamper)):
            return stage_edition("owner/press", self.output, self.commit, self.tag, publish=publish)

    def test_staging_defaults_to_draft_and_verifies_downloaded_attachments(self):
        self.build()
        self.stage()
        create = [args for args in self.operations if args[:2] == ("release", "create")]
        self.assertEqual(1, len(create))
        self.assertIn("--draft", create[0])
        self.assertEqual(3, sum(args[:2] == ("release", "download") for args in self.operations))
        self.assertFalse(any(args[:2] == ("release", "edit") for args in self.operations))

    def test_publication_requires_explicit_flag_after_all_verifications(self):
        self.build()
        self.stage(publish=True)
        self.assertEqual(("release", "edit"), self.operations[-1][:2])
        self.assertIn("--draft=false", self.operations[-1])

    def test_tampered_draft_cannot_be_published(self):
        self.build()
        with self.assertRaises(AssetError):
            self.stage(publish=True, tamper=True)
        self.assertFalse(any(args[:2] == ("release", "edit") for args in self.operations))

    def test_failed_source_check_prevents_release_mutations(self):
        self.build()
        with patch("publish_edition.require_source_check", side_effect=AssetError("failed check")), \
             patch("publish_edition.gh") as gh:
            with self.assertRaises(AssetError):
                stage_edition("owner/press", self.output, self.commit, self.tag, publish=True)
            gh.assert_not_called()

    def test_wrong_tag_prevents_release_mutations(self):
        self.build()
        with patch("publish_edition.require_source_check"), \
             patch("publish_edition.tag_commit", return_value="a" * 40), \
             patch("publish_edition.gh") as gh:
            with self.assertRaises(AssetError):
                stage_edition("owner/press", self.output, self.commit, self.tag, publish=True)
            gh.assert_not_called()


class RequiredCheckTests(unittest.TestCase):
    def checks(self):
        return [{"id": i + 1, "name": name, "app": {"slug": "github-actions"}, "conclusion": "success"}
                for i, name in enumerate(["Validate manuscript sources", "Verify manuscript assets"])]

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
    for name in PublicationTests.__dict__:
        if name.startswith("test_"):
            suite.addTest(PublicationTests(name))
    return suite
