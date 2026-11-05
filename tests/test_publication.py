"""Pre-publication gate and per-push asset verification tests; never publish."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

import test_assets as fixtures
from asset_policy import AssetError
from editorial_notices import render_initial_body
from publish_edition import gh, release_records, require_source_check, publish_edition
from verify_source_assets import verify_source_assets


class PublicationTests(fixtures.EditionTests):
    def test_push_check_downloads_and_verifies_unique_references(self):
        fetcher = unittest.mock.Mock(return_value=(fixtures.SVG, "image/svg+xml"))
        self.assertEqual(1, verify_source_assets(self.repo, fetcher=fetcher))
        fetcher.assert_called_once()
        with self.assertRaisesRegex(AssetError, "SHA-256"):
            verify_source_assets(self.repo, fetcher=lambda *_: (fixtures.SVG + b" ", "image/svg+xml"))

    def test_push_check_rejects_unapproved_destination_before_network(self):
        path = self.repo / "works/sample/manuscript.md"
        path.write_text(path.read_text().replace(fixtures.URL, "https://evil.test/image.svg"))
        fetcher = unittest.mock.Mock()
        with self.assertRaises(AssetError):
            verify_source_assets(self.repo, fetcher=fetcher)
        fetcher.assert_not_called()

class PublicationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.commit = "a" * 40
        self.main_tip = self.commit
        self.origin = self.enterContext(patch("identifier_schema.edition_origin", return_value="https://press.test"))
        self.tag = "works/sample/v1.0.0"
        self.ref = None
        self.release = None
        self.remote = {}
        self.operations = []
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.output = Path(temp.name)
        manifest = {"title": "Test", "rights": "Reserved", "tag": self.tag,
                    "record_url": "https://press.test/" + self.tag, "builder_commit": self.commit,
                    "source_commit": self.commit, "canonical_artifact": "sample-v1.0.0.html"}
        manifest["initial_release_body"] = render_initial_body(manifest)
        self.policy_enabled = True
        for name in [manifest["canonical_artifact"], "provenance.json", "SHA256SUMS"]:
            (self.output / name).write_text(name)
        for name, options in [("verify_edition", {"return_value": manifest}),
                              ("require_source_check", {}), ("check_issuance", {"return_value": {}}),
                              ("tag_commit", {"side_effect": lambda *_: self.ref}),
                              ("gh", {"side_effect": self.fake_gh})]:
            mock = patch("publish_edition." + name, **options)
            setattr(self, name, mock.start())
            self.addCleanup(mock.stop)

    def fake_gh(self, *args, token=None):
        self.operations.append(args)
        if args == ("api", "repos/owner/press/immutable-releases"):
            self.assertEqual("read-settings", token)
            return json.dumps({"enabled": self.policy_enabled})
        self.assertIsNone(token)
        if args[:2] == ("api", "--paginate"):
            return json.dumps([self.release] if self.release else [])
        if args[:3] == ("api", "--method", "POST"):
            params = dict(value.split("=", 1) for value in args[5::2])
            self.release = {"id": 123, "tag_name": params["tag_name"], "draft": True,
                            "prerelease": False, "target_commitish": params["target_commitish"],
                            "assets": [], "body": params["body"], "name": params["name"]}
        elif args[:3] == ("api", "--method", "PATCH"):
            self.assertEqual("repos/owner/press/releases/123", args[3])
            self.assertIn("target_commitish=" + self.commit, args)
            self.release["draft"] = False
            self.release["immutable"] = True
            self.ref = self.release["target_commitish"]
        elif args == ("api", "repos/owner/press/git/ref/heads/main"):
            return json.dumps({"object": {"sha": self.main_tip}})
        elif args[0] == "api":
            self.assertEqual("repos/owner/press/releases/123", args[1])
        elif args[:2] == ("release", "upload"):
            for path in map(Path, args[3:args.index("--repo")]):
                self.remote[path.name] = path.read_bytes()
                self.release["assets"].append({"id": len(self.remote), "name": path.name, "state": "uploaded"})
        elif args[:2] == ("release", "download"):
            name = args[args.index("--pattern") + 1]
            (Path(args[args.index("--dir") + 1]) / name).write_bytes(self.remote[name])
        else:
            self.fail(f"unexpected gh request {args}")
        return json.dumps(self.release)

    def publish(self, issuer=None, policy_token="read-settings"):
        return publish_edition("owner/press", self.output, self.commit, self.tag,
                               issuer_commit=issuer or self.commit, policy_token=policy_token)

    def existing_draft(self):
        """Seed an already-uploaded draft left by an interrupted promotion."""
        from editorial_notices import release_notes
        manifest = self.verify_edition.return_value
        self.remote = {path.name: path.read_bytes() for path in self.output.iterdir()}
        self.release = {"id": 123, "tag_name": self.tag, "draft": True, "prerelease": False,
                        "target_commitish": self.commit, "body": release_notes(manifest),
                        "name": "Test — v1.0.0", "assets": [
                            {"id": idx, "name": name, "state": "uploaded"}
                            for idx, name in enumerate(self.remote, 1)]}

    def test_fresh_publication_reads_back_draft_and_public_bytes(self):
        self.publish()
        self.assertEqual(self.commit, self.ref)
        promotion = next(i for i, args in enumerate(self.operations) if "PATCH" in args)
        self.assertEqual(("api", "repos/owner/press/git/ref/heads/main"), self.operations[promotion - 1])
        for operations in [self.operations[:promotion], self.operations[promotion+1:]]:
            self.assertEqual(3, sum(args[:2] == ("release", "download") for args in operations))

    def test_origin_change_blocks_issuance_but_allows_read_only_verification(self):
        self.origin.return_value = "https://other.test"
        with self.assertRaisesRegex(AssetError, "different permanent record origin"):
            self.publish()
        self.assertIsNone(self.release)
        self.origin.return_value = "https://press.test"
        self.publish()
        self.origin.return_value = "https://other.test"
        self.operations.clear()
        self.publish()
        self.assertFalse(any("--method" in args for args in self.operations))

    def test_promotion_must_use_the_reserved_staging_run(self):
        self.check_issuance.return_value = {"staged_run_id": "42"}
        with self.assertRaisesRegex(AssetError, "staging run named"):
            publish_edition("owner/press", self.output, self.commit, self.tag,
                            issuer_commit=self.commit, staged_run_id="41")
        self.assertIsNone(self.release)
        publish_edition("owner/press", self.output, self.commit, self.tag,
                        issuer_commit=self.commit, policy_token="read-settings", staged_run_id="42")
        self.assertFalse(self.release["draft"])

    def test_initial_notes_and_issuance_are_required_before_any_publish(self):
        manifest = self.verify_edition.return_value
        manifest["initial_release_body"] = render_initial_body(manifest, "Plate I used by permission.\n")
        self.existing_draft()
        self.assertTrue(self.release["body"].endswith("\n\nPlate I used by permission."))
        with patch.dict(self.release, {"body": self.release["body"].replace("used by permission", "is public domain")}):
            with self.assertRaisesRegex(AssetError, "release notes differ"):
                self.publish()
        self.check_issuance.side_effect = AssetError("unapproved")
        with self.assertRaisesRegex(AssetError, "unapproved"):
            self.publish()
        self.assertFalse(any("PATCH" in args for args in self.operations))

    def test_approved_later_notices_are_checked_on_public_retry(self):
        from editorial_notices import edition_notices, release_notes
        self.existing_draft()
        self.publish()
        directory = self.output / "notices/sample/v1.0.0"
        directory.mkdir(parents=True)
        note = "2026-10-10 Correction: corrected attribution"
        (directory / "0001.md").write_text(note + "\n")
        notices = edition_notices(self.output, self.tag)
        self.assertEqual(["- " + note], notices)
        with patch("publish_edition.edition_notices", return_value=notices):
            with self.assertRaisesRegex(AssetError, "release notes differ"):
                self.publish()
            self.release["body"] = release_notes(self.verify_edition.return_value, notices)
            self.publish()
        (directory / "0003.md").write_text(note + "\n")
        with self.assertRaisesRegex(AssetError, "unexpected notice file"):
            edition_notices(self.output, self.tag)

    def test_later_identifiers_are_required_on_public_retry(self):
        from editorial_notices import release_notes
        self.publish()
        initial = self.release["body"]
        identifiers = {"version_doi": "10.1234/x_(a);42", "oclc": "1234"}
        self.check_issuance.return_value = {"identifiers": identifiers, "published_at": "2026-11-04T12:00:00Z"}
        with self.assertRaisesRegex(AssetError, "release notes differ"):
            self.publish()
        self.release["body"] = release_notes(self.verify_edition.return_value, identifiers=identifiers)
        self.assertEqual("## Edition identifiers\n\n- OCLC number: `1234`"
                         "\n- Edition DOI: `10.1234/x_(a);42`\n\n" + initial, self.release["body"])
        self.publish()
        self.assertEqual(initial, release_notes(self.verify_edition.return_value))

    def test_promotion_sends_reviewed_initial_prose(self):
        manifest = self.verify_edition.return_value
        manifest["initial_release_body"] = render_initial_body(manifest, "Plate I © Artist.\n\nUsed by permission.\n")
        self.existing_draft()
        approved = self.release["body"]
        self.publish()
        request = next(args for args in self.operations if "PATCH" in args)
        self.assertIn("body=" + approved, request)
        self.assertIn("Plate I © Artist.\n\nUsed by permission.", approved)

    def test_uncertain_promotion_recovers_without_a_second_write(self):
        self.existing_draft()
        def lose_response(*args, **kwargs):
            result = self.fake_gh(*args, **kwargs)
            if "PATCH" in args:
                raise AssetError("connection closed")
            return result
        self.gh.side_effect = lose_response
        self.publish()
        self.publish()
        self.assertEqual(1, sum("PATCH" in args for args in self.operations))
        self.assertFalse(self.release["draft"])
        self.remote["SHA256SUMS"] = b"wrong"
        with self.assertRaisesRegex(AssetError, "attachments differ"):
            self.publish()

    def test_uncertain_promotion_still_draft(self):
        self.existing_draft()
        def never_applied(*args, **kwargs):
            if "PATCH" in args:
                raise AssetError("connection closed")
            return self.fake_gh(*args, **kwargs)
        self.gh.side_effect = never_applied
        with self.assertRaisesRegex(AssetError, "remains a draft"):
            self.publish()

    def test_denied_promotion_reports_http_status_without_repeating_the_write(self):
        self.existing_draft()
        def denied(*args, **kwargs):
            if "PATCH" in args:
                raise AssetError("gh api PATCH failed (HTTP 403)")
            return self.fake_gh(*args, **kwargs)
        self.gh.side_effect = denied
        with self.assertRaisesRegex(AssetError, r"remains a draft.*HTTP 403"):
            self.publish()
        self.assertTrue(self.release["draft"])

    def test_interrupted_promotion_completes_matching_draft_without_overwriting(self):
        self.existing_draft()
        missing = self.release["assets"].pop()["name"]
        self.publish()
        uploads = [args for args in self.operations if args[:2] == ("release", "upload")]
        self.assertEqual(1, len(uploads))
        self.assertEqual([str(self.output / missing)], list(uploads[0][3:uploads[0].index("--repo")]))
        self.assertFalse(self.release["draft"])

    def test_partial_draft_with_conflicting_bytes_is_left_intact(self):
        self.existing_draft()
        self.release["assets"].pop()
        existing = self.release["assets"][0]["name"]
        self.remote[existing] = b"different proof"
        with self.assertRaisesRegex(AssetError, "attachments differ"):
            self.publish()
        self.assertFalse(any(args[:2] == ("release", "upload") for args in self.operations))
        self.assertTrue(self.release["draft"])

    def test_no_draft_created_without_issuance(self):
        self.check_issuance.side_effect = AssetError("unapproved")
        with self.assertRaisesRegex(AssetError, "unapproved"):
            self.publish()
        self.assertIsNone(self.release)

    def test_public_readback_requires_exact_title_and_immutable_assets(self):
        self.publish()
        for field, value, message in [("name", "Different work", "release title"),
                                      ("immutable", False, "immutable GitHub release")]:
            with self.subTest(field=field), patch.dict(self.release, {field: value}):
                with self.assertRaisesRegex(AssetError, message):
                    self.publish()

    def test_unconfirmed_immutability_leaves_release_in_draft(self):
        self.existing_draft()
        self.policy_enabled = False
        for token, error in [(None, "PRESS_POLICY_READ_TOKEN"), ("read-settings", "enable immutable releases")]:
            with self.subTest(token=token), self.assertRaisesRegex(AssetError, error):
                self.publish(policy_token=token)
        def denied_policy(*args, **kwargs):
            if args[-1].endswith("/immutable-releases"):
                raise AssetError("gh api failed (HTTP 404)")
            return self.fake_gh(*args, **kwargs)
        self.gh.side_effect = denied_policy
        with self.assertRaisesRegex(AssetError, "cannot confirm repository release immutability"):
            self.publish()
        self.assertTrue(self.release["draft"])
        self.assertFalse(any("PATCH" in args for args in self.operations))

    def test_promotion_requires_full_issuer_sha(self):
        for issuer in [None, "main"]:
            with self.subTest(issuer=issuer), self.assertRaisesRegex(AssetError, "full issuer commit"):
                publish_edition("owner/press", self.output, self.commit, self.tag, issuer_commit=issuer)
        self.assertEqual([], self.operations)

    def test_moved_main_prevents_promotion(self):
        self.existing_draft()
        self.main_tip = "b" * 40
        with self.assertRaisesRegex(AssetError, "re-dispatch promotion"):
            self.publish()
        self.assertTrue(self.release["draft"])
        self.assertFalse(any("PATCH" in args for args in self.operations))

    def test_main_moves_during_reviewed_byte_readback(self):
        def move_main(*args, **kwargs):
            result = self.fake_gh(*args, **kwargs)
            if args[:2] == ("release", "download"):
                self.main_tip = "b" * 40
            return result
        self.gh.side_effect = move_main
        with self.assertRaisesRegex(AssetError, "re-dispatch promotion"):
            self.publish()
        self.assertTrue(self.release["draft"])
        self.assertFalse(any("PATCH" in args for args in self.operations))

    def test_distinct_issuer_must_have_passing_checks(self):
        self.existing_draft()
        self.main_tip = "b" * 40
        self.require_source_check.reset_mock()
        self.require_source_check.side_effect = [None, AssetError("issuer check failed")]
        with self.assertRaisesRegex(AssetError, "issuer check failed"):
            self.publish(issuer=self.main_tip)
        self.assertEqual([(self.commit,), (self.main_tip,)],
                         [call.args[1:] for call in self.require_source_check.call_args_list])
        self.assertTrue(self.release["draft"])

    def test_historical_staging_builder_must_still_qualify(self):
        self.verify_edition.return_value["builder_commit"] = "c" * 40
        self.require_source_check.side_effect = [None, AssetError("unverified builder")]
        with self.assertRaisesRegex(AssetError, "unverified builder"):
            self.publish()
        self.assertEqual("c" * 40, self.require_source_check.call_args.args[1])
        self.assertIsNone(self.release)

    def test_invalid_drafts_cannot_be_promoted(self):
        self.existing_draft()
        for field, value in [("assets", self.release["assets"] + [{"id": 4, "name": "stray.pdf", "state": "uploaded"}]),
                             ("body", self.release["body"] + "Rights: other terms\n"),
                             ("name", "Other"), ("target_commitish", "main"), ("draft", False), ("prerelease", True)]:
            with self.subTest(field=field), patch.dict(self.release, {field: value}), self.assertRaises(AssetError):
                self.publish()
        self.remote["SHA256SUMS"] = b"modified"
        with self.assertRaisesRegex(AssetError, "attachments differ"):
            self.publish()
        self.assertTrue(self.release["draft"])
        self.assertFalse(any("PATCH" in args for args in self.operations))

    def test_draft_changes_during_download_are_rejected(self):
        self.existing_draft()
        def replace_attachment(*args, **kwargs):
            result = self.fake_gh(*args, **kwargs)
            if args[:2] == ("release", "download"):
                self.release["assets"][0]["id"] += 1
            return result
        self.gh.side_effect = replace_attachment
        with self.assertRaisesRegex(AssetError, "changed during verification"):
            self.publish()
        self.assertTrue(self.release["draft"])

    def test_postpublication_readback_reports_public_failure(self):
        self.existing_draft()
        def corrupt_after_promotion(*args, **kwargs):
            result = self.fake_gh(*args, **kwargs)
            if "PATCH" in args:
                self.remote["SHA256SUMS"] = b"wrong"
            return result
        self.gh.side_effect = corrupt_after_promotion
        with self.assertRaisesRegex(AssetError, "publication submitted.*attachments differ"):
            self.publish()

    def test_failed_source_check_and_wrong_tag_prevent_mutations(self):
        self.require_source_check.side_effect = AssetError("failed check")
        with self.assertRaisesRegex(AssetError, "failed check"):
            self.publish()
        self.require_source_check.side_effect = None
        self.ref = "b" * 40
        with self.assertRaisesRegex(AssetError, "different commit"):
            self.publish()
        self.assertEqual([], self.operations)


class RequiredCheckTests(unittest.TestCase):
    def test_policy_app_key_and_token_are_limited_to_protected_publication(self):
        root = Path(__file__).resolve().parents[1]
        jobs = yaml.safe_load((root / ".github/workflows/prepare-edition.yml").read_text())["jobs"]
        release = jobs["release"]
        self.assertEqual(release["environment"], "press-release")
        self.assertIn("github.ref == 'refs/heads/main'", release["if"])
        token_step = next(s for s in release["steps"] if s.get("id") == "policy-token")
        self.assertRegex(token_step["uses"], r"^actions/create-github-app-token@[a-f0-9]{40}$")
        inputs = token_step["with"]
        self.assertEqual(inputs["owner"], "${{ github.repository_owner }}")
        self.assertEqual(inputs["repositories"], "${{ github.event.repository.name }}")
        self.assertEqual({k: v for k, v in inputs.items() if k.startswith("permission-")},
                         {"permission-administration": "read"})
        self.assertNotEqual(str(inputs.get("skip-token-revoke", "false")).lower(), "true")
        promotion = next(s for s in release["steps"] if "PRESS_POLICY_READ_TOKEN" in s.get("env", {}))
        self.assertEqual(promotion["env"]["PRESS_POLICY_READ_TOKEN"], "${{ steps.policy-token.outputs.token }}")
        self.assertEqual(promotion["env"]["GH_TOKEN"], "${{ github.token }}")
        for name, job in jobs.items():
            if name != "release":
                self.assertNotIn("PRESS_POLICY_APP_PRIVATE_KEY", json.dumps(job))

    def setUp(self):
        self.commit = "a" * 40
        self.repo = "owner/press"
        self.run = {"id": 42, "run_attempt": 2, "path": ".github/workflows/validate-source.yml",
                    "event": "push", "head_branch": "main", "head_sha": self.commit,
                    "status": "completed", "conclusion": "success",
                    "head_repository": {"full_name": self.repo}}
        self.runs = [self.run]
        self.jobs = [{"name": "Validate manuscript sources", "conclusion": "success",
                      "steps": [{"name": "Verify editorial history against the event base", "conclusion": "success"},
                                {"name": "Verify public edition records against receipts and notices", "conclusion": "success"}]},
                     {"name": "Verify manuscript assets", "conclusion": "success"}]
        self.api = self.enterContext(patch("publish_edition.api", side_effect=self.lookup))

    def lookup(self, repo, path):
        self.assertEqual(self.repo, repo)
        if path == f"compare/{self.commit}...main":
            return {"status": "ahead"}
        if path == f"actions/workflows/validate-source.yml/runs?event=push&branch=main&head_sha={self.commit}&per_page=100":
            return {"total_count": len(self.runs), "workflow_runs": self.runs}
        self.assertEqual("actions/runs/42/attempts/2/jobs?per_page=100", path)
        return {"jobs": self.jobs}

    def test_gh_errors_report_operation_and_http_status_only(self):
        result = subprocess.CompletedProcess([], 1, "", "gh: private detail (HTTP 403)")
        with patch("publish_edition.subprocess.run", return_value=result), self.assertRaises(AssetError) as caught:
            gh("api", "--method", "PATCH", "private-request")
        self.assertEqual("gh api PATCH failed (HTTP 403)", str(caught.exception))

    def test_paginated_release_inventory_works_with_existing_gh(self):
        with patch("publish_edition.gh", return_value='[{"id":1}]\n[{"id":2}]\n'):
            self.assertEqual([{"id":1},{"id":2}], release_records("owner/press"))

    def test_both_jobs_and_history_step_required_in_latest_attempt(self):
        require_source_check(self.repo, self.commit)
        self.jobs[0]["steps"][0]["conclusion"] = "skipped"
        with self.assertRaisesRegex(AssetError, "previous tip"):
            require_source_check(self.repo, self.commit)
        self.jobs[0]["steps"][0]["conclusion"] = "success"
        self.jobs[0]["steps"][1]["conclusion"] = "skipped"
        with self.assertRaisesRegex(AssetError, "public edition records"):
            require_source_check(self.repo, self.commit)
        self.jobs[0]["steps"][1]["conclusion"] = "success"
        self.jobs.pop()
        with self.assertRaisesRegex(AssetError, "re-run all jobs"):
            require_source_check(self.repo, self.commit)

    def test_other_workflow_branch_or_event_cannot_authorize_edition(self):
        for field, value in [("head_branch", "topic"), ("head_sha", "b" * 40),
                             ("head_repository", {"full_name": "fork/press"}), ("event", "workflow_dispatch"),
                             ("path", ".github/workflows/other.yml"), ("event", "pull_request")]:
            with self.subTest(field=field), patch.dict(self.run, {field: value}), self.assertRaises(AssetError):
                require_source_check(self.repo, self.commit)
        self.runs.clear()
        with self.assertRaises(AssetError):
            require_source_check(self.repo, self.commit)

    def test_newer_failed_or_incomplete_attempt_cannot_use_older_success(self):
        for conclusion in ["failure", None]:
            with self.subTest(conclusion=conclusion):
                self.runs[:] = [{**self.run, "id": 100, "conclusion": conclusion}, self.run]
                with self.assertRaisesRegex(AssetError, "latest main push"):
                    require_source_check(self.repo, self.commit)

    def test_sync_can_wait_for_the_exact_push_run_to_appear_and_finish(self):
        self.runs.clear()
        def advance(_):
            if not self.runs:
                self.runs.append({**self.run, "status": "in_progress", "conclusion": None})
            else:
                self.runs[:] = [self.run]
        with patch("publish_edition.time.sleep", side_effect=advance) as sleep:
            require_source_check(self.repo, self.commit, wait_seconds=600)
        self.assertEqual(2, sleep.call_count)

    def test_sync_wait_is_bounded_and_never_retries_terminal_or_untrusted_runs(self):
        self.run.update(status="queued", conclusion=None)
        with patch("publish_edition.time.monotonic", side_effect=[100, 103, 106]), patch("publish_edition.time.sleep") as sleep:
            with self.assertRaisesRegex(AssetError, "expected completed"):
                require_source_check(self.repo, self.commit, wait_seconds=5)
            sleep.assert_called_once_with(2)
        for update in [{"status": "completed", "conclusion": "failure"}, {"status": "queued", "head_branch": "topic"}]:
            with self.subTest(update=update), patch.dict(self.run, update), patch("publish_edition.time.sleep") as sleep:
                with self.assertRaises(AssetError):
                    require_source_check(self.repo, self.commit, wait_seconds=600)
                sleep.assert_not_called()

    def test_commit_must_belong_to_main(self):
        with patch("publish_edition.api", return_value={"status": "diverged"}):
            with self.assertRaises(AssetError):
                require_source_check("owner/press", "a" * 40)

    def test_workflow_requires_main_push_baseline_and_compares_new_branches_with_main(self):
        root = Path(__file__).resolve().parents[1]
        steps = yaml.safe_load((root / ".github/workflows/validate-source.yml").read_text())["jobs"]["source"]["steps"]
        name = self.jobs[0]["steps"][0]["name"]
        script = next(step["run"] for step in steps if step.get("name") == name)
        with tempfile.TemporaryDirectory() as tmp:
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=tmp, text=True).strip()

            git("init", "-q", "-b", "main")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.com",
                "commit", "-q", "--allow-empty", "-m", "initial")
            tip = git("rev-parse", "HEAD")
            git("update-ref", "refs/remotes/origin/main", tip)
            # Capture the history verifier's actual arguments after the workflow selects its base.
            verifier = Path(tmp) / ".venv/bin/python"
            verifier.parent.mkdir(parents=True)
            verifier.write_text('#!/bin/sh\necho "$@"\n')
            verifier.chmod(0o755)
            env = {**os.environ, "GITHUB_EVENT_NAME": "push", "GITHUB_REPOSITORY": self.repo}
            for branch, before, succeeds in [("main", "", False), ("main", "0" * 40, False),
                                              ("topic", "0" * 40, True)]:
                with self.subTest(branch=branch, before=before):
                    result = subprocess.run(["bash", "-e", "-c", script], cwd=tmp, capture_output=True, text=True,
                                            env={**env, "BASE_SHA": before, "GITHUB_REF": "refs/heads/" + branch})
                    self.assertEqual(succeeds, result.returncode == 0)
                    if succeeds:
                        self.assertIn("--base " + tip, result.stdout)


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(RequiredCheckTests))
    suite.addTests(loader.loadTestsFromTestCase(PublicationLifecycleTests))
    for name in PublicationTests.__dict__:
        if name.startswith("test_"):
            suite.addTest(PublicationTests(name))
    return suite
