import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from asset_policy import AssetError
from check_profile_history import check_profile_history


class ProfileHistoryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        self.manifest = {"schema": "press.edition.v1", "tag": "works/sample/v1.0.0",
                         "presentation_profile": "press.html.v1"}
        for field, path in [("template", "templates/edition-v1.html"),
                            ("stylesheet", "styles/edition-v1.css")]:
            file = self.root / path
            file.parent.mkdir()
            file.write_text(field)
            self.manifest[field + "_path"] = path
            self.manifest[field + "_sha256"] = hashlib.sha256(file.read_bytes()).hexdigest()
        self.raw = json.dumps(self.manifest).encode()
        self.provenance = self.root / "records/editions/sample/v1.0.0/provenance.json"
        self.provenance.parent.mkdir(parents=True)

    def registration(self, **fields):
        path = self.root / "registrations/editions/sample/v1.0.0.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"provenance_sha256": hashlib.sha256(self.raw).hexdigest(), **fields}
        path.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
        subprocess.run(["git", "add", str(path)], cwd=self.root, check=True)

    def test_profile_edit_before_claim_requires_restoring_reviewed_bytes(self):
        self.registration()
        template = self.root / self.manifest["template_path"]
        template.write_text("edited after staging")
        check_profile_history(self.root)
        self.provenance.write_bytes(self.raw)
        self.registration(issuing=True)
        with self.assertRaisesRegex(AssetError, "restore templates/edition-v1.html"):
            check_profile_history(self.root)
        template.write_text("template")
        check_profile_history(self.root)

    def test_claim_and_receipt_freeze_only_the_files_used(self):
        self.provenance.write_bytes(self.raw)
        for fields in ({"issuing": True}, {"issuing": True, "published_at": "2026-11-05T00:00:00Z"}):
            with self.subTest(fields=fields):
                self.registration(**fields)
                (self.root / "styles/edition-v2.css").write_text("unreleased changes")
                stylesheet = self.root / self.manifest["stylesheet_path"]
                stylesheet.write_text("stylesheet")
                check_profile_history(self.root)
                stylesheet.write_text("edited after claim")
                with self.assertRaisesRegex(AssetError, "restore styles/edition-v1.css"):
                    check_profile_history(self.root)

    def test_first_candidate_must_match_issuing_checkout(self):
        candidate = self.root / "provenance.json"
        candidate.write_bytes(self.raw)
        check_profile_history(self.root, candidate=candidate)
        for field in ("template", "stylesheet"):
            with self.subTest(field=field):
                path = self.root / self.manifest[field + "_path"]
                original = path.read_bytes()
                path.write_bytes(original + b"edited after staging")
                with self.assertRaisesRegex(AssetError, path.name):
                    check_profile_history(self.root, candidate=candidate)
                path.write_bytes(original)

    def test_claim_requires_approved_provenance_for_this_edition(self):
        self.registration(issuing=True)
        with self.assertRaisesRegex(AssetError, "reviewed provenance"):
            check_profile_history(self.root)
        self.provenance.write_bytes(self.raw + b" ")
        with self.assertRaisesRegex(AssetError, "differs from the registration"):
            check_profile_history(self.root)
        self.raw = json.dumps({**self.manifest, "tag": "works/other/v1.0.0"}).encode()
        self.provenance.write_bytes(self.raw)
        self.registration(issuing=True)
        with self.assertRaisesRegex(AssetError, "different edition"):
            check_profile_history(self.root)
