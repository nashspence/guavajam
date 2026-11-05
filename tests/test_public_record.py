"""A receipted publisher record can be regenerated and served from either origin."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from asset_policy import AssetError
from editorial_notices import render_initial_body
from issuance import authorization
from public_record import check_records, record_html, write_records


class PublicRecordTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.git("init", "-q")
        (self.root / "edition-origin.txt").write_text("https://press.example.org\n")
        self.tag = "works/essay/v1.0.0"
        self.timestamp = "2026-11-04T12:00:00Z"
        self.url = "https://press.example.org/" + self.tag
        self.manifest = dict(title="Scale < 2 & change", author="Nash Spence", language="en",
                             work_identifiers={"concept_doi": "10.1234/essay"}, tag=self.tag,
                             canonical_artifact="essay-v1.0.0.html", source_commit="a" * 40,
                             html_sha256="b" * 64, record_url=self.url, rights="all-rights-reserved",
                             initial_note="Plate I & caption.\n\nSecond paragraph.\n")
        self.manifest["initial_release_body"] = render_initial_body(self.manifest, self.manifest["initial_note"])
        self.package = self.root / "dist"
        self.package.mkdir()
        (self.package / "provenance.json").write_text(json.dumps(self.manifest))
        self.receipt = {**authorization(self.package, self.manifest), "published_at": self.timestamp}
        self.registration = self.root / "registrations/editions/essay/v1.0.0.json"
        self.registration.parent.mkdir(parents=True)
        self.save_receipt()
        self.directory = self.root / "records/editions/essay/v1.0.0"
        self.page = self.directory / "index.html"

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)

    def save_receipt(self):
        self.registration.write_text(json.dumps(self.receipt, sort_keys=True, indent=2) + "\n")
        self.git("add", str(self.registration))

    def generate(self):
        with patch("public_record.verify_edition", return_value=self.manifest) as verify:
            write_records(self.root, self.tag, package=self.package)
            verify.assert_called_once_with(self.package, tag=self.tag, commit="a" * 40)

    def test_portable_readable_record(self):
        html = record_html(self.manifest, published_at=self.timestamp, identifiers={"oclc": "12345"},
                           notices=["- 2026-11-05 Withdrawal: Check A & B."]).decode()
        for text in ("Scale &lt; 2 &amp; change", "Nash Spence", "10.1234/essay", self.timestamp,
                     "<p>Plate I &amp; caption.</p>", "<p>Second paragraph.</p>", "a" * 40,
                     "<li>OCLC number: 12345</li>", "<li>2026-11-05 Withdrawal: Check A &amp; B.</li>",
                     f'href="{self.url}/read"'):
            self.assertIn(text, html)
        for name in ("essay-v1.0.0.html", "SHA256SUMS", "provenance.json"):
            href = "v1.0.0/" + name
            self.assertIn(f'href="{href}"', html)
            for base in (self.url, "https://backup.example.org/archive/" + self.tag):
                self.assertEqual(urljoin(base, href), base + "/" + name)

    def test_reconcile_from_package_then_from_saved_provenance(self):
        self.generate()
        with self.assertRaisesRegex(AssetError, "tracked pages"):
            check_records(self.root)
        self.git("add", "records")
        check_records(self.root)
        notices = self.root / "notices/essay/v1.0.0"
        notices.mkdir(parents=True)
        (notices / "0001.md").write_text("2026-11-05 Correction: Attribution updated.\n")
        self.receipt["identifiers"] = {"oclc": "42"}
        self.save_receipt()
        with self.assertRaisesRegex(AssetError, "stale"):
            check_records(self.root)
        write_records(self.root, self.tag)
        check_records(self.root)
        self.assertIn("Attribution updated.", self.page.read_text())
        self.assertIn("OCLC number: 42", self.page.read_text())
        (self.directory / "provenance.json").write_text("{}\n")
        with self.assertRaisesRegex(AssetError, "provenance differs"):
            write_records(self.root, self.tag)

    def test_work_listing_tracks_receipts_and_preserves_version_citations(self):
        self.generate()
        landing = self.root / "records/works/essay/index.html"
        original = landing.read_bytes()
        for version, extra in (("v3.0.0", {}), ("v4.0.0", {"cancellation_reason": "Retired"})):
            intent = {**self.receipt, "version": version, **extra}
            del intent["published_at"]
            path = self.registration.with_name(version + ".json")
            path.write_text(json.dumps(intent, sort_keys=True, indent=2) + "\n")
        self.git("add", "registrations", "records")
        check_records(self.root)
        notices = self.root / "notices/essay/v1.0.0"
        notices.mkdir(parents=True)
        (notices / "0001.md").write_text("2026-11-06 Withdrawal: An error affects the proof.\n")
        write_records(self.root, self.tag)
        check_records(self.root)
        self.assertEqual(landing.read_bytes(), original)
        self.assertIn("Withdrawal: An error", self.page.read_text())

        for version in ("v1.10.0", "v1.2.0"):
            tag = "works/essay/" + version
            manifest = dict(self.manifest, tag=tag, title="Revised essay", canonical_artifact=f"essay-{version}.html",
                            record_url="https://press.example.org/" + tag)
            manifest["initial_release_body"] = render_initial_body(manifest)
            (self.package / "provenance.json").write_text(json.dumps(manifest))
            receipt = {**authorization(self.package, manifest), "published_at": self.timestamp}
            path = self.registration.with_name(version + ".json")
            path.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
            self.git("add", str(path))
            with patch("public_record.verify_edition", return_value=manifest):
                write_records(self.root, tag, package=self.package)
        self.git("add", "records")
        check_records(self.root)
        html = landing.read_text()
        self.assertLess(html.index("v1.10.0"), html.index("v1.2.0"))
        self.assertLess(html.index("v1.2.0"), html.index("v1.0.0"))
        self.assertNotIn("v3.0.0", html)
        self.assertNotIn("v4.0.0", html)
        self.assertIn("Scale &lt; 2 &amp; change", html)
        self.assertIn("Revised essay", html)
        canonical = "https://press.example.org/works/essay"
        self.assertIn(f'<link rel="canonical" href="{canonical}">', html)
        for base in (canonical, "https://backup.example.org/archive/works/essay"):
            href = "essay/v1.0.0"
            self.assertIn(f'href="{href}"', html)
            self.assertEqual(urljoin(base, href), base + "/v1.0.0")
            self.assertIn('href="../essay"', self.page.read_text())
            self.assertEqual(urljoin(base + "/v1.0.0", "../essay"), base)

        landing.write_bytes(original)
        with self.assertRaisesRegex(AssetError, "stale work record"):
            check_records(self.root)
        (self.root / "edition-origin.txt").write_text("https://different.example.org\n")
        with self.assertRaisesRegex(AssetError, "different permanent work address"):
            check_records(self.root)

    def test_no_work_page_before_first_receipt_or_through_symlink(self):
        del self.receipt["published_at"]
        self.save_receipt()
        check_records(self.root)
        work = self.root / "records/works/essay"
        work.mkdir(parents=True)
        (work / "index.html").write_text("draft listing")
        with self.assertRaisesRegex(AssetError, "tracked pages"):
            check_records(self.root)
        (work / "index.html").unlink()
        work.rmdir()
        work.symlink_to(self.package, target_is_directory=True)
        self.receipt["published_at"] = self.timestamp
        self.save_receipt()
        with self.assertRaisesRegex(AssetError, "symlink"):
            self.generate()
        self.assertFalse((self.package / "index.html").exists())

    def test_pending_stray_and_symlink_paths_fail(self):
        del self.receipt["published_at"]
        self.save_receipt()
        with self.assertRaisesRegex(AssetError, "receipted edition"):
            self.generate()
        self.directory.mkdir(parents=True)
        self.page.write_text("unapproved")
        with self.assertRaisesRegex(AssetError, "tracked pages"):
            check_records(self.root)
        self.page.unlink()
        self.directory.rmdir()
        self.directory.symlink_to(self.package, target_is_directory=True)
        self.receipt["published_at"] = self.timestamp
        self.save_receipt()
        with self.assertRaisesRegex(AssetError, "symlink"):
            self.generate()
        self.assertFalse((self.package / "index.html").exists())
