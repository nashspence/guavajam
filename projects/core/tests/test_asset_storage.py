"""Asset storage, retention and exact-tip retirement against isolated fixtures."""
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import unquote

import yaml

from guavajam_core.asset_policy import AssetError
from guavajam_core import asset_storage
from guavajam_core.asset_storage import B2, GRACE_DAYS, LEASE_PREFIX, credentials
from guavajam_core import asset_workflow
from guavajam_core.configure_asset_maintenance import configure, deletion_ruleset
from guavajam_core.manage_assets import candidates, collect, fingerprints, git, require_action, retained_assets, upload


from guavajam_test_support import FIXTURES, RESOURCES
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><title>Asset fixture</title></svg>\n'
DIGEST = hashlib.sha256(SVG).hexdigest()
KEY = "sha256/" + DIGEST + ".svg"
ORIGIN = "https://assets.publisher.test"
NOW = 2_000_000_000
OLD = int((NOW - (GRACE_DAYS + 1) * 86400) * 1000)
VALUES = {"B2_APPLICATION_KEY_ID": "fixture-id", "B2_APPLICATION_KEY": "fixture-private-value",
          "B2_BUCKET_ID": "fixture-bucket-id", "B2_BUCKET_NAME": "fixture-bucket"}


def version(key=KEY, *, stamp=OLD, action="upload", file_id="version-one"):
    return {"bucketId": VALUES["B2_BUCKET_ID"], "fileName": key, "fileId": file_id,
            "uploadTimestamp": stamp, "action": action, "contentLength": len(SVG) if action == "upload" else 0}


class MemoryB2:
    def __init__(self, records=()):
        self.records = list(records)
        self.bytes = {KEY: SVG} if any(i["fileName"] == KEY for i in records) else {}
        self.deleted, self.uploads = [], []

    def versions(self, prefix="sha256/"):
        return [dict(i) for i in self.records if i["fileName"].startswith(prefix)]

    def upload(self, key, raw, media_type, **kwargs):
        self.uploads.append((key, raw, media_type, kwargs))
        self.bytes[key] = raw
        self.records.append(version(key, stamp=int(NOW * 1000), file_id="new-" + str(len(self.uploads))))

    def verify(self, key, raw, media_type):
        if self.bytes.get(key) != raw:
            raise AssetError("origin differs")

    def delete(self, item):
        self.deleted.append(item)
        self.records = [i for i in self.records if i["fileId"] != item["fileId"]]


