"""Public release inventory and notices checked by the scheduled archive audit."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from asset_policy import AssetError
from audit_assets import audit, main, verify_public_release
from editorial_notices import render_initial_body


class ArchiveAuditTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        manifest = {"title": "Sample", "tag": "works/sample/v1.0.0", "source_commit": "a" * 40,
                    "rights": "all-rights-reserved", "canonical_artifact": "sample-v1.0.0.html",
                    "assets": [], "record_url": "https://press.example.org/works/sample/v1.0.0"}
        manifest["initial_release_body"] = render_initial_body(manifest)
        names = [manifest["canonical_artifact"], "provenance.json", "SHA256SUMS"]
        self.manifest = manifest
        self.record_path = Path("registrations/editions/sample/v1.0.0.json")
        self.release = {"tag_name": manifest["tag"], "name": "Sample — v1.0.0", "immutable": True, "draft": False, "prerelease": False, "published_at": "2026-11-04T13:00:00Z",
                        "body": manifest["initial_release_body"],
                        "assets": [{"name": name, "id": i, "state": "uploaded"}
                                   for i, name in enumerate(names, 1)]}
        self.work_check = self.enterContext(patch("audit_assets.verify_published_work"))
        self.reader_check = self.enterContext(patch("audit_assets.verify_reader_routes"))
        for name, result in (("release_records", [self.release]), ("gh", ""),
                             ("verify_edition", manifest), ("tag_commit", manifest["source_commit"]),
                             ("edition_notices", []),
                             ("git_sources", [self.record_path]), ("check_issuance", {}),
                             ("read_registration", {"published_at": self.release["published_at"]})):
            self.enterContext(patch("audit_assets." + name, return_value=result))

    def test_extra_public_attachment(self):
        self.release["assets"].append({"name": "draft.pdf", "id": 4, "state": "uploaded"})
        with self.assertRaisesRegex(AssetError, "unexpected or incomplete release attachment"):
            audit(self.root, "owner/press")

    def test_public_rights_contradict_verified_manifest(self):
        self.release["body"] += "\nRights: CC0-1.0"
        with self.assertRaisesRegex(AssetError, "release notes differ"):
            audit(self.root, "owner/press")

    def test_missing_public_attachment(self):
        self.release["assets"].pop()
        with self.assertRaisesRegex(AssetError, "missing release attachments"):
            audit(self.root, "owner/press")

    def test_release_id_reads_one_published_release(self):
        with patch.object(sys, "argv", ["audit_assets.py", "--repo", "owner/press", "--release-id", "123"]), \
             patch("audit_assets.api", return_value=self.release) as api, \
             patch("audit_assets.release_records") as releases:
            self.assertEqual(0, main())
            api.assert_called_once_with("owner/press", "releases/123")
            releases.assert_not_called()
            self.reader_check.assert_called_once()

    def test_explicit_verification_rejects_draft_and_prerelease(self):
        for state in ("draft", "prerelease"):
            with self.subTest(state=state), self.assertRaisesRegex(AssetError, "published official edition"):
                verify_public_release("owner/press", dict(self.release, **{state: True}))

    def test_registered_release_disappears(self):
        with patch("audit_assets.release_records", return_value=[]):
            with self.assertRaisesRegex(AssetError, "missing registered edition: works/sample/v1.0.0"):
                audit(self.root, "owner/press")

    def test_registered_release_becomes_prerelease(self):
        self.release["prerelease"] = True
        with self.assertRaisesRegex(AssetError, "missing registered edition"):
            audit(self.root, "owner/press")

    def test_unresolved_intent_fails_even_if_remote_release_has_disappeared(self):
        with patch("audit_assets.release_records", return_value=[]), patch("audit_assets.read_registration", return_value={}):
            with self.assertRaisesRegex(AssetError, "Unresolved publication intent.*works/sample/v1.0.0"):
                audit(self.root, "owner/press")

    def test_cancelled_intent_needs_no_public_release(self):
        with patch("audit_assets.release_records", return_value=[]), patch("audit_assets.read_registration", return_value={"cancellation_reason": "Retired"}):
            audit(self.root, "owner/press")

    def test_public_edition_must_have_independent_registration(self):
        with patch("audit_assets.git_sources", return_value=[]):
            with self.assertRaisesRegex(AssetError, "missing issuance record for public release"):
                audit(self.root, "owner/press")

    def test_audit_requires_exact_committed_notices(self):
        notices = ["- 2026-11-04 Correction: Equation 2 uses the real numbers."]
        with patch("audit_assets.edition_notices", return_value=notices):
            with self.assertRaisesRegex(AssetError, "release notes differ"):
                audit(self.root, "owner/press")
            self.release["body"] = "## Editorial notices\n\n" + notices[0] + "\n\n" + self.release["body"]
            audit(self.root, "owner/press")

    def test_public_audit_requires_registered_identifiers_in_body(self):
        with patch("audit_assets.check_issuance", return_value={"identifiers": {"oclc": "42"}}):
            with self.assertRaisesRegex(AssetError, "release notes differ"):
                audit(self.root, "owner/press")
            self.release["body"] = "## Edition identifiers\n\n- OCLC number: `42`\n\n" + self.release["body"]
            audit(self.root, "owner/press")

    def test_public_audit_requires_completed_receipt(self):
        with patch("audit_assets.check_issuance", return_value={}) as check:
            audit(self.root, "owner/press")
        self.assertTrue(check.call_args.kwargs["require_receipt"])
        self.work_check.assert_called_once()

    def test_audit_reports_stale_mirror_and_other_edition_reader_and_origin(self):
        self.release['body'] += 'stale'
        second = dict(self.release, tag_name='works/sample/v2.0.0', name='Sample — v2.0.0')
        manifest = dict(self.manifest, tag=second['tag_name'], canonical_artifact='sample-v2.0.0.html')
        second['body'] = manifest['initial_release_body'] = render_initial_body(manifest)
        second['assets'] = [dict(self.release['assets'][0], name=manifest['canonical_artifact']), *self.release['assets'][1:]]
        url = 'https://assets.press.nash.spence.0819870.xyz/sha256/' + 'a' * 64 + '.png'
        manifest['assets'] = [{'source_kind': 'https', 'source_url': url}]
        self.reader_check.side_effect = [None, AssetError('reader offline')]
        with (patch('audit_assets.release_records', return_value=[self.release, second]),
              patch('audit_assets.git_sources', return_value=[self.record_path, self.record_path.with_name('v2.0.0.json'),
                                                             self.record_path.with_name('v3.0.0.json')]),
              patch('audit_assets.read_registration', return_value={}),
              patch('audit_assets.verify_edition', side_effect=[self.manifest, manifest]),
              patch('audit_assets.fetch_asset', side_effect=AssetError('asset missing')) as fetch,
              self.assertRaises(AssetError) as raised):
            audit(self.root, 'owner/press')
        message = str(raised.exception)
        for expected in ('release notes differ', 'works/sample/v2.0.0 reader: reader offline',
                         'Unresolved publication intent', 'asset missing'):
            self.assertIn(expected, message)
        fetch.assert_called_once()
        self.work_check.assert_called_once()
        with self.assertRaisesRegex(AssetError, 'release notes differ'):
            verify_public_release('owner/press', self.release, root=self.root)

    def test_missing_primary_still_checks_reader_only_with_independent_commitments(self):
        with (patch('audit_assets.release_records', return_value=[]),
              patch('audit_assets.saved_manifest', return_value=self.manifest),
              patch('audit_assets.check_authorization') as auth):
            with self.assertRaisesRegex(AssetError, 'missing registered edition'):
                audit(self.root, 'owner/press')
            self.reader_check.assert_called_once()
            self.work_check.assert_called_once()
            self.assertTrue(auth.call_args.kwargs['require_claim'])
            self.reader_check.reset_mock()
            auth.side_effect = AssetError('untrusted local manifest')
            with self.assertRaisesRegex(AssetError, 'untrusted local manifest'):
                audit(self.root, 'owner/press')
            self.reader_check.assert_not_called()

    def test_unconfirmed_stop_still_needs_resolution_and_abandoned_release_is_an_error(self):
        receipt = {'issuing': True, 'stop_reason': 'Unable to finish'}
        with patch('audit_assets.read_registration', return_value=receipt):
            with patch('audit_assets.release_records', return_value=[]):
                with self.assertRaisesRegex(AssetError, 'Unresolved publication intent'):
                    audit(self.root, 'owner/press')
                receipt['abandoned_run_id'] = '345'
                audit(self.root, 'owner/press')
            with (patch('audit_assets.check_issuance', side_effect=AssetError('receipt missing')),
                  self.assertRaisesRegex(AssetError, 'public release exists for a confirmed abandoned claim')):
                audit(self.root, 'owner/press')

    def test_fresh_unapproved_draft_is_ignored(self):
        self.release["draft"] = True
        with patch("audit_assets.git_sources", return_value=[]), patch("audit_assets.check_issuance") as check:
            audit(self.root, "owner/press")
        check.assert_not_called()


class RecoveryCheckpointTests(unittest.TestCase):
    def test_committed_checkpoint_detects_stale_or_missing_editorial_state_before_readback(self):
        import json
        import subprocess
        import tempfile

        from audit_assets import committed_checkpoint

        tag = 'works/sample/v1.0.0'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def git(*args):
                subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.org', *args],
                               cwd=root, check=True, capture_output=True)

            git('init')
            receipt = root / 'registrations/editions/sample/v1.0.0.json'
            receipt.parent.mkdir(parents=True)
            def write_receipt(**extra):
                doc = {'work': 'sample', 'version': 'v1.0.0', 'published_at': '2026-11-04T13:00:00Z', **extra}
                receipt.write_text(json.dumps(doc, indent=2, sort_keys=True) + '\n')

            write_receipt()
            page = root / 'records/editions/sample/v1.0.0/index.html'
            page.parent.mkdir(parents=True)
            page.write_bytes(b'approved edition page')
            git('add', '.')
            git('commit', '-m', 'Receipt')
            original = committed_checkpoint(root, tag)
            notice = root / 'notices/sample/v1.0.0/0001.md'
            notice.parent.mkdir(parents=True)
            notice.write_text('2026-11-05 Withdrawal: Result withdrawn.\n')
            write_receipt(identifiers={'oclc': '42'})
            self.assertEqual(committed_checkpoint(root, tag), original)
            git('add', '.')
            git('commit', '-m', 'Editorial update')
            current = committed_checkpoint(root, tag)
            self.assertNotEqual(original, current)

            def verify(expected):
                argv = ['audit_assets.py', '--reader', str(root), '--registration', str(receipt),
                        '--archive-root', str(root), '--editorial-sha256', expected]
                with patch.object(sys, 'argv', argv), patch('audit_assets.verify_edition', return_value={'tag': tag}), \
                     patch('audit_assets.check_authorization'), patch('audit_assets.verify_reader_routes') as routes:
                    status = main()
                    self.assertEqual(routes.called, status == 0)
                    return status

            self.assertEqual(verify(current), 0)
            self.assertEqual(verify(original), 1)
            page.write_bytes(b'edition page after a style change')
            self.assertEqual(committed_checkpoint(root, tag), current)
            self.assertEqual(verify(current), 1)
            git('add', '.')
            git('commit', '-m', 'Update edition page style')
            restyled = committed_checkpoint(root, tag)
            self.assertNotEqual(restyled, current)
            self.assertEqual(verify(restyled), 0)
            page.unlink()
            self.assertEqual(verify(restyled), 1)
            page.write_bytes(b'approved edition page')
            write_receipt()
            self.assertEqual(verify(current), 1)
            write_receipt(identifiers={'oclc': '42'})
            notice.unlink()
            notice.parent.rmdir()
            self.assertEqual(verify(current), 1)
            write_receipt()
            self.assertEqual(verify(original), 0)  # A trusted zero-notice checkpoint is explicit.
            receipt.unlink()
            self.assertEqual(verify(original), 1)
