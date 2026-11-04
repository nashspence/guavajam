import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from asset_policy import AssetError
from check_profile_history import check_profile_history


class ProfileHistoryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.release = {"tag_name": "works/sample/v1.0.0", "draft": False, "prerelease": False}
        self.manifest = {"schema": "press.edition.v1", "tag": self.release["tag_name"],
                         "presentation_profile": "press.html.v1"}
        for field, path in [("template", "templates/edition-v1.html"),
                            ("stylesheet", "styles/edition-v1.css")]:
            file = self.root / path
            file.parent.mkdir()
            file.write_text(field)
            self.manifest[field + "_path"] = path
            self.manifest[field + "_sha256"] = hashlib.sha256(file.read_bytes()).hexdigest()
        self.releases = self.enterContext(patch("check_profile_history.release_records",
                                                return_value=[self.release]))
        self.download = self.enterContext(patch("check_profile_history.gh",
                                                return_value=json.dumps(self.manifest)))

    def check(self):
        check_profile_history(self.root, "owner/press")

    def test_unissued_profiles_can_change_in_place(self):
        (self.root / self.manifest["template_path"]).write_text("edited")
        (self.root / self.manifest["stylesheet_path"]).rename(self.root / "renamed.css")
        for records in ([], [dict(self.release, draft=True)], [dict(self.release, prerelease=True)],
                        [dict(self.release, tag_name="tools/v1.0.0")]):
            with self.subTest(records=records):
                self.releases.return_value = records
                self.check()
                self.download.assert_not_called()

    def test_published_edition_freezes_only_the_files_it_used(self):
        (self.root / "styles/edition-v2.css").write_text("unreleased changes")
        self.check()
        (self.root / self.manifest["stylesheet_path"]).write_text("changed issued CSS")
        with self.assertRaises(AssetError):
            self.check()
        (self.root / self.manifest["stylesheet_path"]).write_text("stylesheet")
        (self.root / self.manifest["template_path"]).unlink()
        with self.assertRaises(AssetError):
            self.check()

    def test_first_candidate_must_match_issuing_checkout(self):
        self.releases.return_value = []
        candidate = self.root / "provenance.json"
        candidate.write_text(json.dumps(self.manifest), encoding="utf-8")
        check_profile_history(self.root, "owner/press", candidate=candidate)
        for field in ("template", "stylesheet"):
            with self.subTest(field=field):
                path = self.root / self.manifest[field + "_path"]
                original = path.read_bytes()
                path.write_bytes(original + b"edited after staging")
                with self.assertRaisesRegex(AssetError, path.name):
                    check_profile_history(self.root, "owner/press", candidate=candidate)
                path.write_bytes(original)

    def test_invalid_or_unavailable_provenance_blocks_validation(self):
        for raw in ("invalid json", json.dumps(dict(self.manifest, tag="works/other/v1.0.0")),
                    json.dumps(dict(self.manifest, stylesheet_path="../outside.css"))):
            with self.subTest(raw=raw):
                self.download.return_value = raw
                with self.assertRaises(AssetError):
                    self.check()
        self.download.side_effect = AssetError("missing provenance asset")
        with self.assertRaises(AssetError):
            self.check()
