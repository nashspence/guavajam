"""Verify a different host's preserved package before moving the edition's permanent routes."""
from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from asset_policy import AssetError
from audit_assets import editorial_checkpoint, main
from backup_package import verify_backup_package
from editorial_notices import render_initial_body
from issuance import authorization
from public_record import record_html


class BackupPackageTests(unittest.TestCase):
    def setUp(self):
        fixture = Path(__file__).parent / "fixtures/archived-v1"
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        manifest = json.loads((fixture / "provenance.json").read_text())
        self.manifest = manifest
        self.tag = manifest["tag"]
        self.base = "https://backup.example.org/press"
        self.filename = manifest["canonical_artifact"]
        url = "https://press.example.org/" + self.tag
        manifest["record_url"] = url
        manifest.update(author="Test author", language="en")
        html = (fixture / self.filename).read_text().replace(
            "</head>", '<meta name="author" content="Test author"></head>').replace(
            "<body>", f'<body><a id="press-edition-status" href="{url}">Edition status</a>').replace(
            "</h2>", f'<a class="press-permalink" aria-label="Link to Section" href="{url}/read#example">#</a></h2>').replace(
            "</footer>", f'<a id="press-edition-read" href="{url}/read">Read</a>'
            f'<a id="press-edition-record" href="{url}">Record</a></footer>').encode()
        manifest["html_byte_count"] = len(html)
        manifest["html_sha256"] = hashlib.sha256(html).hexdigest()
        manifest["initial_release_body"] = render_initial_body(manifest)
        raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
        (self.root / "provenance.json").write_bytes(raw)
        self.receipt = {**authorization(self.root, manifest), "published_at": "2026-11-05T14:00:00Z",
                        "identifiers": {"oclc": "42"}}
        (self.root / "provenance.json").unlink()
        self.files = {self.filename: html, "provenance.json": raw}
        self.files["SHA256SUMS"] = "".join(hashlib.sha256(data).hexdigest() + "  " + name + "\n"
                                           for name, data in self.files.items()).encode()
        self.requests = []
        self.enterContext(patch("backup_package.public_response", self.response))

    @contextmanager
    def response(self, url, accept, *, timeout):
        prefix = self.base + "/" + self.tag + "/"
        self.assertTrue(url.startswith(prefix))
        name = url.removeprefix(prefix)
        self.requests.append(name)
        response = io.BytesIO(self.files.get(name, b""))
        response.status = 200 if name in self.files else 404
        response.getheader = {}.get
        yield response

    def test_checks_backup_attachments_and_reading_routes_before_cutover(self):
        registration = self.root / "receipt.json"
        registration.write_text(json.dumps(self.receipt, indent=2, sort_keys=True) + "\n")
        notice = "2026-11-05 Withdrawal: This result has been withdrawn."
        parent = self.root / "notices/archive-fixture/v1.0.0"
        parent.mkdir(parents=True)
        (parent / "0001.md").write_text(notice + "\n")
        checkpoint = editorial_checkpoint(self.receipt, ["- " + notice])
        argv = ["audit_assets.py", "--backup-base", self.base, "--archive-root", str(self.root),
                "--registration", str(registration), "--editorial-sha256", checkpoint]
        record_url = self.base + "/" + self.tag
        pages = {record_url: record_html(self.manifest, published_at=self.receipt["published_at"],
                                         notices=["- " + notice], identifiers=self.receipt["identifiers"]),
                 record_url + "/read": self.files[self.filename]}

        work_url = record_url.rsplit("/", 1)[0]
        pages[work_url] = b'<a href="archive-fixture/v1.0.0">v1.0.0</a>'
        argv.extend(["--work-sha256", hashlib.sha256(pages[work_url]).hexdigest()])

        @contextmanager
        def page(url, *args, **kwargs):
            self.requests.append(url)
            response = io.BytesIO(pages[url])
            response.status = 200
            response.getheader = {"Content-Type": "text/html"}.get
            yield response

        with patch.object(sys, "argv", argv), patch("reader_routes.public_response", page):
            self.assertEqual(main(), 0)
            self.assertEqual(self.requests, [*self.files, *pages])
            pages[work_url] += b"stale edition list"
            self.assertEqual(main(), 1)
            pages[record_url] = pages[record_url].replace(b"Withdrawal", b"Correction")
            self.assertEqual(main(), 1)
            (parent / "0001.md").unlink()
            self.requests.clear()
            self.assertEqual(main(), 1)
            self.assertEqual(self.requests, [])

    def test_missing_attachment_or_modified_bytes_fail(self):
        for name, original in list(self.files.items()):
            with self.subTest(name=name):
                del self.files[name]
                with self.assertRaisesRegex(AssetError, "HTTP 404"):
                    verify_backup_package(self.base, self.tag, self.receipt)
                self.files[name] = original[:-1] + b"x"
                with self.assertRaisesRegex(AssetError, "differs from the trusted receipt|edition checksums do not match"):
                    verify_backup_package(self.base, self.tag, self.receipt)
                self.files[name] = original