def authorization(caps=("listFiles", "writeFiles", "readFiles", "readFileRetentions")):
    return {"authorizationToken": "fixture-access-token", "apiInfo": {"storageApi": {
        "apiUrl": "https://api001.backblazeb2.com", "downloadUrl": "https://f001.backblazeb2.com",
        "allowed": {"buckets": [{"id": VALUES["B2_BUCKET_ID"], "name": VALUES["B2_BUCKET_NAME"]}],
                    "namePrefix": "sha256/", "capabilities": list(caps)}}}}


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root / "asset-policy.json").write_text(json.dumps({"origin": ORIGIN}))
        self.image = self.root / "a figure.svg"
        self.image.write_bytes(SVG)

    def test_private_env_is_data_and_never_executes_shell(self):
        path = self.root / ".env.assets"
        payload = "$(touch forbidden)"
        path.write_text("\n".join(f"{k}='{payload if k == 'B2_APPLICATION_KEY' else v}'" for k, v in VALUES.items()))
        path.chmod(0o600)
        loaded = credentials(self.root, {})
        self.assertEqual(payload, loaded["B2_APPLICATION_KEY"])
        self.assertFalse((self.root / "forbidden").exists())
        path.chmod(0o644)
        with self.assertRaises(AssetError):
            credentials(self.root, {})
        path.unlink()
        path.symlink_to(self.image)
        with self.assertRaises(AssetError):
            credentials(self.root, {})

    def test_credential_file_refuses_unknown_fields_and_missing_values(self):
        with self.assertRaises(AssetError):
            credentials(self.root, {})
        path = self.root / ".env.assets"
        path.write_text("CLOUDFLARE_API_TOKEN=fixture\n")
        path.chmod(0o600)
        with self.assertRaises(AssetError):
            credentials(self.root, {})

    def test_b2_scope_is_one_bucket_with_optional_exact_prefix_and_required_capabilities(self):
        for operation, caps in [("upload", ("listFiles", "writeFiles", "readFiles", "readFileRetentions")),
                                ("delete", ("listFiles", "deleteFiles", "readFileRetentions")), ("preview", ("listFiles", "readFileRetentions"))]:
            with patch("guavajam_core.asset_storage.request", return_value=authorization(caps)):
                self.assertEqual(VALUES["B2_BUCKET_ID"], B2(VALUES, operation=operation).bucket_id)
        for field, value in [("buckets", None), ("buckets", []), ("namePrefix", "sha256/x"),
                             ("capabilities", ["writeFiles"])]:
            data = authorization()
            data["apiInfo"]["storageApi"]["allowed"][field] = value
            with self.subTest(field=field, value=value), patch("guavajam_core.asset_storage.request", return_value=data), self.assertRaises(AssetError):
                B2(VALUES, operation="upload")

    def test_shared_read_write_key_is_accepted_but_client_modes_limit_calls(self):
        data = authorization(("listFiles", "writeFiles", "deleteFiles", "readFiles", "readFileRetentions", "readBuckets", "writeBuckets"))
        with patch("guavajam_core.asset_storage.request", return_value=data):
            uploader = B2(VALUES, operation="upload")
            preview = B2(VALUES, operation="preview")
            deleter = B2(VALUES, operation="delete")
        for store, operation in [(uploader, "b2_delete_file_version"), (preview, "b2_get_upload_url"),
                                 (preview, "b2_delete_file_version"), (deleter, "b2_get_upload_url"),
                                 (uploader, "b2_update_bucket")]:
            with self.subTest(operation=operation), self.assertRaises(AssetError):
                store.call(operation)

    def test_b2_service_urls_cannot_send_credentials_elsewhere(self):
        for url in ["https://evil.test", "https://api001.backblazeb2.com.evil.test", "http://api.backblazeb2.com",
                    "https://user@api.backblazeb2.com", "https://api.backblazeb2.com:443", "https://api.backblazeb2.com/#x"]:
            with self.subTest(url=url), self.assertRaises(AssetError):
                asset_storage.b2_url(url)

    def test_b2_http_failures_do_not_log_service_body_or_token(self):
        error = asset_storage.urllib.error.HTTPError("https://api.backblazeb2.com", 403,
                    VALUES["B2_APPLICATION_KEY"], {}, io.BytesIO(VALUES["B2_APPLICATION_KEY"].encode()))
        with patch("guavajam_core.asset_storage.urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(AssetError) as raised:
                asset_storage.request("https://api.backblazeb2.com", VALUES["B2_APPLICATION_KEY"])
        self.assertNotIn(VALUES["B2_APPLICATION_KEY"], str(raised.exception))
        self.assertIn("HTTP 403", str(raised.exception))
        self.assertIsNone(asset_storage.NoRedirects().redirect_request(None, None, 302, None, None, None))

    def test_version_pagination_is_complete_and_rejects_ambiguous_inventory(self):
        with patch("guavajam_core.asset_storage.request", return_value=authorization()):
            store = B2(VALUES, operation="upload")
        second = version(file_id="version-two")
        pages = [{"files": [version()], "nextFileName": KEY, "nextFileId": "version-two"},
                 {"files": [second], "nextFileName": None, "nextFileId": None}]
        with patch.object(store, "call", side_effect=pages) as call:
            self.assertEqual(2, len(store.versions()))
            self.assertEqual("version-two", call.call_args.kwargs["startFileId"])
        for page in [{"files": []}, {"files": [version(), version()], "nextFileName": None, "nextFileId": None},
                     {"files": [version("other/key")], "nextFileName": None, "nextFileId": None},
                     {"files": [], "nextFileName": "outside/prefix", "nextFileId": "x"}]:
            with self.subTest(page=page), patch.object(store, "call", return_value=page), self.assertRaises(AssetError):
                store.versions()

    def test_upload_preserves_bytes_and_reuse_renews_only_lease(self):
        store = MemoryB2()
        fetcher = Mock(return_value=(SVG, "image/svg+xml"))
        self.assertEqual(("Uploaded", ORIGIN + "/" + KEY), upload(self.root, self.image, store, fetcher=fetcher))
        self.assertEqual("Already stored", upload(self.root, self.image, store, fetcher=fetcher)[0])
        self.assertEqual([LEASE_PREFIX + KEY[7:], KEY, LEASE_PREFIX + KEY[7:]], [i[0] for i in store.uploads])
        self.assertEqual(SVG, store.uploads[1][1])
        self.assertEqual(2, fetcher.call_count)

    def test_upload_refuses_corrupt_existing_bytes_hidden_files_and_public_mismatch(self):
        store = MemoryB2([version()])
        store.bytes[KEY] = SVG + b" "
        with self.assertRaises(AssetError):
            upload(self.root, self.image, store, fetcher=Mock())
        self.assertNotIn(KEY, [i[0] for i in store.uploads])
        with self.assertRaises(AssetError):
            upload(self.root, self.image, MemoryB2([version(action="hide")]), fetcher=Mock())
        with self.assertRaises(AssetError):
            upload(self.root, self.image, MemoryB2(), fetcher=lambda *args: (SVG + b" ", "image/svg+xml"))

    def test_invalid_local_file_never_mutates_storage(self):
        store = Mock()
        self.image.write_bytes(b"not an image")
        with self.assertRaises(AssetError):
            upload(self.root, self.image, store)
        store.upload.assert_not_called()
        self.image.unlink()
        self.image.symlink_to(self.root / "asset-policy.json")
        with self.assertRaises(OSError):
            upload(self.root, self.image, store)
        store.upload.assert_not_called()

    def test_b2_upload_sends_original_bytes_and_verifies_its_receipt(self):
        with patch("guavajam_core.asset_storage.request", return_value=authorization()):
            store = B2(VALUES, operation="upload")
        endpoint = {"bucketId": VALUES["B2_BUCKET_ID"], "uploadUrl": "https://pod-001.backblaze.com/b2api/v4/b2_upload_file",
                    "authorizationToken": "fixture-upload-token"}
        receipt = {"bucketId": VALUES["B2_BUCKET_ID"], "fileName": KEY, "contentLength": len(SVG),
                   "contentSha1": hashlib.sha1(SVG).hexdigest()}
        with patch.object(store, "call", return_value=endpoint), patch("guavajam_core.asset_storage.request", return_value=receipt) as send:
            store.upload(KEY, SVG, "image/svg+xml")
        self.assertEqual(SVG, send.call_args.kwargs["body"])
        headers = send.call_args.kwargs["headers"]
        self.assertEqual(hashlib.sha1(SVG).hexdigest(), headers["X-Bz-Content-Sha1"])
        self.assertEqual(str(len(SVG)), headers["Content-Length"])
        self.assertNotIn("Content-Encoding", headers)
        self.assertEqual(asset_storage.CACHE_CONTROL, unquote(headers["X-Bz-Info-b2-cache-control"]))
        with patch.object(store, "call", return_value=endpoint), \
             patch("guavajam_core.asset_storage.request", return_value={**receipt, "contentSha1": "wrong"}), self.assertRaises(AssetError):
            store.upload(KEY, SVG, "image/svg+xml")

    def test_delete_uses_exact_version_without_governance_bypass(self):
        with patch("guavajam_core.asset_storage.request", return_value=authorization(("listFiles", "deleteFiles", "readFileRetentions"))):
            store = B2(VALUES, operation="delete")
        with patch.object(store, "call", return_value={"fileName": KEY, "fileId": "exact-version"}) as call:
            store.delete(version(file_id="exact-version"))
        self.assertEqual({"fileName": KEY, "fileId": "exact-version"}, call.call_args.kwargs)


class CollectionTests(unittest.TestCase):
    def run_gc(self, store, scan=lambda *args: set(), **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return collect(Path("."), "publisher/press", store, now=NOW, scan=scan, **kwargs)

    def test_shared_digest_and_recent_asset_or_lease_protect_all_versions(self):
        recent = int((NOW - 1) * 1000)
        for records, retained in [([version(), version(KEY.replace(".svg", ".png"), file_id="png")], {DIGEST}),
                                  ([version(stamp=recent)], set()),
                                  ([version(), version(LEASE_PREFIX + KEY[7:], stamp=recent, file_id="lease")], set()),
                                  ([version(action="start")], set())]:
            with self.subTest(records=records):
                self.assertEqual({}, candidates(records, retained, NOW))

    def test_preview_does_not_delete_and_apply_removes_exact_versions_and_markers(self):
        records = [version(file_id="old"), version(action="hide", stamp=OLD + 1, file_id="marker"),
                   version(LEASE_PREFIX + KEY[7:], file_id="lease"), version("unrecognized/key", file_id="other")]
        store = MemoryB2(records)
        self.assertEqual(0, self.run_gc(store))
        self.assertEqual([], store.deleted)
        self.assertEqual(3, self.run_gc(store, apply=True))
        self.assertEqual({"old", "marker", "lease"}, {i["fileId"] for i in store.deleted})
        self.assertEqual("marker", store.deleted[-1]["fileId"])
        self.assertEqual(["other"], [i["fileId"] for i in store.records])

    def test_apply_aborts_when_reference_or_lease_appears(self):
        store = MemoryB2([version()])
        with self.assertRaises(AssetError):
            self.run_gc(store, scan=Mock(side_effect=[set(), {DIGEST}]), apply=True)
        self.assertEqual([], store.deleted)
        store.versions = Mock(side_effect=[[version()], [version(), version(LEASE_PREFIX + KEY[7:], stamp=int(NOW * 1000), file_id="lease")]])
        with self.assertRaises(AssetError):
            self.run_gc(store, apply=True)
        self.assertEqual([], store.deleted)

    def test_storage_or_reference_failures_never_authorize_deletion(self):
        for where in ["scan", "storage"]:
            store = MemoryB2([version()])
            scan = Mock(side_effect=AssetError("unavailable")) if where == "scan" else Mock(return_value=set())
            if where == "storage":
                store.versions = Mock(side_effect=AssetError("unavailable"))
            with self.assertRaises(AssetError):
                self.run_gc(store, scan=scan, apply=True)
            self.assertEqual([], store.deleted)

    def test_apply_gate_requires_trusted_main_workflow(self):
        env = {"GITHUB_ACTIONS": "true", "GITHUB_REF": "refs/heads/main", "GITHUB_REPOSITORY": "publisher/press",
               "GITHUB_WORKFLOW_REF": "publisher/press/.github/workflows/maintain-assets.yml@refs/heads/main",
               "GITHUB_EVENT_NAME": "workflow_dispatch"}
        with patch.dict(os.environ, env, clear=True):
            require_action("publisher/press")
        with patch.dict(os.environ, {**env, "GITHUB_EVENT_NAME": "schedule"}, clear=True), self.assertRaises(AssetError):
            require_action("publisher/press")
        for name in env:
            with patch.dict(os.environ, {**env, name: "untrusted"}, clear=True), self.assertRaises(AssetError):
                require_action("publisher/press")


class GitRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.origin, self.root = self.temp / "origin.git", self.temp / "source"
        subprocess.run(["git", "init", "--bare", "-q", str(self.origin)], check=True, capture_output=True)
        self.root.mkdir()
        self.g("init", "-q", "-b", "main")
        self.g("config", "user.email", "fixture@publisher.test")
        self.g("config", "user.name", "Fixture")
        self.g("remote", "add", "origin", str(self.origin))
        (self.root / ".gitignore").write_text(".env*\n")
        self.commit("initial")

    def g(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, stderr=subprocess.DEVNULL).decode().strip()

    def commit(self, message):
        self.g("add", ".")
        self.g("commit", "-qm", message)
        return self.g("rev-parse", "HEAD")

    def test_history_branch_pr_index_and_untracked_roots_without_executing_code(self):
        hashes = [str(i) * 64 for i in range(1, 9)]
        (self.root / "manuscript.md").write_text(ORIGIN + "/sha256/" + hashes[0] + ".svg")
        self.commit("historical image")
        (self.root / "manuscript.md").unlink()
        (self.root / "provenance.json").write_text(json.dumps({"assets": [{"sha256": hashes[1]}], "withdrawn": True}))
        main = self.commit("withdrawn proof")
        self.g("push", "-q", "origin", "main")
        self.g("switch", "-qc", "active")
        (self.root / "branch.md").write_text(hashes[2])
        branch = self.commit("active branch image")
        self.g("switch", "-q", "main")
        self.g("switch", "-qc", "fork-pr")
        (self.root / "fork.md").write_text(hashes[3])
        (self.root / "malicious.py").write_text("raise AssertionError('PR code must never execute')")
        pr = self.commit("fork PR")
        self.g("push", "-q", "origin", "HEAD:refs/pull/9/head")
        self.g("switch", "-q", "main")
        self.g("branch", "-D", "fork-pr")
        (self.root / "staged.md").write_text(hashes[4])
        self.g("add", "staged.md")
        (self.root / "staged.md").write_text(hashes[5])
        (self.root / "untracked.md").write_text(hashes[6])
        (self.root / ".env.assets").write_text(hashes[7])
        roots = {"branch:main": main, "branch:active": branch, "pr:9": pr}
        with patch("guavajam_core.manage_assets.remote_roots", return_value=roots):
            retained = retained_assets(self.root, "publisher/press")
        self.assertTrue(set(hashes[:7]) <= retained)
        self.assertNotIn(hashes[7], retained)

    def test_reference_normalization_retains_json_escapes_and_markdown_entities(self):
        encoded = DIGEST.replace("a", "&#97;")
        self.assertIn(DIGEST, fingerprints(encoded.encode()))
        self.assertIn(DIGEST, fingerprints(DIGEST.replace("a", "\\u0061").encode()))

    def test_remote_drift_and_shallow_history_fail_closed(self):
        main = self.g("rev-parse", "HEAD")
        self.g("push", "-q", "origin", "main")
        roots = {"branch:main": main}
        with patch("guavajam_core.manage_assets.remote_roots", side_effect=[roots, {**roots, "pr:1": main}]), self.assertRaises(AssetError):
            retained_assets(self.root, "publisher/press")
        original = git
        def shallow(root, *args, **kwargs):
            return b"true\n" if args == ("rev-parse", "--is-shallow-repository") else original(root, *args, **kwargs)
        with patch("guavajam_core.manage_assets.git", side_effect=shallow), self.assertRaises(AssetError):
            retained_assets(self.root, "publisher/press")

    @contextlib.contextmanager
    def retirement(self, head):
        original_run = subprocess.run
        def read_ref(args, **kwargs):
            if args[:2] == ["gh", "api"]:
                return subprocess.CompletedProcess(args, 0, json.dumps({
                    "ref": "refs/heads/retire-me", "object": {"sha": head}}), "")
            return original_run(args, **kwargs)
        pushes = []
        def local_git(root, *args, **kwargs):
            if "push" in args:
                pushes.append(args)
            args = tuple(str(self.origin) if arg == "https://github.com/publisher/press.git" else arg for arg in args)
            return git(root, *args, **kwargs)
        with patch("guavajam_core.asset_workflow.gh", return_value=json.dumps({"default_branch": "main"})), \
             patch("guavajam_core.asset_workflow.pages", return_value=[]), \
             patch("guavajam_core.asset_workflow.git", side_effect=local_git), \
             patch("guavajam_core.asset_workflow.subprocess.run", side_effect=read_ref), \
             contextlib.redirect_stdout(io.StringIO()):
            yield pushes

    def signed_branch(self, message):
        key = self.temp / "signer"
        if not key.exists():
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                           check=True, capture_output=True)
        self.g("config", "gpg.format", "ssh")
        self.g("config", "user.signingkey", str(key))
        self.g("switch", "-qc", "retire-me")
        self.g("commit", "--allow-empty", "-S", "-qm", "Publish\n\n" + message)
        head = self.g("rev-parse", "HEAD")
        self.g("push", "-q", "origin", "main", "retire-me")
        return head

    def test_signed_publications_must_reach_main_before_compare_and_delete(self):
        from guavajam_core.publication_protocol import publication_trailers
        for label in (None, "Initial edition"):
            with self.subTest(label=label):
                head = self.signed_branch("\n".join(publication_trailers(
                    "works/field-notes/manuscript.md", "2026-11-13", label)))
                with self.retirement(head) as pushes:
                    with self.assertRaisesRegex(AssetError, "would orphan a declared publication"):
                        asset_workflow.retire(self.root, "publisher/press", "retire-me", head, "fixture-token")
                    self.assertEqual([], pushes)
                retained = subprocess.check_output(["git", "--git-dir", str(self.origin), "rev-parse", "retire-me"]).decode().strip()
                self.assertEqual(head, retained)
                self.g("switch", "-q", "main")
                self.g("merge", "--ff-only", "retire-me")
                self.g("push", "-q", "origin", "main")
                with self.retirement(head) as pushes:
                    asset_workflow.retire(self.root, "publisher/press", "retire-me", head, "fixture-token")
                    self.assertEqual(1, len(pushes))
                    self.assertIn("--force-with-lease=refs/heads/retire-me:" + head, pushes[0])
                result = subprocess.run(["git", "--git-dir", str(self.origin), "show-ref", "--verify", "refs/heads/retire-me"], capture_output=True)
                self.assertNotEqual(0, result.returncode)
                self.g("branch", "-D", "retire-me")

    def test_malformed_signed_declaration_stops_retirement_before_push(self):
        head = self.signed_branch("Press-Publication: 1\nPress-Work: works/field-notes/manuscript.md")
        with self.retirement(head) as pushes:
            with self.assertRaisesRegex(AssetError, "cannot classify a signed Press declaration"):
                asset_workflow.retire(self.root, "publisher/press", "retire-me", head, "fixture-token")
            self.assertEqual([], pushes)

    def test_site_checkpoint_alone_can_be_retired(self):
        head = self.signed_branch("Press-Site: 1")
        with self.retirement(head) as pushes:
            asset_workflow.retire(self.root, "publisher/press", "retire-me", head, "fixture-token")
            self.assertEqual(1, len(pushes))

    def test_compare_and_delete_refuses_concurrent_branch_push(self):
        first = self.g("rev-parse", "HEAD")
        self.g("branch", "retire-me")
        self.g("push", "-q", "origin", "main", "retire-me")
        (self.root / "new.md").write_text("A concurrent change")
        second = self.commit("new tip")
        def push(root, *args, **kwargs):
            self.g("push", "-q", "origin", "HEAD:refs/heads/retire-me")
            args = tuple(str(self.origin) if arg == "https://github.com/publisher/press.git" else arg for arg in args)
            return git(root, *args, **kwargs)
        original_run = subprocess.run
        def read_ref(args, **kwargs):
            if args[:2] == ["gh", "api"]:
                return subprocess.CompletedProcess(args, 0, json.dumps({"ref": "refs/heads/retire-me", "object": {"sha": first}}), "")
            return original_run(args, **kwargs)
        with patch("guavajam_core.asset_workflow.gh", return_value=json.dumps({"default_branch": "main"})), \
             patch("guavajam_core.asset_workflow.pages", return_value=[]), patch("guavajam_core.asset_workflow.git", side_effect=push), \
             patch("guavajam_core.asset_workflow.subprocess.run", side_effect=read_ref), self.assertRaises(AssetError):
            asset_workflow.retire(self.root, "publisher/press", "retire-me", first, "fixture-token")
        actual = subprocess.check_output(["git", "--git-dir", str(self.origin), "rev-parse", "retire-me"]).decode().strip()
        self.assertEqual(second, actual)

    def test_compare_and_delete_succeeds_only_with_the_separate_app_token(self):
        head = self.g("rev-parse", "HEAD")
        self.g("branch", "retire-me")
        self.g("push", "-q", "origin", "main", "retire-me")
        original_run = subprocess.run
        def read_ref(args, **kwargs):
            if args[:2] == ["gh", "api"]:
                return subprocess.CompletedProcess(args, 0, json.dumps({"ref": "refs/heads/retire-me", "object": {"sha": head}}), "")
            return original_run(args, **kwargs)
        observed = []
        def push(root, *args, **kwargs):
            if "push" not in args:return git(root,*args,**kwargs)
            observed.append((args, kwargs["env"]["GH_TOKEN"]))
            args = tuple(str(self.origin) if arg == "https://github.com/publisher/press.git" else arg for arg in args)
            return git(root, *args, **kwargs)
        with patch("guavajam_core.asset_workflow.gh", return_value=json.dumps({"default_branch": "main"})), \
             patch("guavajam_core.asset_workflow.pages", return_value=[]), patch("guavajam_core.asset_workflow.git", side_effect=push), \
             patch("guavajam_core.asset_workflow.subprocess.run", side_effect=read_ref), contextlib.redirect_stdout(io.StringIO()):
            asset_workflow.retire(self.root, "publisher/press", "retire-me", head, "fixture-app-token")
        self.assertEqual("fixture-app-token", observed[0][1])
        self.assertNotIn("fixture-app-token", " ".join(observed[0][0]))
        self.assertIn("--force-with-lease=refs/heads/retire-me:" + head, observed[0][0])
        result = subprocess.run(["git", "--git-dir", str(self.origin), "show-ref", "--verify", "refs/heads/retire-me"], capture_output=True)
        self.assertNotEqual(0, result.returncode)


class WorkflowTests(unittest.TestCase):
    def test_credential_starter_has_exactly_the_operator_variables(self):
        root = Path(__file__).resolve().parents[1]
        lines = (RESOURCES / "storage/credentials.env.example").read_text().splitlines()
        self.assertEqual(asset_storage.VARIABLES, {line.split("=", 1)[0] for line in lines if line and not line.startswith("#")})
    def test_dispatch_always_uses_main_and_does_not_need_storage_credentials(self):
        with patch("guavajam_core.asset_workflow.repository", return_value="publisher/press"), \
             patch("guavajam_core.asset_workflow.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run, \
             contextlib.redirect_stdout(io.StringIO()):
            asset_workflow.request(Path("."), "request-gc", {})
        self.assertEqual(["gh", "api", "--method", "POST"], run.call_args.args[0][:4])
        self.assertEqual({"ref": "main", "inputs": {"operation": "preview"}}, json.loads(run.call_args.kwargs["input"]))

    def test_retire_command_requires_no_storage_inventory_or_credentials(self):
        repo = "publisher/press"
        head = "a" * 40
        env = {"GITHUB_ACTIONS": "true", "GITHUB_REF": "refs/heads/main", "GITHUB_REPOSITORY": repo,
               "GITHUB_WORKFLOW_REF": repo + "/.github/workflows/maintain-assets.yml@refs/heads/main",
               "GITHUB_EVENT_NAME": "workflow_dispatch", "RETIRE_BRANCH": "feature", "RETIRE_HEAD_SHA": head,
               "PRESS_ASSET_RETIRE_TOKEN": "fixture"}
        with patch.dict(os.environ, env, clear=True), patch.object(sys, "argv", ["guavajam_core.asset_workflow.py", "retire"]), \
             patch("guavajam_core.asset_workflow.repository_root", return_value=Path(".")), patch("guavajam_core.asset_workflow.repository", return_value=repo), patch("guavajam_core.asset_workflow.retire") as retire:
            self.assertEqual(0, asset_workflow.main())
        retire.assert_called_once_with(Path("."), repo, "feature", head, "fixture")

    def test_main_and_open_prs_cannot_be_retired(self):
        with self.assertRaises(AssetError):
            asset_workflow.retire(Path("."), "publisher/press", "main", "a" * 40, "fixture")
        pr = {"head": {"repo": {"full_name": "publisher/press"}, "ref": "active"}, "base": {"ref": "main"}}
        with patch("guavajam_core.asset_workflow.gh", return_value=json.dumps({"default_branch": "main"})), \
             patch("guavajam_core.asset_workflow.pages", return_value=[pr]), self.assertRaises(AssetError):
            asset_workflow.retire(Path("."), "publisher/press", "active", "a" * 40, "fixture")


    def test_deletion_ruleset_cannot_grant_a_main_protection_bypass(self):
        rule = deletion_ruleset(123)
        self.assertEqual([{"type": "deletion"}], rule["rules"])
        self.assertEqual(["~ALL"], rule["conditions"]["ref_name"]["include"])
        app = {"id": 123, "permissions": {"contents": "write", "metadata": "read"}}
        with patch("guavajam_core.configure_asset_maintenance.gh", return_value=json.dumps(app)), \
             patch("guavajam_core.configure_asset_maintenance.pages", return_value=[]), \
             patch("guavajam_core.configure_asset_maintenance.subprocess.run") as run, self.assertRaises(AssetError):
            configure("publisher/press", "fixture", apply=True)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(loader.loadTestsFromName(name,cls) for cls in (StorageTests,CollectionTests,GitRetentionTests,WorkflowTests,) for name in cls.__dict__ if name.startswith("test_"))
