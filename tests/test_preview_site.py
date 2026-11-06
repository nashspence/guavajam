"""Preview the real starter without issuing it or changing its canonical bytes."""
import hashlib
from http.client import HTTPConnection
import io
import json
from pathlib import Path
import sys
import subprocess
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urljoin, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from asset_policy import AssetError
from build_edition import build_edition
from issuance import authorization
from preview_site import PREVIEW_SCHEMA, assemble_preview, main, preview_server
from public_record import write_records
from test_assets import EditionTests
from test_render_contract import RenderContractTests
from verify_edition import verify_edition


class PreviewTests(EditionTests):
    def setUp(self):
        super().setUp()
        RenderContractTests.sample_with_structures(self)
        self.preview = self.base / "preview"
        self.timestamp = "2026-11-06T12:00:00Z"

    def assemble(self, **kwargs):
        return assemble_preview(self.repo, self.commit, self.tag, self.preview,
            fetcher=lambda *_: (self.preview_asset, "image/svg+xml"),
            simulated_at=self.timestamp, **kwargs)

    def state(self):
        return (self.git("status", "--porcelain=v1", "--untracked-files=all"),
                self.git("show-ref"), self.git("ls-files", "--stage"),
                {p: (self.repo / p).read_bytes() for p in self.git("ls-files").splitlines()})

    def test_canonical_package_identity_and_separate_simulation(self):
        # Uncommitted tracked edits and untracked files cannot enter the preview.
        (self.repo / "works/sample-work/manuscript.md").write_text("Unreviewed replacement")
        (self.repo / "private.env").write_text("synthetic private fixture")
        before = self.state()
        with patch("subprocess.run", wraps=subprocess.run) as calls:
            metadata = self.assemble()
        for call in calls.call_args_list:
            command = call.args[0]
            self.assertIn(command[0], ("git", "pandoc"))
            if command[0] == "git":
                self.assertIn(command[1], ("rev-parse", "show-ref", "archive", "show"))
        manifest = build_edition(self.repo, self.commit, self.tag, self.output,
            fetcher=lambda *_: (self.preview_asset, "image/svg+xml"))
        self.assertEqual(manifest, verify_edition(self.preview / "package"))
        self.assertEqual({p.name: p.read_bytes() for p in self.output.iterdir()},
                         {p.name: p.read_bytes() for p in (self.preview / "package").iterdir()})
        self.assertEqual(PREVIEW_SCHEMA, metadata["schema"])
        self.assertIs(metadata["preview_only"], True)
        self.assertEqual(self.timestamp, metadata["published_at"])
        self.assertFalse({"issuing", "staged_run_id", "artifact_sha256"} & metadata.keys())
        for route in ("/works/sample-work", "/" + self.tag):
            page = (self.preview / metadata["routes"][route]["file"]).read_bytes()
            self.assertIn(b"Local preview", page)
            self.assertIn(b"simulated", page)
        read = (self.preview / metadata["routes"]["/" + self.tag + "/read"]["file"]).read_bytes()
        self.assertNotIn(b"Local preview", read)
        self.assertEqual(hashlib.sha256(read).hexdigest(), manifest["html_sha256"])
        self.assertFalse(any(p.name in ("private.env", "authorization.json") or
                             "registrations" in p.parts for p in self.preview.rglob("*")))
        self.assertEqual(before, self.state())

    def test_http_routes_navigation_head_attachments_and_bounds(self):
        metadata = self.assemble()
        before = self.state()
        with preview_server(self.preview, metadata, 0) as server:
            self.assertEqual("127.0.0.1", server.server_address[0])
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                conn = HTTPConnection(*server.server_address, timeout=5)
                def request(path, method="GET", headers=None):
                    conn.request(method, path, headers=headers or {})
                    response = conn.getresponse()
                    return response.status, dict(response.getheaders()), response.read()
                base = f"http://127.0.0.1:{server.server_address[1]}"
                # Resolve the actual shared renderer's links at extensionless URLs.
                for current, href in (("/works/sample-work", "sample-work/v1.0.0"),
                                      ("/" + self.tag, "v1.0.0/read"),
                                      ("/" + self.tag, "../sample-work")):
                    status, _, raw = request(current)
                    self.assertEqual(200, status)
                    self.assertIn(f'href="{href}"'.encode(), raw)
                    self.assertEqual(200, request(urlsplit(urljoin(base + current, href)).path)[0])
                for route, info in metadata["routes"].items():
                    status, headers, raw = request(route)
                    self.assertEqual(200, status, route)
                    self.assertEqual((self.preview / info["file"]).read_bytes(), raw)
                    self.assertEqual("no-store, no-transform", headers["Cache-Control"])
                    self.assertEqual("simulated; not-issued", headers["X-Press-Preview"])
                    status, head, empty = request(route, "HEAD")
                    self.assertEqual((200, b"", str(len(raw))), (status, empty, head["Content-Length"]))
                    if route.endswith((".html", ".json", "SHA256SUMS")):
                        self.assertTrue(headers["Content-Disposition"].startswith("attachment;"))
                        self.assertEqual("application/octet-stream", headers["Content-Type"])
                for route in ("/preview.json", "/package/provenance.json", "/.git/config", "/private.env",
                              "/works/sample-work/", "/" + self.tag + "/../provenance.json",
                              "/%2e%2e/private.env", "/" + self.tag + "?download=1"):
                    self.assertEqual(404, request(route)[0], route)
                self.assertEqual(403, request("/", headers={"Host": "external.example"})[0])
                self.assertEqual(501, request("/", "POST")[0])
                conn.close()
            finally:
                server.shutdown()
                thread.join(timeout=5)
        self.assertEqual(before, self.state())

    def issued_first_edition(self):
        manifest = build_edition(self.repo, self.commit, self.tag, self.output,
            fetcher=lambda *_: (self.preview_asset, "image/svg+xml"))
        receipt = {**authorization(self.output, manifest), "issuing": True,
                   "published_at": "2026-11-05T12:00:00Z", "identifiers": {"oclc": "42"}}
        path = self.repo / "registrations/editions/sample-work/v1.0.0.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
        notices = self.repo / "notices/sample-work/v1.0.0"
        notices.mkdir(parents=True)
        (notices / "0001.md").write_text("2026-11-06 Withdrawal: Review in progress.\n")
        self.git("add", "registrations", "notices")
        write_records(self.repo, self.tag, package=self.output)
        self.git("add", "records")
        self.git("commit", "-qm", "Record an issued fixture and current withdrawal")
        self.commit = self.git("rev-parse", "HEAD")
        self.tag = "works/sample-work/v2.0.0"
        return receipt

    def test_existing_receipt_metadata_status_and_candidate_share_work_page(self):
        receipt = self.issued_first_edition()
        before = self.state()
        metadata = self.assemble()
        routes = metadata["routes"]
        landing = (self.preview / routes["/works/sample-work"]["file"]).read_text()
        self.assertIn("Withdrawn", landing)
        self.assertIn("Published", landing)
        self.assertLess(landing.index("sample-work/v2.0.0"), landing.index("sample-work/v1.0.0"))
        self.assertIn("2026-11-05", landing)
        old_route = "/works/sample-work/v1.0.0"
        old = (self.preview / routes[old_route]["file"]).read_bytes()
        self.assertEqual(old, (self.repo / "records/editions/sample-work/v1.0.0/index.html").read_bytes())
        self.assertIn(b"OCLC number: 42", old)
        self.assertIn(b"Review in progress.", old)
        with preview_server(self.preview, metadata, 0) as server:
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                conn = HTTPConnection(*server.server_address, timeout=5)
                conn.request("GET", old_route + "/read")
                response = conn.getresponse()
                self.assertEqual(302, response.status)
                self.assertEqual(receipt["record_url"] + "/read", response.getheader("Location"))
                self.assertEqual(b"", response.read())
                conn.close()
            finally:
                server.shutdown(); thread.join(timeout=5)
        self.assertEqual(before, self.state())

    def test_stale_existing_pages_fail_without_creating_output(self):
        self.issued_first_edition()
        page = self.repo / "records/works/sample-work/index.html"
        page.write_bytes(page.read_bytes() + b"stale")
        self.git("commit", "-qam", "Stale fixture")
        self.commit = self.git("rev-parse", "HEAD")
        with self.assertRaisesRegex(AssetError, "work page is stale"):
            self.assemble()
        self.assertFalse(self.preview.exists())

    def test_conflicting_registered_version_fails_without_writes(self):
        self.issued_first_edition()
        self.tag = "works/sample-work/v1.0.0"
        before = self.state()
        with self.assertRaisesRegex(AssetError, "already registered"):
            self.assemble()
        self.assertEqual(before, self.state())
        self.assertFalse(self.preview.exists())

    def test_existing_local_tag_fails_without_writes(self):
        self.git("tag", self.tag)
        before = self.state()
        with self.assertRaisesRegex(AssetError, "tag already exists"):
            self.assemble()
        self.assertEqual(before, self.state())
        self.assertFalse(self.preview.exists())

    def test_invalid_tag_timestamp_and_output_are_rejected(self):
        for tag in ("sample-work/v1.0.0", "works/../v1.0.0", "works/sample-work/v01.0.0"):
            with self.subTest(tag=tag), self.assertRaisesRegex(AssetError, "TAG must"):
                assemble_preview(self.repo, self.commit, tag, self.preview)
        with self.assertRaisesRegex(AssetError, "timestamp"):
            assemble_preview(self.repo, self.commit, self.tag, self.preview, simulated_at="yesterday")
        for output in (self.repo / "dist", self.repo / "new", self.repo):
            with self.subTest(output=output), self.assertRaisesRegex(AssetError, "new directory outside"):
                assemble_preview(self.repo, self.commit, self.tag, output)
        self.preview.mkdir()
        (self.preview / "retained.txt").write_text("keep")
        with self.assertRaisesRegex(AssetError, "new directory outside"):
            self.assemble()
        self.assertEqual("keep", (self.preview / "retained.txt").read_text())

    def test_missing_committed_manuscript_cannot_use_dirty_file(self):
        self.tag = "works/uncommitted/v1.0.0"
        path = self.repo / "works/uncommitted/manuscript.md"
        path.parent.mkdir()
        path.write_text("Not committed")
        before = self.state()
        with self.assertRaisesRegex(AssetError, "manuscript"):
            self.assemble()
        self.assertEqual(before, self.state())
        self.assertFalse(self.preview.exists())

    def test_failed_build_and_verification_leave_no_output_or_staging(self):
        before = self.state()
        for target in ("preview_site.build_edition", "preview_site.verify_edition"):
            with self.subTest(target=target), patch(target, side_effect=AssetError("rejected fixture")):
                with self.assertRaisesRegex(AssetError, "rejected fixture"):
                    self.assemble()
            self.assertFalse(self.preview.exists())
            self.assertEqual([], list(self.base.glob(".press-preview-build-*")))
            self.assertEqual(before, self.state())

    def test_cli_ctrl_c_removes_temporary_site_and_prints_local_urls(self):
        assemble = assemble_preview
        observed = []
        def offline(repo, commit, tag, output):
            observed.append(output)
            return assemble(self.repo, self.commit, tag, output,
                fetcher=lambda *_: (self.preview_asset, "image/svg+xml"))
        with patch("sys.argv", ["preview_site.py", "--tag", self.tag, "--port", "0"]), \
             patch("preview_site.Path.cwd", return_value=self.repo), \
             patch("preview_site.assemble_preview", side_effect=offline), \
             patch("preview_site.ThreadingHTTPServer.serve_forever", side_effect=KeyboardInterrupt), \
             patch("sys.stdout", new_callable=io.StringIO) as log:
            self.assertEqual(0, main())
        self.assertIn("SIMULATED PUBLICATION; NOT ISSUED", log.getvalue())
        self.assertIn("http://127.0.0.1:", log.getvalue())
        self.assertIn("/" + self.tag + "/read", log.getvalue())
        self.assertTrue(observed)
        self.assertFalse(observed[0].exists())
        self.assertFalse(observed[0].parent.exists())


def load_tests(loader, tests, pattern):
    # Do not run inherited canonical-builder tests twice.
    return unittest.TestSuite(PreviewTests(name) for name in PreviewTests.__dict__ if name.startswith("test_"))
