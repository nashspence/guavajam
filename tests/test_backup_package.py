"""Verify a different host's preserved package before moving the edition's permanent routes."""
from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from asset_policy import AssetError
from audit_assets import editorial_checkpoint, main
from backup_package import verify_backup_package
from check_backup_archive import check_backup_archive
from editorial_notices import render_initial_body
from export_archive import export_archive
from issuance import authorization, public_receipt
from public_record import record_html
from reviewed_artifact import restore_claimed
from edition_package import package_names
from test_render_contract import starter_package


class BackupPackageTests(unittest.TestCase):
    def setUp(self):
        fixture = self.enterContext(starter_package())
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        manifest = json.loads((fixture.output / "provenance.json").read_text())
        self.manifest = manifest
        self.tag = manifest["tag"]
        self.base = "https://backup.example.org/press"
        self.filename = manifest["canonical_artifact"]
        self.receipt = {**authorization(fixture.output, manifest), "issuing": True, "published_at": "2026-11-05T14:00:00Z",
                        "identifiers": {"oclc": "42"}}
        self.files = {name: (fixture.output / name).read_bytes() for name in package_names(manifest)}
        import shutil
        shutil.rmtree(fixture.repo)
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

    def test_receipt_reads_original_release_bytes_during_reader_outage(self):
        for name, data in self.files.items():
            (self.root / name).write_bytes(data)
        claim = {**authorization(self.root, self.manifest), "issuing": True}
        registration = self.root / "registrations/editions/sample-work/v1.0.0.json"
        registration.parent.mkdir(parents=True)
        registration.write_text(json.dumps(claim, indent=2, sort_keys=True) + "\n")
        release = {"tag_name": self.tag, "draft": False, "prerelease": False, "immutable": True,
                   "published_at": self.receipt["published_at"], "name": self.manifest["title"] + " — v1.0.0",
                   "body": self.manifest["initial_release_body"],
                   "assets": [{"name": n, "id": i, "state": "uploaded"} for i, n in enumerate(self.files, 1)]}

        def download(*args):
            self.assertEqual(args[:6], ("release", "download", self.tag, "--repo", "owner/press", "--pattern"))
            name = args[6]
            (Path(args[-1]) / name).write_bytes(self.files[name])

        with (patch("publish_edition.api", return_value=release), patch("audit_assets.gh", side_effect=download),
              patch("audit_assets.tag_commit", return_value=self.manifest["source_commit"]),
              patch("reader_routes.public_response", side_effect=OSError("reader host offline"))):
            result = public_receipt(self.root, self.root, self.manifest, "owner/press")
            self.assertEqual(result, {**claim, "published_at": release["published_at"]})
            registration.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
            self.assertEqual(result, public_receipt(self.root, self.root, self.manifest, "owner/press"))
            self.files[self.filename] += b"corrupt public download"
            with self.assertRaisesRegex(AssetError, "checksums do not match"):
                public_receipt(self.root, self.root, self.manifest, "owner/press")

    def test_checks_backup_attachments_and_reading_routes_before_cutover(self):
        registration = self.root / "receipt.json"
        registration.write_text(json.dumps(self.receipt, indent=2, sort_keys=True) + "\n")
        notice = "2026-11-05 Withdrawal: This result has been withdrawn."
        parent = self.root / "notices/sample-work/v1.0.0"
        parent.mkdir(parents=True)
        (parent / "0001.md").write_text(notice + "\n")
        record_url = self.base + "/" + self.tag
        approved = record_html(self.manifest, published_at=self.receipt["published_at"],
                               notices=["- " + notice], identifiers=self.receipt["identifiers"])
        pages = {record_url: approved, record_url + "/read": self.files[self.filename]}
        archived = self.root / "records/editions/sample-work/v1.0.0/index.html"
        archived.parent.mkdir(parents=True)
        archived.write_bytes(approved)
        checkpoint = editorial_checkpoint(self.receipt, ["- " + notice], approved)
        argv = ["audit_assets.py", "--backup-base", self.base, "--archive-root", str(self.root),
                "--registration", str(registration), "--editorial-sha256", checkpoint]

        work_url = record_url.rsplit("/", 1)[0]
        pages[work_url] = b'<a href="sample-work/v1.0.0">v1.0.0</a>'
        argv.extend(["--work-sha256", hashlib.sha256(pages[work_url]).hexdigest()])

        @contextmanager
        def page(url, *args, **kwargs):
            self.requests.append(url)
            response = io.BytesIO(pages[url])
            response.status = 200
            response.getheader = {"Content-Type": "text/html"}.get
            yield response

        with patch.object(sys, "argv", argv), patch("reader_routes.public_response", page):
            # A recovery verifier with newer CSS still recognizes the approved archived bytes.
            with patch("public_record.PAGE_STYLE", "<style>body{font-size:2rem}</style>"):
                self.assertEqual(main(), 0)
            self.assertEqual(self.requests, [*self.files, *pages])
            pages[work_url] += b"stale edition list"
            self.assertEqual(main(), 1)
            pages[work_url] = pages[work_url].removesuffix(b"stale edition list")
            pages[record_url] = pages[record_url].replace(b"Withdrawal", b"Correction")
            self.assertEqual(main(), 1)
            # Even if both the recovery host and local copy agree, the independent checkpoint wins.
            archived.write_bytes(pages[record_url])
            self.requests.clear()
            self.assertEqual(main(), 1)
            self.assertEqual(self.requests, [])
            archived.write_bytes(approved)
            (parent / "0001.md").unlink()
            self.requests.clear()
            self.assertEqual(main(), 1)
            self.assertEqual(self.requests, [])

    def test_standby_uses_complete_retained_commit_and_reports_multiple_failures(self):
        def git(*args):
            return subprocess.check_output(['git', *args], cwd=self.root, text=True).strip()

        def save(path, data):
            path = self.root / path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        def receipt_path(version):
            return f'registrations/editions/sample-work/{version}.json'

        git('init', '-q')
        save('README.md', b'No issued editions yet.\n')
        git('add', 'README.md')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '-q', '--allow-empty', '-m', 'empty')
        empty = git('rev-parse', 'HEAD')
        with self.assertRaisesRegex(AssetError, 'no issued editions'):
            check_backup_archive(self.root, empty, self.base, 'owner/press')
        for version in ('v1.0.0', 'v2.0.0'):
            manifest = dict(self.manifest, tag='works/sample-work/' + version)
            manifest['initial_release_body'] = render_initial_body(manifest)
            directory = Path('records/editions/sample-work') / version
            save(directory / 'provenance.json', (json.dumps(manifest) + '\n').encode())
            receipt = {**self.receipt, **authorization(self.root / directory, manifest)}
            save(receipt_path(version), (json.dumps(receipt, indent=2, sort_keys=True) + '\n').encode())
            save(directory / 'index.html', b'approved edition page')
        save('records/works/sample-work/index.html', b'approved work page')
        git('add', '.')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '-qm', 'issued editions')
        commit = git('rev-parse', 'HEAD')
        # Standby omissions cannot narrow the inventory, even after the working copy loses an entry.
        (self.root / receipt_path('v2.0.0')).unlink()
        save('records/works/sample-work/index.html', b'stale work page')
        with (patch('check_backup_archive.verify_backup_package', side_effect=[None, AssetError('missing archive')]) as package,
              patch('check_backup_archive.verify_reader_routes') as reader,
              patch('check_backup_archive.verify_work_route', side_effect=AssetError('stale work')) as work,
              self.assertRaises(AssetError) as raised):
            check_backup_archive(self.root, commit, self.base, 'owner/press')
        self.assertIn('works/sample-work/v2.0.0 standby package: missing archive', str(raised.exception))
        self.assertIn('works/sample-work standby work page: stale work', str(raised.exception))
        self.assertEqual(2, package.call_count)
        self.assertEqual(2, reader.call_count)
        work.assert_called_once()
        self.assertEqual(hashlib.sha256(b'approved work page').hexdigest(), work.call_args.args[2])

    def prepare_export(self):
        self.checkout, self.packages, self.output = (self.root / name for name in ("checkout", "packages", "standby"))
        self.checkout.mkdir()
        self.git("init", "-q")
        (self.checkout / "README.md").write_text("Fixture\n")
        self.git("add", ".")
        self.git("commit", "-qm", "root")
        self.ancestor = self.git("rev-parse", "HEAD")
        tag = Path(self.tag)
        edition = tag.relative_to("works")
        record = self.checkout / "records/editions" / edition
        record.mkdir(parents=True)
        (record / "provenance.json").write_bytes(self.files["provenance.json"])
        notice = "2026-11-05 Withdrawal: Withdrawn for reanalysis."
        notices = self.checkout / "notices" / edition
        notices.mkdir(parents=True)
        (notices / "0001.md").write_text(notice + "\n")
        self.edition_page = record_html(self.manifest, published_at=self.receipt["published_at"],
                                       notices=["- " + notice], identifiers=self.receipt["identifiers"])
        (record / "index.html").write_bytes(self.edition_page)
        receipt = self.checkout / "registrations/editions" / edition.with_name(tag.name + ".json")
        receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps(self.receipt, indent=2, sort_keys=True) + "\n")
        self.work_page = self.checkout / "records" / tag.parent / "index.html"
        self.work_page.parent.mkdir(parents=True)
        self.work_page.write_bytes(b"<p>All editions withdrawn</p>")
        self.git("add", ".")
        self.git("commit", "-qm", "receipt and withdrawal")
        self.approved = self.git("rev-parse", "HEAD")
        self.source = self.packages / tag
        self.source.mkdir(parents=True)
        for name, data in self.files.items():
            (self.source / name).write_bytes(data)

    def git(self, *args, cwd=None):
        return subprocess.check_output(["git", "-c", "user.name=Test", "-c", "user.email=test@example.org", *args],
                                       cwd=cwd or self.checkout, text=True).strip()

    def test_standby_export_restores_only_approved_history_and_public_files(self):
        self.prepare_export()
        self.work_page.write_bytes(b"newer page")
        self.git("commit", "-qam", "later checkpoint")
        later = self.git("rev-parse", "HEAD")
        self.assertEqual(export_archive(self.checkout, self.approved, self.packages, self.output), 1)
        self.assertEqual({p.name for p in self.output.iterdir()}, {"archive.bundle", "ARCHIVE_COMMIT", "public"})
        self.assertEqual((self.output / "ARCHIVE_COMMIT").read_text(), self.approved + "\n")
        tag, public = Path(self.tag), self.output / "public"
        expected = {tag / n: data for n, data in self.files.items()}
        expected.update({tag / "index.html": self.edition_page, tag / "read.html": self.files[self.filename],
                         tag.parent / "index.html": b"<p>All editions withdrawn</p>"})
        self.assertEqual({p.relative_to(public): p.read_bytes() for p in public.rglob("*") if p.is_file()}, expected)
        restored = self.root / "restored"
        self.git("clone", "-q", str(self.output / "archive.bundle"), str(restored))
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=restored), self.approved)
        self.assertEqual(self.git("rev-parse", "HEAD^", cwd=restored), self.ancestor)
        self.assertNotEqual(subprocess.run(["git", "cat-file", "-e", later], cwd=restored).returncode, 0)
        with self.assertRaisesRegex(AssetError, "new directory outside"):
            export_archive(self.checkout, self.approved, self.packages, self.output)

    def test_standby_export_refuses_incomplete_or_linked_inputs_without_partial_output(self):
        self.prepare_export()
        with self.assertRaisesRegex(AssetError, "no issued editions"):
            export_archive(self.checkout, self.ancestor, self.packages, self.output)
        with self.assertRaisesRegex(AssetError, "new directory outside"):
            export_archive(self.checkout, self.approved, self.packages, self.checkout / "standby")
        shallow = self.root / "shallow"
        self.git("clone", "-q", "--depth=1", self.checkout.as_uri(), str(shallow))
        with self.assertRaisesRegex(AssetError, "shallow"):
            export_archive(shallow, self.approved, self.packages, self.output)
        checksum = self.source / "SHA256SUMS"
        checksum.unlink()
        with self.assertRaisesRegex(AssetError, "exactly HTML"):
            export_archive(self.checkout, self.approved, self.packages, self.output)
        (self.root / "other").write_bytes(self.files["SHA256SUMS"])
        checksum.symlink_to(self.root / "other")
        with self.assertRaisesRegex(AssetError, "symlinks"):
            export_archive(self.checkout, self.approved, self.packages, self.output)
        checksum.unlink()
        checksum.write_bytes(self.files["SHA256SUMS"])
        (self.source / self.filename).write_bytes(b"stale")
        with self.assertRaisesRegex(AssetError, "checksums do not match"):
            export_archive(self.checkout, self.approved, self.packages, self.output)
        (self.source / self.filename).write_bytes(self.files[self.filename])
        # Even a self-consistent three-file package cannot replace the independently receipted one.
        provenance = self.files["provenance.json"] + b"\n"
        (self.source / "provenance.json").write_bytes(provenance)
        checksum.write_bytes(self.files["SHA256SUMS"].replace(self.receipt["provenance_sha256"].encode(),
                                                              hashlib.sha256(provenance).hexdigest().encode()))
        with self.assertRaisesRegex(AssetError, "provenance_sha256 differs"):
            export_archive(self.checkout, self.approved, self.packages, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.output.parent.glob(".press-export-*")), [])

    def test_issue_from_saved_files_after_historical_runs_are_gone(self):
        claim = {k: v for k, v in self.receipt.items() if k not in {"identifiers", "published_at"}}
        claim["staged_run_id"] = "73"
        edition = self.tag.removeprefix("works/")
        path = self.root / "registrations/editions" / (edition + ".json")
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(claim, indent=2, sort_keys=True) + "\n")
        provenance = self.root / "records/editions" / edition / "provenance.json"
        provenance.parent.mkdir(parents=True)
        provenance.write_bytes(self.files["provenance.json"])
        digest = claim["provenance_sha256"]
        assets = [{"name": name, "size": len(data), "state": "uploaded"} for name, data in self.files.items()]

        def transport(*args):
            self.assertEqual(("proof-restore-" + digest, "--repo", "owner/press"), args[2:5])
            if args[:2] == ("release", "view"):
                return json.dumps({"isDraft": True, "assets": assets})
            self.assertEqual(("release", "download"), args[:2])
            self.assertEqual(("--pattern", self.filename, "--pattern", "provenance.json", "--pattern", "SHA256SUMS"),
                             args[7:])
            for name, data in self.files.items():
                (Path(args[6]) / name).write_bytes(data)

        def restore(run_id="73"):
            with tempfile.TemporaryDirectory() as temp:
                result = Path(temp) / "edition"
                restore_claimed(self.root, "owner/press", self.tag, run_id, digest, result)
                return {p.name: p.read_bytes() for p in result.iterdir()}

        with (patch("reviewed_artifact.gh", side_effect=transport) as gh,
              patch("reviewed_artifact.api", side_effect=AssertionError("historical Actions lookup"))):
            with self.assertRaisesRegex(AssetError, "issuing claim"):
                restore(run_id="74")
            with patch.dict(claim, issuing=False):
                path.write_text(json.dumps(claim, indent=2, sort_keys=True) + "\n")
                with self.assertRaisesRegex(AssetError, "issuing claim"):
                    restore()
            gh.assert_not_called()
            path.write_text(json.dumps(claim, indent=2, sort_keys=True) + "\n")
            with patch.dict(assets[0], size=10**9), self.assertRaisesRegex(AssetError, "bounded"):
                restore()
            self.assertEqual(self.files, restore())
            for name, data in list(self.files.items()):
                with (self.subTest(name=name), patch.dict(self.files, {name: data[:-1] + bytes([data[-1] ^ 1])}),
                      self.assertRaises(AssetError)):
                    restore()

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
