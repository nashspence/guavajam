"""A candidate gate must bind evidence to unchanged verified publication bytes."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import fitz
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from asset_policy import AssetError
from qualify_edition import PAPERS, qualify_edition, verify_printed_pdf


def pdf(path, paper="A4", *, clipped=False, text="Candidate x"):
    with fitz.open() as document:
        width, height = PAPERS[paper]
        page = document.new_page(width=width, height=height)
        page.insert_text((width - 8 if clipped else 45, 60), text)
        document.save(path)


class CandidatePDFTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_both_actual_paper_sizes_and_mathematical_text_are_checked(self):
        for paper in PAPERS:
            path = self.base / (paper + ".pdf")
            pdf(path, paper)
            self.assertEqual(1, verify_printed_pdf(path, paper, ["x"])["pages"])
            with self.assertRaisesRegex(AssetError, "lost visible tokens"):
                verify_printed_pdf(path, paper, ["missing-math-token"])

    def test_off_page_text_is_not_hidden_by_pdf_extraction_crop(self):
        path = self.base / "clipped.pdf"
        pdf(path, clipped=True, text="WIDE-MATHEMATICS")
        with self.assertRaisesRegex(AssetError, "outside page 1"):
            verify_printed_pdf(path, "A4", [])

    def test_wrong_paper_size_fails(self):
        path = self.base / "wrong.pdf"
        pdf(path, "Letter")
        with self.assertRaisesRegex(AssetError, "paper size"):
            verify_printed_pdf(path, "A4", [])


class CandidateQualificationTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.package = self.base / "edition"; self.package.mkdir()
        self.output = self.base / "qualification"
        self.manifest = {"tag": "works/sample/v1.0.0", "canonical_artifact": "sample-v1.0.0.html",
                         "source_commit": "a" * 40, "builder_commit": "b" * 40,
                         "record_url": "https://press.example.org/works/sample/v1.0.0",
                         "presentation_profile": "press.html.v1", "html_sha256": "c" * 64}
        for name, raw in (("sample-v1.0.0.html", b"candidate"), ("provenance.json", b"provenance"), ("SHA256SUMS", b"checksums")):
            (self.package / name).write_bytes(raw)
        self.verify = self.enterContext(patch("qualify_edition.verify_edition", return_value=self.manifest))
        self.mutation = None
        self.browser_failures = []
        self.wrong_digest = False

    def runner(self, command, **kwargs):
        self.assertEqual(self.verify.call_count, 1, "verification must precede opening HTML in a browser")
        self.assertEqual(command[:1], ["node"])
        request = json.loads(kwargs["input"])
        self.assertEqual(str(self.package / self.manifest["canonical_artifact"]), request["file"])
        report = {"schema": "press.candidate-browser.v1",
                  "provenance_sha256": "f" * 64 if self.wrong_digest else request["provenance_sha256"],
                  "html_sha256": request["html_sha256"], "math_tokens": ["x"], "failures": self.browser_failures,
                  "structure": {}, "print": {paper: {} for paper in PAPERS},
                  "screens": {name: {} for name in ("desktop", "mobile", "mobile-dark", "mobile-enlarged")}}
        (self.output / "browser.json").write_text(json.dumps(report))
        for paper in PAPERS:
            pdf(self.output / (paper + ".pdf"), paper)
        if self.mutation:
            self.mutation()
        return subprocess.CompletedProcess(command, int(bool(self.browser_failures)), "", "")

    def test_report_identifies_exact_proof_and_never_enters_the_package(self):
        before = {p.name: p.read_bytes() for p in self.package.iterdir()}
        report = qualify_edition(self.package, self.output, runner=self.runner)
        self.assertEqual("passed", report["status"])
        self.assertEqual(hashlib.sha256(b"provenance").hexdigest(), report["provenance_sha256"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.package.iterdir()})
        self.assertTrue(report["package_unchanged"])
        self.assertEqual(2, self.verify.call_count)
        self.assertEqual(set(PAPERS), report["print"].keys())

    def test_modified_candidate_fails_even_if_reverification_accepts_it(self):
        self.mutation = lambda: (self.package / "sample-v1.0.0.html").write_bytes(b"replacement")
        with self.assertRaisesRegex(AssetError, "package changed"):
            qualify_edition(self.package, self.output, runner=self.runner)
        report = json.loads((self.output / "report.json").read_text())
        self.assertEqual("failed", report["status"])
        self.assertFalse(report["package_unchanged"])

    def test_browser_failure_is_preserved_in_the_bound_review_report(self):
        self.browser_failures = ["A4: Mathematics exceeds printable width at equation-7"]
        with self.assertRaisesRegex(AssetError, "equation-7"):
            qualify_edition(self.package, self.output, runner=self.runner)
        self.assertEqual(self.browser_failures, json.loads((self.output / "report.json").read_text())["failures"])

    def test_different_proof_cannot_supply_qualification_evidence(self):
        self.wrong_digest = True
        with self.assertRaisesRegex(AssetError, "different candidate bytes"):
            qualify_edition(self.package, self.output, runner=self.runner)

    def test_invalid_package_never_starts_browser_or_creates_evidence(self):
        self.verify.side_effect = AssetError("bad checksum")
        runner = Mock()
        with self.assertRaisesRegex(AssetError, "bad checksum"):
            qualify_edition(self.package, self.output, runner=runner)
        runner.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_output_inside_package_and_existing_output_are_rejected(self):
        for output in (self.package / "review", self.base):
            with self.subTest(output=output), self.assertRaisesRegex(AssetError, "new directory outside"):
                qualify_edition(self.package, output, runner=Mock())

    def test_timeout_leaves_failed_evidence_and_rechecks_the_package(self):
        runner = Mock(side_effect=subprocess.TimeoutExpired("node", 240))
        with self.assertRaisesRegex(AssetError, "timed out"):
            qualify_edition(self.package, self.output, runner=runner)
        self.assertEqual(2, self.verify.call_count)
        self.assertEqual("failed", json.loads((self.output / "report.json").read_text())["status"])


class CandidateWorkflowTests(unittest.TestCase):
    def test_no_verified_proof_upload_or_registration_precedes_candidate_gate(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/prepare-edition.yml").read_text())
        steps = workflow["jobs"]["build"]["steps"]
        qualify = next(n for n, step in enumerate(steps) if "scripts/qualify_edition.py" in step.get("run", ""))
        for n, step in enumerate(steps):
            if step.get("with", {}).get("name") == "verified-edition" or "scripts/issuance.py prepare" in step.get("run", ""):
                self.assertGreater(n, qualify)
                self.assertNotIn("always()", step.get("if", ""))
        evidence = next(step for step in steps if step.get("with", {}).get("name") == "edition-qualification")
        self.assertEqual("always()", evidence["if"])
        self.assertNotIn("edition/", evidence["with"]["path"])


if __name__ == "__main__":
    unittest.main()
