"""Asset URL, download, passive media and standalone edition regression tests."""
import base64
import hashlib
import io
import json
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from asset_policy import (AssetError, AssetPolicy, MAX_BYTES, fetch_asset,
                          public_addresses, svg_errors, verify_bytes)
from build_edition import build_edition
from editorial_notices import release_notes
from validate_sources import parse_document, walk
from verify_edition import verify_edition
from test_validate_sources import StrictSourceTests, MANUSCRIPT, METADATA

ORIGIN = "https://assets.publisher.test"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><circle cx="5" cy="5" r="4"/></svg>\n'
DIGEST = hashlib.sha256(SVG).hexdigest()
URL = ORIGIN + "/sha256/" + DIGEST + ".svg"


class AssetURLTests(unittest.TestCase):
    def test_prepared_preview_url_is_embedded_in_markdown(self):
        root = Path(__file__).resolve().parents[1]
        output = subprocess.check_output(
            [sys.executable, "scripts/prepare_asset.py", "storage/preview.svg"], cwd=root, text=True)
        fields = dict(line.split(": ", 1) for line in output.splitlines())
        preview = Path("storage/README.md")
        ast, errors = parse_document(root / preview, preview)
        self.assertEqual([], errors)
        targets = [node["c"][2][0] for node in walk(ast["blocks"]) if node["t"] == "Image"]
        self.assertIn(fields["URL"], targets)

    def test_canonical_origins_only(self):
        for origin in ["http://assets.publisher.test", "https://x", "https://x.test/",
                       "https://x.test:443", "https://user@x.test", "https://X.test",
                       "https://x.test?", "https://127.0.0.1", None, 123]:
            with self.subTest(origin=origin), self.assertRaises(AssetError):
                AssetPolicy(origin)

    def test_entire_url_checked_without_normalization(self):
        policy = AssetPolicy(ORIGIN)
        self.assertEqual((DIGEST, "svg"), policy.parse(URL))
        for extension in ["png", "jpg", "jpeg", "webp", "svg"]:
            policy.parse(ORIGIN + "/sha256/" + DIGEST + "." + extension)
        bad = [
            URL + "?", URL + "#", URL + "?download=1", URL + "#fragment",
            URL.replace("/sha256/", "/sha256/%2e%2e/"),
            URL.replace("/sha256/", "/%73ha256/"),
            URL.replace(DIGEST, DIGEST.upper()),
            URL.replace(DIGEST, "a" * 63), URL.replace(DIGEST, "a" * 65),
            URL.replace(".svg", ".gif"), URL.replace(".svg", ".SVG"),
            URL.replace("https:", "http:"), URL.replace("https:", ""),
            URL.replace("://", "://user:password@"),
            URL.replace(".test/", ".test:443/"), URL.replace(".test/", ".test./"),
            URL.replace(".test/", ".test.evil/"), URL.replace("assets.", "evil."),
            URL.replace("/sha256/", "//sha256/"), URL.replace("/sha256/", "/a/../sha256/"),
            URL.replace(".test/", ".test%2f.evil/"), URL.replace(".test/", ".test\\@evil/"),
            URL + "/redirect", URL.replace("https://", "HTTPS://"),
            " " + URL, URL + "\n", URL.replace(".svg", "%2esvg"),
            URL.replace("/sha256/", "/sha256%2f/"),
        ]
        for target in bad:
            with self.subTest(target=target), self.assertRaises(AssetError):
                policy.parse(target)

    def test_dns_must_be_public_only(self):
        for addresses in [["127.0.0.1"], ["10.0.0.1"], ["169.254.169.254"],
                          ["::1"], ["fc00::1"], ["::ffff:127.0.0.1"],
                          ["93.184.216.34", "192.168.1.1"], []]:
            records = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in addresses]
            with self.subTest(addresses=addresses), patch("asset_policy.socket.getaddrinfo", return_value=records):
                with self.assertRaises(AssetError):
                    public_addresses("assets.publisher.test")

    def test_dns_error(self):
        with patch("asset_policy.socket.getaddrinfo", side_effect=socket.gaierror):
            with self.assertRaises(AssetError):
                public_addresses("assets.publisher.test")


