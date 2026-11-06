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
from public_record import check_records, record_html, work_projection, write_records


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
                             html_sha256="b" * 64, html_byte_count=12345, record_url=self.url, rights="all-rights-reserved",
                             initial_note="Plate I & caption.\n\nSecond paragraph.\n")
        self.manifest["initial_release_body"] = render_initial_body(self.manifest, self.manifest["initial_note"])
        self.package = self.root / "dist"
        self.package.mkdir()
        (self.package / "provenance.json").write_text(json.dumps(self.manifest))
        self.receipt = {**authorization(self.package, self.manifest), "issuing": True, "published_at": self.timestamp}
        self.registration = self.root / "registrations/editions/essay/v1.0.0.json"
        self.registration.parent.mkdir(parents=True)
        self.save_receipt()
        self.work_registration = self.root / "registrations/works/essay.json"
        self.work_registration.parent.mkdir()
        self.save_work({"concept_doi": "10.1234/essay"})
        self.directory = self.root / "records/editions/essay/v1.0.0"
        self.page = self.directory / "index.html"

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)

    def save_receipt(self):
        self.registration.write_text(json.dumps(self.receipt, sort_keys=True, indent=2) + "\n")
        self.git("add", str(self.registration))

    def save_work(self, identifiers, **fields):
        record = {"schema": "press.work-registration.v1", "work": "essay", "identifiers": identifiers, **fields}
        self.work_registration.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
        self.git("add", str(self.work_registration))

    def generate(self):
        with patch("public_record.verify_edition", return_value=self.manifest) as verify:
            write_records(self.root, self.tag, package=self.package)
            verify.assert_called_once_with(self.package, tag=self.tag, commit="a" * 40)

    def issue_version(self, version, **fields):
        tag = "works/essay/" + version
        manifest = dict(self.manifest, tag=tag, title="Revised essay", canonical_artifact=f"essay-{version}.html",
                        record_url="https://press.example.org/" + tag)
        manifest.update(fields)
        manifest["initial_release_body"] = render_initial_body(manifest)
        (self.package / "provenance.json").write_text(json.dumps(manifest))
        receipt = {**authorization(self.package, manifest), "issuing": True, "published_at": self.timestamp}
        path = self.registration.with_name(version + ".json")
        path.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
        self.git("add", str(path))
        with patch("public_record.verify_edition", return_value=manifest):
            write_records(self.root, tag, package=self.package)

    def test_portable_readable_record(self):
        html = record_html(self.manifest, published_at=self.timestamp, identifiers={"oclc": "12345", "version_doi": "10.1234/essay.v1"},
                           notices=["- 2026-11-05 Withdrawal: Check A & B."]).decode()
        for text in ("Scale &lt; 2 &amp; change", "Nash Spence", "10.1234/essay", self.timestamp,
                     "<p>Plate I &amp; caption.</p>", "<p>Second paragraph.</p>", "a" * 40,
                     "<li>OCLC number: 12345</li>", "<li>2026-11-05 Withdrawal: Check A &amp; B.</li>",
                     'href="v1.0.0/read"', "(12,345 bytes)", "Concept DOI at issuance",
                     f'<link rel="canonical" href="{self.url}">', f'<a href="{self.url}">{self.url}</a>',
                     "Nash Spence. <cite>Scale &lt; 2 &amp; change</cite>. v1.0.0. Press, 2026-11-04.",
                     '<a href="https://doi.org/10.1234/essay.v1">https://doi.org/10.1234/essay.v1</a>'):
            self.assertIn(text, html)
        for name in ("essay-v1.0.0.html", "SHA256SUMS", "provenance.json"):
            href = "v1.0.0/" + name
            self.assertIn(f'href="{href}"', html)
            for base in (self.url, "https://backup.example.org/archive/" + self.tag):
                self.assertEqual(urljoin(base, href), base + "/" + name)

    def test_edition_landing_prioritizes_identity_status_access_and_citation(self):
        html = record_html(self.manifest, published_at=self.timestamp,
                           identifiers={"version_doi": "10.1234/essay.v1"}, notices=[]).decode()
        self.assertEqual(1, html.count("<h1>"))
        self.assertIn('<header class="press-record-header">', html)
        self.assertIn('<p class="press-record-authors">Nash Spence</p>', html)
        self.assertIn('Edition v1.0.0 · <strong>Published</strong>', html)
        self.assertIn('<p class="press-record-identifier">Edition DOI: '
                      '<a href="https://doi.org/10.1234/essay.v1">'
                      'https://doi.org/10.1234/essay.v1</a></p>', html)
        self.assertLess(html.index('<h1>'), html.index('id="press-record-read"'))
        self.assertLess(html.index('id="press-record-read"'), html.index('<h2>Cite this edition</h2>'))
        self.assertLess(html.index('<h2>Cite this edition</h2>'), html.index('<h2>Edition details</h2>'))
        self.assertIn('<dl class="press-record-details">', html)
        # #45's published identifiers and bibliographic facts remain unchanged.
        self.assertIn(f'<link rel="canonical" href="{self.url}">', html)
        self.assertIn('<meta name="DCTERMS.issued" content="' + self.timestamp + '">', html)

    def test_withdrawn_edition_keeps_advisory_notices_before_access(self):
        html = record_html(self.manifest, published_at=self.timestamp,
                           identifiers={}, notices=['- 2026-11-05 Withdrawal: Do not rely on this edition.']).decode()
        self.assertLess(html.index('<strong>Withdrawn</strong>'),
                        html.index('<h2>Editorial notices</h2>'))
        self.assertLess(html.index('Withdrawal: Do not rely on this edition.'),
                        html.index('id="press-record-read"'))
        self.assertIn('Read the archived edition', html)
        self.assertIn('href="v1.0.0/read"', html)

    def test_work_landing_uses_reviewed_identity_and_keeps_edition_history(self):
        self.generate()
        self.issue_version('v2.0.0', title='A later edition', author='Other author')
        html = (self.root / 'records/works/essay/index.html').read_text()
        self.assertIn('<h1>Scale &lt; 2 &amp; change</h1>', html)
        self.assertIn('<p class="press-record-authors">Nash Spence</p>', html)
        self.assertIn('Current concept DOI: <a href="https://doi.org/10.1234/essay">'
                      'https://doi.org/10.1234/essay</a>', html)
        self.assertLess(html.index('<h2 id="press-editions">Editions</h2>'),
                        html.index('<h2>Cite this work</h2>'))
        self.assertLess(html.index('href="essay/v2.0.0"'), html.index('href="essay/v1.0.0"'))
        self.assertIn('Press, 2026.', html)

    def test_record_css_retains_reading_palette_without_remote_dependencies(self):
        from public_record import PAGE_STYLE
        self.assertIn('grid-template-columns: minmax(8rem, 33%) minmax(0, 1fr)', PAGE_STYLE)
        self.assertIn('@media (max-width: 36rem)', PAGE_STYLE)
        self.assertIn('@media (prefers-color-scheme: dark)', PAGE_STYLE)
        self.assertIn('@media print', PAGE_STYLE)
        self.assertIn('outline: 3px solid var(--press-focus)', PAGE_STYLE)
        self.assertNotIn('@import', PAGE_STYLE)
        self.assertNotIn('url(', PAGE_STYLE)
        self.assertNotIn('@font-face', PAGE_STYLE)

    def test_reviewed_title_language_is_local_to_visible_title_parts(self):
        title = 'Échelle < 2 & changements "révisés"'
        escaped = 'Échelle &lt; 2 &amp; changements &quot;révisés&quot;'
        manifest = dict(self.manifest, title=title, language="fr-CA")
        page = record_html(manifest, published_at=self.timestamp, identifiers={}, notices=[]).decode()
        authored = '<span lang="fr-CA">' + escaped + '</span>'
        self.assertIn('<html lang="en">', page)
        self.assertIn('<h1>' + authored + '</h1>', page)
        self.assertIn('<cite>' + authored + '</cite>. v1.0.0. Press, 2026-11-04.', page)
        self.assertIn('<dt>Work</dt><dd>' + authored + '</dd>', page)
        self.assertIn('<p>Press · Edition record</p>', page)
        self.assertIn('<a href="' + self.url + '">' + self.url + '</a>', page)
        self.assertEqual(page.count('lang="fr-CA"'), 3)
        english = record_html(self.manifest, published_at=self.timestamp, identifiers={}, notices=[]).decode()
        self.assertIn('<h1>Scale &lt; 2 &amp; change</h1>', english)
        self.assertNotIn('<span lang=', english)
        legacy = dict(self.manifest); del legacy["language"]
        self.assertNotIn('<span lang=', record_html(legacy, published_at=self.timestamp, identifiers={}, notices=[]).decode())

    def test_public_title_language_rejects_invalid_attributes(self):
        for language in ('fr-CA" onclick="bad', '<script>', '', 'fr_CA', None, 1, []):
            with self.subTest(language=language), self.assertRaisesRegex(AssetError, 'reviewed manuscript language'):
                record_html(dict(self.manifest, language=language), published_at=self.timestamp, identifiers={}, notices=[])

    def test_work_listing_uses_each_editions_own_title_language(self):
        self.generate()
        self.issue_version('v2.0.0', title='Méthode < 2 & suite', language='fr-CA')
        self.git('add', 'records'); check_records(self.root)
        landing = (self.root / 'records/works/essay/index.html').read_text()
        self.assertIn('<html lang="en">', landing)
        self.assertIn('<a href="essay/v2.0.0"><span lang="fr-CA">Méthode &lt; 2 &amp; suite</span> — v2.0.0</a>', landing)
        self.assertIn('<a href="essay/v1.0.0">Scale &lt; 2 &amp; change — v1.0.0</a>', landing)
        self.assertEqual(landing.count('lang="fr-CA"'), 1)
        french = (self.root / 'records/editions/essay/v2.0.0/index.html').read_text()
        self.assertIn('<h1><span lang="fr-CA">Méthode &lt; 2 &amp; suite</span></h1>', french)
        self.assertIn('<html lang="en">', french)
        self.assertIn('href="v2.0.0/read"', french)
        self.assertIn('<h1>Scale &lt; 2 &amp; change</h1>', self.page.read_text())

    def test_citation_follows_the_current_edition_doi_and_keeps_its_address(self):
        for identifiers in ({"version_doi": "10.1234/essay.v1"}, {}):
            with self.subTest(identifiers=identifiers):
                html = record_html(self.manifest, published_at=self.timestamp,
                                   identifiers=identifiers, notices=[]).decode()
                citation = html.split("<h2>Cite this edition</h2>")[1].split("</p>", 1)[0]
                self.assertIn(f'<a href="{self.url}">{self.url}</a>', citation)
                self.assertEqual('href="https://doi.org/10.1234/essay.v1"' in citation, bool(identifiers))
                self.assertIn('10.1234/essay · <a href="../essay">Current work DOI and history</a>', html)

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

    def test_generated_pages_fit_the_reader_and_checkpoint_limit(self):
        self.generate()
        notices = self.root / "notices/essay/v1.0.0"
        notices.mkdir(parents=True)
        # Escaping increases the size of the rendered record beyond the notice's text.
        (notices / "0001.md").write_text("2026-11-05 Correction: " + "&" * 20 + "\n")
        original = self.page.read_bytes()
        with patch("asset_policy.MAX_RECORD_BYTES", len(original)), self.assertRaisesRegex(AssetError, "size limit"):
            write_records(self.root, self.tag)
        self.assertEqual(original, self.page.read_bytes())

        work = (self.root / "records/works/essay/index.html").read_bytes()
        receipts = {"v1.0.0": self.receipt}
        with patch("asset_policy.MAX_RECORD_BYTES", len(work)):
            work_projection(self.root, "essay", receipts)
        with patch("asset_policy.MAX_RECORD_BYTES", len(work) - 1), self.assertRaisesRegex(AssetError, "size limit"):
            work_projection(self.root, "essay", receipts, write=True)

    def test_work_listing_tracks_receipts_and_preserves_version_citations(self):
        self.generate()
        landing = self.root / "records/works/essay/index.html"
        original = landing.read_bytes()
        for version, extra in (("v3.0.0", {}), ("v4.0.0", {"cancellation_reason": "Retired", "cancellation_run_id": "42"})):
            intent = {**self.receipt, "version": version, **extra}
            del intent["published_at"]
            del intent["issuing"]
            path = self.registration.with_name(version + ".json")
            path.write_text(json.dumps(intent, sort_keys=True, indent=2) + "\n")
        self.git("add", "registrations", "records")
        check_records(self.root)
        notices = self.root / "notices/essay/v1.0.0"
        notices.mkdir(parents=True)
        (notices / "0001.md").write_text("2026-11-06 Withdrawal: An error affects the proof.\n")
        write_records(self.root, self.tag)
        check_records(self.root)
        self.assertIn("All editions of this work have been withdrawn", landing.read_text())
        self.assertIn("<strong>Withdrawn</strong>", landing.read_text())
        self.assertIn("Withdrawal: An error", self.page.read_text())

        for version in ("v1.10.0", "v1.2.0"):
            self.issue_version(version)
        self.git("add", "records")
        check_records(self.root)
        successor_notice = self.root / "notices/essay/v1.2.0/0001.md"
        successor_notice.parent.mkdir(parents=True)
        for target in ("v1.2.0", "v3.0.0", "v1.0.1", "bad"):
            successor_notice.write_text(f"2026-11-06 Supersession: Revised. [replacement: {target}]\n")
            with self.assertRaisesRegex(AssetError, "replacement"):
                write_records(self.root, "works/essay/v1.2.0")
        successor_notice.write_text("2026-11-06 Supersession: Revised. [replacement: v1.10.0]\n")
        write_records(self.root, "works/essay/v1.2.0")
        html = landing.read_text()
        self.assertNotIn("All editions of this work have been withdrawn", html)
        self.assertIn("<strong>Superseded</strong>", html)
        self.assertIn('href="essay/v1.10.0">Replacement v1.10.0', html)
        self.assertLess(html.index("v1.10.0"), html.index("v1.2.0"))
        self.assertLess(html.index("v1.2.0"), html.index("v1.0.0"))
        self.assertNotIn("v3.0.0", html)
        self.assertNotIn("v4.0.0", html)
        self.assertIn("Scale &lt; 2 &amp; change", html)
        self.assertIn("Revised essay", html)
        canonical = "https://press.example.org/works/essay"
        self.assertIn(f'<link rel="canonical" href="{canonical}">', html)
        self.assertIn(f'<a href="{canonical}">{canonical}</a>', html)
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

    def test_withdrawal_can_point_to_earlier_edition_and_rejects_cycles(self):
        self.generate()
        self.issue_version("v2.0.0")
        landing = self.root / "records/works/essay/index.html"
        latest = self.root / "notices/essay/v2.0.0/0001.md"
        latest.parent.mkdir(parents=True)
        latest.write_text("2026-11-06 Withdrawal: Use the earlier edition. [replacement: v1.0.0]\n")
        write_records(self.root, "works/essay/v2.0.0")
        self.assertIn('href="essay/v1.0.0">Replacement v1.0.0</a>', landing.read_text())
        edition = (self.root / "records/editions/essay/v2.0.0/index.html").read_text()
        read = edition.index("Read the archived edition")
        self.assertLess(edition.index("<strong>Withdrawn</strong>"), read)
        self.assertLess(edition.index('href="v1.0.0">Check the status of v1.0.0'), read)
        for host in ("https://press.example.org", "https://backup.example.org/archive"):
            self.assertEqual(urljoin(host + "/works/essay/v2.0.0", "v1.0.0"), host + "/works/essay/v1.0.0")
        earlier = self.root / "notices/essay/v1.0.0/0001.md"
        earlier.parent.mkdir(parents=True)
        earlier.write_text("2026-11-07 Withdrawal: Both editions share the problem.\n")
        write_records(self.root, self.tag)
        self.assertIn('href="essay/v1.0.0">Replacement v1.0.0 (Withdrawn)', landing.read_text())
        earlier.write_text("2026-11-07 Withdrawal: See the second edition. [replacement: v2.0.0]\n")
        with self.assertRaisesRegex(AssetError, "replacement links form a cycle"):
            write_records(self.root, self.tag)

    def test_withdrawal_and_reinstatement_of_a_replacement(self):
        self.generate()
        self.issue_version("v2.0.0")
        notices = self.root / "notices/essay"
        earlier = notices / "v1.0.0"
        earlier.mkdir(parents=True)
        (earlier / "0001.md").write_text("2026-11-06 Supersession: Revised. [replacement: v2.0.0]\n")
        write_records(self.root, self.tag)
        self.git("add", "records")
        archived = self.page.read_text()
        self.assertIn("The editorial notice named v2.0.0 as the replacement.", archived)
        self.assertIn('<a href="v2.0.0">Check the status of v2.0.0</a>', archived)
        self.assertIn('<a href="../essay">view all editions of this work</a>', archived)

        latest = notices / "v2.0.0"
        latest.mkdir()
        landing = self.root / "records/works/essay/index.html"
        for step, decision, label, no_current in (
                ("0001", "Withdrawal: Figures under review.", "Replacement v2.0.0 (Withdrawn)", True),
                ("0002", "Reinstatement: Figures verified.", "Replacement v2.0.0", False)):
            with self.subTest(decision=decision):
                (latest / (step + ".md")).write_text(f"2026-11-07 {decision}\n")
                write_records(self.root, "works/essay/v2.0.0")
                check_records(self.root)
                work = landing.read_text()
                self.assertEqual("This work has no current edition." in work, no_current)
                self.assertNotIn("All editions of this work have been withdrawn", work)
                self.assertIn(f'<a href="essay/v2.0.0">{label}</a>', work)
                self.assertEqual(self.page.read_text(), archived)

    def test_reinstatement_is_explicit_and_preserves_the_notice_history(self):
        self.generate()
        self.issue_version("v2.0.0")
        notices = self.root / "notices/essay/v1.0.0"
        notices.mkdir(parents=True)
        first = notices / "0001.md"
        first.write_text("2026-11-06 Reinstatement: Decision reviewed.\n")
        with self.assertRaisesRegex(AssetError, "requires a withdrawn or superseded edition"):
            write_records(self.root, self.tag)
        for kind, status in (("Withdrawal", "Withdrawn"), ("Supersession", "Superseded")):
            with self.subTest(kind=kind):
                first.write_text(f"2026-11-06 {kind}: Decision under review. [replacement: v2.0.0]\n")
                (notices / "0002.md").write_text("2026-11-07 Correction: Decision being reconsidered.\n")
                write_records(self.root, self.tag)
                self.assertIn(f"<strong>{status}</strong>", self.page.read_text())
                last = notices / "0003.md"
                last.write_text("2026-11-07 Reinstatement: The original decision was in error.\n")
                write_records(self.root, self.tag)
                page = self.page.read_text()
                self.assertIn("<strong>Published</strong>", page)
                self.assertIn("Read this edition online", page)
                self.assertNotIn("Check the status of", page)
                self.assertIn(f"{kind}: Decision under review.", page)
                self.assertIn("Reinstatement: The original decision was in error.", page)
                landing = (self.root / "records/works/essay/index.html").read_text()
                self.assertNotIn(f"<strong>{status}</strong>", landing)
                self.assertNotIn("Replacement", landing)
                last.unlink()

    def test_late_work_doi_updates_only_work_page(self):
        self.git("rm", "-f", str(self.work_registration))
        del self.manifest["work_identifiers"]
        self.manifest["initial_release_body"] = render_initial_body(self.manifest)
        (self.package / "provenance.json").write_text(json.dumps(self.manifest))
        self.receipt = {**authorization(self.package, self.manifest), "issuing": True, "published_at": self.timestamp}
        self.save_receipt()
        self.generate()
        initial = self.page.read_bytes()
        self.work_registration.parent.mkdir(exist_ok=True)
        self.save_work({"concept_doi": "10.1234/late"})
        write_records(self.root, self.tag)
        self.assertIn('href="https://doi.org/10.1234/late"', (self.root / "records/works/essay/index.html").read_text())
        self.assertEqual(self.page.read_bytes(), initial)

    def test_doi_corrections_preserve_captured_identifiers(self):
        self.generate()
        edition = self.page.read_bytes()
        old, new = "10.1234/essay", "10.1234/revised"
        self.save_work({"concept_doi": new})
        with self.assertRaisesRegex(AssetError, "no matching work registration or history"):
            write_records(self.root, self.tag)
        history = [{"date": "2026-11-06", "from": old, "to": new}]
        self.save_work({"concept_doi": new}, concept_doi_history=history)
        write_records(self.root, self.tag)
        work = self.root / "records/works/essay/index.html"
        self.assertIn(f'href="https://doi.org/{new}"', work.read_text())
        self.assertIn(f"2026-11-06 · Corrected {old} to {new}.", work.read_text())
        history.append({"date": "2026-11-07", "from": new, "to": None})
        self.save_work({}, concept_doi_history=history)
        write_records(self.root, self.tag)
        self.assertIn(f"2026-11-07 · Retired {new}.", work.read_text())
        self.assertNotIn("https://doi.org/", work.read_text())
        self.assertEqual(self.page.read_bytes(), edition)

    def test_claim_requires_only_the_tracked_provenance(self):
        del self.receipt["published_at"]
        self.save_receipt()
        with self.assertRaisesRegex(AssetError, "reviewed provenance"):
            check_records(self.root)
        self.directory.mkdir(parents=True)
        provenance = self.directory / "provenance.json"
        provenance.write_bytes((self.package / "provenance.json").read_bytes())
        with self.assertRaisesRegex(AssetError, "tracked pages"):
            check_records(self.root)
        self.git("add", str(provenance))
        check_records(self.root)
        self.page.write_text("premature page")
        with self.assertRaisesRegex(AssetError, "tracked pages"):
            check_records(self.root)
        self.page.unlink()
        del self.receipt["issuing"]
        self.save_receipt()
        with self.assertRaisesRegex(AssetError, "tracked pages"):
            check_records(self.root)

    def test_no_work_page_before_first_receipt_or_through_symlink(self):
        del self.receipt["published_at"]
        del self.receipt["issuing"]
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
        self.receipt["issuing"] = True
        self.receipt["published_at"] = self.timestamp
        self.save_receipt()
        with self.assertRaisesRegex(AssetError, "symlink"):
            self.generate()
        self.assertFalse((self.package / "index.html").exists())

    def test_pending_stray_and_symlink_paths_fail(self):
        del self.receipt["published_at"]
        del self.receipt["issuing"]
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
        self.receipt["issuing"] = True
        self.receipt["published_at"] = self.timestamp
        self.save_receipt()
        with self.assertRaisesRegex(AssetError, "symlink"):
            self.generate()
        self.assertFalse((self.package / "index.html").exists())