class SourceAssetTests(StrictSourceTests):
    # Do not inherit/run the base suite twice: load_tests below selects new methods.
    def setUp(self):
        super().setUp()
        (self.root / "asset-policy.json").write_text(json.dumps({"origin": ORIGIN}))

    def test_remote_inline_and_reference_images_offline(self):
        for image in [f"![Structural diagram]({URL})\n",
                      f"![Structural diagram][figure-1]\n\n[figure-1]: {URL}\n",
                      f"![Structural diagram][]\n\n[Structural diagram]: {URL}\n"]:
            with self.subTest(image=image), patch("socket.create_connection", side_effect=AssertionError("offline")):
                self.assertEqual([], self.check(MANUSCRIPT + image))

    def test_remote_missing_alt(self):
        self.assert_bad("image alt text required", MANUSCRIPT + f"![]({URL})\n")

    def test_remote_reference_unapproved(self):
        self.assert_bad("unsupported/external URI", MANUSCRIPT +
                        "![Structural diagram][figure]\n\n[figure]: https://evil.test/image.svg\n")

    def test_remote_parsed_destinations_rejected(self):
        for url in [URL + "?x=1", URL + "#frag", URL.replace("/sha256/", "/%73ha256/"),
                    URL.replace(".test/", ".test:443/"), URL.replace(".svg", ".gif")]:
            with self.subTest(url=url):
                self.assertTrue(self.check(MANUSCRIPT + f"![Structural diagram][fig]\n\n[fig]: {url}\n"))


class SourceBudgetTests(unittest.TestCase):
    def test_each_work_has_a_budget_and_shared_downloads_are_reused(self):
        from verify_source_assets import verify_source_assets

        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (root / "asset-policy.json").write_text(json.dumps({"origin": ORIGIN}))
        raw = [SVG + b" " * i for i in range(3)]
        urls = [ORIGIN + "/sha256/" + hashlib.sha256(data).hexdigest() + ".svg" for data in raw]
        content = dict(zip(urls, raw))
        paths = [Path(f"works/{slug}/manuscript.md") for slug in ("first", "second")]
        for index, path in enumerate(paths):
            (root / path.parent).mkdir(parents=True)
            (root / path).write_text("\n".join(f"![Image]({url})" for url in [urls[0], urls[index + 1]]))
        fetcher = Mock(side_effect=lambda policy, url: (content[url], "image/svg+xml"))
        with patch("verify_source_assets.git_sources", return_value=paths), \
             patch("verify_source_assets.MAX_ASSETS", 2), \
             patch("verify_source_assets.MAX_TOTAL_BYTES", max(map(len, raw)) * 2):
            self.assertEqual(3, verify_source_assets(root, fetcher=fetcher))
            self.assertEqual(fetcher.call_count, 3)
            with (root / paths[0]).open("a") as manuscript:
                manuscript.write(f"\n![Extra]({urls[2]})\n")
            with self.assertRaisesRegex(AssetError, "works/first/manuscript.md"):
                verify_source_assets(root, fetcher=fetcher)


class FetchTests(unittest.TestCase):
    def fetch(self, raw=SVG, headers=None, status=200, url=URL):
        response = Mock()
        response.status = status
        headers = {"Content-Type": "image/svg+xml", **(headers or {})}
        response.getheader.side_effect = lambda name, default=None: headers.get(name, default)
        response.read1.side_effect = [raw, b""]
        connection = Mock()
        connection.getresponse.return_value = response
        with patch("asset_policy.public_addresses", return_value=["93.184.216.34"]), \
             patch("asset_policy.PinnedHTTPSConnection", return_value=connection):
            return fetch_asset(AssetPolicy(ORIGIN), url)

    def test_verified_download(self):
        self.assertEqual((SVG, "image/svg+xml"), self.fetch(headers={"Content-Length": str(len(SVG))}))

    def test_fail_closed_cases(self):
        for status in [301, 302, 307, 308, 404, 500]:
            with self.subTest(status=status), self.assertRaises(AssetError):
                self.fetch(status=status, headers={"Location": "http://127.0.0.1/"})
        for raw, headers in [
            (SVG + b" ", {}),
            (SVG, {"Content-Type": "image/png"}),
            (SVG, {"Content-Encoding": "gzip"}),
            (SVG, {"Content-Length": str(MAX_BYTES + 1)}),
            (SVG, {"Content-Length": "bogus"}),
            (SVG, {"Content-Length": str(len(SVG) + 1)}),
            (b"x" * (MAX_BYTES + 1), {}),
        ]:
            with self.subTest(headers=headers, size=len(raw)), self.assertRaises(AssetError):
                self.fetch(raw, headers)

    def test_download_failure(self):
        with patch("asset_policy.public_addresses", return_value=["93.184.216.34"]), \
             patch("asset_policy.PinnedHTTPSConnection") as connection:
            connection.return_value.request.side_effect = TimeoutError
            with self.assertRaises(AssetError):
                fetch_asset(AssetPolicy(ORIGIN), URL)
            connection.return_value.close.assert_called_once()

    def test_unsafe_svg_with_correct_hash(self):
        raw = b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>'
        url = ORIGIN + "/sha256/" + hashlib.sha256(raw).hexdigest() + ".svg"
        with self.assertRaises(AssetError):
            self.fetch(raw, url=url)

    def test_mime_cannot_hide_actual_raster_type(self):
        from PIL import Image
        for extension, format, mime in [("png", "PNG", "image/png"), ("jpg", "JPEG", "image/jpeg"),
                                        ("webp", "WEBP", "image/webp")]:
            buf = io.BytesIO()
            Image.new("RGB", (4, 4)).save(buf, format)
            raw = buf.getvalue()
            self.assertEqual(mime, verify_bytes(raw, extension, mime))
            with self.assertRaises(AssetError):
                verify_bytes(raw, "webp" if extension == "png" else "png",
                             "image/webp" if extension == "png" else "image/png")
        with self.assertRaises(AssetError):
            verify_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 50, "png", "image/png")

    def test_svg_active_external_and_encoded_content(self):
        bad = [
            b'<!DOCTYPE svg [<!ENTITY x "x">]><svg xmlns="http://www.w3.org/2000/svg"/>',
            '<!DOCTYPE svg><svg xmlns="http://www.w3.org/2000/svg"/>'.encode("utf-16"),
            b'<?xml-stylesheet href="https://evil.test/style.css"?><svg xmlns="http://www.w3.org/2000/svg"/>',
        ]
        for fragment in ['<script/>', '<foreignObject/>', '<animate attributeName="href"/>',
                         '<set attributeName="onload"/>', '<image href="https://evil.test/x"/>',
                         '<g onload="alert(1)"/>', '<path fill="url(https://evil.test/x)"/>',
                         '<path style="fill:red"/>', '<g xmlns="http://www.w3.org/1999/xhtml"/>',
                         '<path fill="u\\72l(https://evil.test/x)"/>']:
            bad.append(('<svg xmlns="http://www.w3.org/2000/svg">' + fragment + '</svg>').encode())
        for raw in bad:
            with self.subTest(raw=raw):
                self.assertTrue(svg_errors(raw))


class EditionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Press test")
        self.git("config", "user.email", "test@example.invalid")
        path = self.repo / "works/sample/manuscript.md"
        path.parent.mkdir(parents=True)
        import yaml
        path.write_text("---\n" + yaml.safe_dump(METADATA) + "---\n\n" + MANUSCRIPT +
                        f"\n![Structural diagram][figure]\n\n[figure]: {URL}\n")
        (self.repo / "asset-policy.json").write_text(json.dumps({"origin": ORIGIN}))
        (self.repo / "edition-origin.txt").write_text("https://press.example.org\n")
        self.git("add", ".")
        self.git("commit", "-qm", "Reviewed source")
        self.commit = self.git("rev-parse", "HEAD")
        self.tag = "works/sample/v1.0.0"
        self.output = self.base / "edition"

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True,
                              capture_output=True, text=True).stdout.strip()

    def build(self, fetcher=lambda policy, url: (SVG, "image/svg+xml")):
        return build_edition(self.repo, self.commit, self.tag, self.output, fetcher=fetcher)

    def test_standalone_edition_and_provenance_offline(self):
        # Dirty/unreviewed files must not affect output.
        (self.repo / "works/sample/manuscript.md").write_text("unreviewed\n")
        manifest = self.build()
        self.assertEqual(self.commit, manifest["source_commit"])
        self.assertEqual(self.tag, manifest["tag"])
        self.assertEqual(URL, manifest["assets"][0]["source_url"])
        self.assertEqual(DIGEST, manifest["assets"][0]["sha256"])
        self.assertEqual(len(SVG), manifest["assets"][0]["byte_count"])
        with patch("socket.create_connection", side_effect=AssertionError("offline")):
            self.assertEqual(manifest, verify_edition(self.output, commit=self.commit, tag=self.tag))
        text = (self.output / "sample-v1.0.0.html").read_text()
        self.assertIn("data:image/svg+xml;base64," + base64.b64encode(SVG).decode(), text)
        self.assertNotIn(ORIGIN, text)
        self.assertNotIn("rights_statement", manifest)
        self.assertNotIn("Edition rights:", text)
        self.assertNotIn("Edition rights:", release_notes(manifest))
        self.assertEqual("", self.git("tag", "--list"))
        self.assertEqual("main", self.git("branch", "--show-current"))

    def test_build_requires_a_committed_edition_origin(self):
        self.git("rm", "edition-origin.txt")
        self.git("commit", "-qm", "Unconfigure edition origin")
        self.commit = self.git("rev-parse", "HEAD")
        with self.assertRaisesRegex(AssetError, "edition-origin.txt"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_initial_notes_are_read_from_the_matching_committed_edition(self):
        directory = self.repo / "works/sample/editions"
        directory.mkdir()
        source = "Plate I © Artist.\n\nUsed by permission.\n"
        (directory / "v1.0.0.md").write_bytes(source.encode("utf-8"))
        (directory / "v2.0.0.md").write_text("Different edition.\n")
        self.git("add", "works/sample/editions")
        self.git("commit", "-qm", "Review edition-specific rights terms")
        self.commit = self.git("rev-parse", "HEAD")
        (directory / "v1.0.0.md").write_text("Unreviewed replacement.\n")
        manifest = self.build()
        self.assertTrue(manifest["initial_release_body"].endswith(source.removesuffix("\n")))
        self.assertIn(f"({manifest['record_url']}/read)", manifest["initial_release_body"])
        self.assertEqual(manifest, verify_edition(self.output))

    def test_repeat_build_has_identical_bytes(self):
        self.build()
        second = self.base / "second"
        build_edition(self.repo, self.commit, self.tag, second, fetcher=lambda *_: (SVG, "image/svg+xml"))
        for path in self.output.iterdir():
            self.assertEqual(path.read_bytes(), (second / path.name).read_bytes())

    def test_unavailable_or_tampered_asset_produces_no_package(self):
        def unavailable(*args):
            raise AssetError("unavailable")
        for fetcher in [unavailable, lambda *_: (SVG + b" ", "image/svg+xml")]:
            with self.assertRaises(AssetError):
                self.build(fetcher)
            self.assertFalse(self.output.exists())

    def test_existing_tag_must_match_reviewed_commit(self):
        self.git("tag", self.tag)
        (self.repo / "README.md").write_text("# Notes\n")
        self.git("add", ".")
        self.git("commit", "-qm", "Next revision")
        self.commit = self.git("rev-parse", "HEAD")
        with self.assertRaisesRegex(AssetError, "tag"):
            self.build()

    def test_historical_local_image_packaged(self):
        path = self.repo / "works/sample/manuscript.md"
        path.write_text(path.read_text().replace(URL, "assets/figure.svg"))
        local = path.parent / "assets/figure.svg"
        local.parent.mkdir()
        local.write_bytes(SVG)
        self.git("add", ".")
        self.git("commit", "-qm", "Historical local image")
        self.commit = self.git("rev-parse", "HEAD")
        self.build(fetcher=lambda *_: self.fail("local image fetched"))
        manifest = verify_edition(self.output)
        self.assertEqual("git", manifest["assets"][0]["source_kind"])

    def test_repeated_images_count_toward_html_size(self):
        path = self.repo / "works/sample/manuscript.md"
        path.write_text(path.read_text() + f"\n![Same figure]({URL})\n")
        self.git("add", ".")
        self.git("commit", "-qm", "Reuse the figure")
        self.commit = self.git("rev-parse", "HEAD")
        # One encoded image fits. Reject the second before invoking the HTML writer.
        with patch("asset_policy.MAX_HTML_BYTES", len(base64.b64encode(SVG))), \
             self.assertRaisesRegex(AssetError, "canonical HTML exceeds"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_archived_html_size_checked_before_reading(self):
        fixture = Path(__file__).parent / "fixtures/archived-v1"
        with patch("asset_policy.MAX_HTML_BYTES", 16), self.assertRaisesRegex(AssetError, "canonical HTML exceeds"):
            verify_edition(fixture)

    def test_package_tampering_rejected(self):
        self.build()
        path = self.output / "sample-v1.0.0.html"
        path.write_text(path.read_text().replace("Structural diagram", "Tampered"))
        with self.assertRaises(AssetError):
            verify_edition(self.output)

    def test_provenance_commit_and_tag_checked(self):
        self.build()
        for kwargs in [{"commit": "a" * 40}, {"tag": "works/sample/v2.0.0"}]:
            with self.assertRaises(AssetError):
                verify_edition(self.output, **kwargs)


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for cls in [AssetURLTests, SourceBudgetTests, FetchTests, EditionTests]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    for name in SourceAssetTests.__dict__:
        if name.startswith("test_"):
            suite.addTest(SourceAssetTests(name))
    return suite
