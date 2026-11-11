"""Operator status observes committed authority without advancing publication."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from asset_policy import AssetError
from edition_status import inspect_edition
from editorial_notices import render_initial_body
from issuance import authorization
from public_record import record_html


class EditionStatusTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = self.base / 'repo'; self.root.mkdir()
        self.git('init', '-q', '-b', 'main')
        work = self.root / 'works/example/manuscript.md'
        work.parent.mkdir(parents=True); work.write_text('# Example\n')
        self.commit()
        self.tag = 'works/example/v1.0.0'
        self.manifest = {
            'title': 'Example', 'author': 'Nash Spence', 'language': 'en', 'tag': self.tag,
            'source_commit': self.git('rev-parse', 'HEAD'), 'html_sha256': 'a' * 64,
            'html_byte_count': 123, 'canonical_artifact': 'example-v1.0.0.html',
            'record_url': 'https://press.example.org/' + self.tag, 'rights': 'all-rights-reserved',
        }
        self.manifest['initial_release_body'] = render_initial_body(self.manifest)
        self.package = self.base / 'package'; self.package.mkdir()
        self.provenance = (json.dumps(self.manifest, sort_keys=True, indent=2) + '\n').encode()
        (self.package / 'provenance.json').write_bytes(self.provenance)
        self.record = authorization(self.package, self.manifest, staged_run_id='42')
        self.original = copy.deepcopy(self.record)
        self.path = self.root / 'registrations/editions/example/v1.0.0.json'
        self.page = self.root / 'records/editions/example/v1.0.0/index.html'

    def git(self, *args):
        return subprocess.check_output(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.org',
                                        *args], cwd=self.root, text=True).strip()

    def commit(self):
        self.git('add', '-A'); self.git('commit', '-qm', 'Test committed edition state')

    def save(self, *, commit=True):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.record, sort_keys=True, indent=2) + '\n')
        if self.record.get('issuing') is True:
            self.page.parent.mkdir(parents=True, exist_ok=True)
            (self.page.parent / 'provenance.json').write_bytes(self.provenance)
        if 'published_at' in self.record:
            self.page.write_bytes(record_html(self.manifest, published_at=self.record['published_at'],
                                              notices=[], identifiers=None))
        if commit:
            self.commit()

    def draft(self, *, complete):
        names = ['example-v1.0.0.html', 'provenance.json', 'SHA256SUMS'] if complete else ['provenance.json']
        return {'tag_name': self.tag, 'draft': True, 'prerelease': False, 'name': 'Example — v1.0.0',
                'body': self.manifest['initial_release_body'], 'target_commitish': self.record['source_commit'],
                'assets': [{'id': i, 'name': name, 'state': 'uploaded'} for i, name in enumerate(names)]}

    def test_unreserved_is_offline_and_does_not_change_git_state(self):
        before = self.git('rev-parse', 'HEAD')
        with patch('edition_status.release_records', side_effect=AssertionError('network')):
            report = inspect_edition(self.root, self.tag)
        self.assertEqual(report['state'], 'unreserved')
        self.assertEqual(report['remote']['state'], 'not-checked')
        self.assertEqual(report['preservation']['independent_custody'], 'not-observed')
        self.assertEqual(report['checkpoint_commit'], before)
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)
        self.assertFalse(self.git('status', '--porcelain'))

    def test_valid_committed_lifecycle_states_have_specific_next_actions(self):
        states = [
            ({}, 'reserved'), ({'issuing': True}, 'claimed'),
            ({'issuing': True, 'stop_reason': 'Stop this claim.'}, 'stopped'),
            ({'issuing': True, 'stop_reason': 'Stop this claim.', 'abandoned_run_id': '43'}, 'abandoned'),
            ({'cancellation_reason': 'Cancel this reservation.', 'cancellation_run_id': '43'}, 'cancelled'),
            ({'issuing': True, 'published_at': '2026-11-11T12:00:00Z'}, 'receipted'),
        ]
        for fields, expected in states:
            with self.subTest(state=expected):
                self.record = {**self.original, **fields}; self.save()
                report = inspect_edition(self.root, self.tag)
                self.assertEqual(report['state'], expected)
                self.assertEqual(report['proof']['staged_run_id'], '42')
                self.assertEqual(report['proof']['provenance_sha256'], self.original['provenance_sha256'])
                self.assertTrue(report['next_step'])
                self.assertEqual(report['preservation']['independent_custody'], 'not-observed')
        self.assertRegex(report['preservation']['editorial_sha256'], '^[a-f0-9]{64}$')
        self.assertIn('compatibility test data', report['preservation']['next_step'])

    def test_dirty_receipt_is_ignored_until_committed(self):
        self.record['issuing'] = True; self.save()
        self.record['published_at'] = '2026-11-11T12:00:00Z'; self.save(commit=False)
        report = inspect_edition(self.root, self.tag)
        self.assertEqual(report['state'], 'claimed')
        self.assertTrue(report['checkout_has_changes'])
        self.assertNotIn('published_at', report)
        self.assertNotIn('editorial_sha256', report['preservation'])

    def test_invalid_record_or_changed_claimed_provenance_never_looks_ready(self):
        self.record['issuing'] = 'true'; self.save()
        with self.assertRaisesRegex(AssetError, 'issuing must be true'):
            inspect_edition(self.root, self.tag)
        self.record['issuing'] = True; self.save()
        (self.page.parent / 'provenance.json').write_bytes(self.provenance + b' '); self.commit()
        with self.assertRaisesRegex(AssetError, 'provenance differs'):
            inspect_edition(self.root, self.tag)

    def test_receipt_with_stale_record_cannot_produce_a_current_checkpoint(self):
        self.record.update(issuing=True, published_at='2026-11-11T12:00:00Z'); self.save()
        self.page.write_bytes(self.page.read_bytes() + b'changed'); self.commit()
        with self.assertRaisesRegex(AssetError, 'stale public record'):
            inspect_edition(self.root, self.tag)

    def test_remote_unavailable_is_never_reported_as_absent(self):
        self.save()
        with (patch('edition_status.release_records', side_effect=AssetError('GitHub unavailable')),
              patch('edition_status.tag_commit') as tag):
            with self.assertRaisesRegex(AssetError, 'unavailable'):
                inspect_edition(self.root, self.tag, repo='owner/press')
            tag.assert_not_called()

    def test_empty_visible_inventory_does_not_establish_hidden_draft_absence(self):
        self.record['issuing'] = True; self.save()
        # A successful read-only listing can omit an untagged unpublished draft.
        with (patch('edition_status.release_records', return_value=[]),
              patch('edition_status.tag_commit', return_value=None)):
            report = inspect_edition(self.root, self.tag, repo='owner/press')
        self.assertEqual(report['state'], 'claimed')
        self.assertEqual(report['remote']['state'], 'no-visible-release-or-tag')
        self.assertEqual(report['remote']['draft_absence'], 'not-established')
        self.assertIn('not a publication authorization', report['scope'])

    def test_draft_inventory_distinguishes_partial_complete_and_stopped(self):
        self.record['issuing'] = True; self.save()
        for complete, expected in [(False, 'draft-incomplete'), (True, 'draft')]:
            with (self.subTest(complete=complete),
                  patch('edition_status.release_records', return_value=[self.draft(complete=complete)]),
                  patch('edition_status.tag_commit', return_value=None)):
                report = inspect_edition(self.root, self.tag, repo='owner/press')
                self.assertEqual(report['state'], expected)
                self.assertEqual(report['remote']['inventory'], 'metadata-only')
                self.assertEqual(bool(report['remote']['missing_attachments']), not complete)
        self.record['stop_reason'] = 'Stop this claim.'; self.save()
        with (patch('edition_status.release_records', return_value=[self.draft(complete=True)]),
              patch('edition_status.tag_commit', return_value=None)):
            report = inspect_edition(self.root, self.tag, repo='owner/press')
            self.assertEqual(report['state'], 'stopped')
            self.assertIn('resolve the unpublished draft', report['next_step'])
            self.assertIn('then run protected absence confirmation', report['next_step'])

    def test_publication_that_won_stop_race_requires_verified_receipt(self):
        self.record.update(issuing=True, stop_reason='Stop this claim.'); self.save()
        release = {**self.draft(complete=True), 'draft': False, 'published_at': '2026-11-11T12:00:00Z'}
        with (patch('edition_status.release_records', return_value=[release]),
              patch('edition_status.tag_commit', return_value=self.record['source_commit']),
              patch('edition_status.verify_public_release') as verify):
            report = inspect_edition(self.root, self.tag, repo='owner/press')
            self.assertEqual(report['state'], 'public-awaiting-receipt')
            self.assertEqual(report['remote']['state'], 'public-verified')
            verify.assert_called_once()
            self.assertFalse(verify.call_args.kwargs['require_receipt'])
        with (patch('edition_status.release_records', return_value=[release]),
              patch('edition_status.tag_commit', return_value=self.record['source_commit']),
              patch('edition_status.verify_public_release', side_effect=AssetError('wrong bytes'))):
            with self.assertRaisesRegex(AssetError, 'wrong bytes'):
                inspect_edition(self.root, self.tag, repo='owner/press')

    def test_retired_or_unclaimed_remote_state_is_a_conflict(self):
        self.record.update(cancellation_reason='Cancelled.', cancellation_run_id='43'); self.save()
        with (patch('edition_status.release_records', return_value=[self.draft(complete=True)]),
              patch('edition_status.tag_commit', return_value=None)):
            with self.assertRaisesRegex(AssetError, 'without an active issuance claim'):
                inspect_edition(self.root, self.tag, repo='owner/press')

    def test_duplicate_releases_and_wrong_tags_fail_closed(self):
        self.record['issuing'] = True; self.save()
        draft = self.draft(complete=True)
        with patch('edition_status.release_records', return_value=[draft, draft]):
            with self.assertRaisesRegex(AssetError, 'multiple releases'):
                inspect_edition(self.root, self.tag, repo='owner/press')
        with (patch('edition_status.release_records', return_value=[draft]),
              patch('edition_status.tag_commit', return_value='f' * 40)):
            with self.assertRaisesRegex(AssetError, 'differs from the reviewed source'):
                inspect_edition(self.root, self.tag, repo='owner/press')

    def test_missing_receipted_release_and_orphan_tag_require_reconciliation(self):
        self.record.update(issuing=True, published_at='2026-11-11T12:00:00Z'); self.save()
        with (patch('edition_status.release_records', return_value=[]),
              patch('edition_status.tag_commit', return_value=None)):
            with self.assertRaisesRegex(AssetError, 'missing its public release'):
                inspect_edition(self.root, self.tag, repo='owner/press')
        del self.record['published_at']; self.save()
        with (patch('edition_status.release_records', return_value=[]),
              patch('edition_status.tag_commit', return_value=self.record['source_commit'])):
            with self.assertRaisesRegex(AssetError, 'tag exists without a release'):
                inspect_edition(self.root, self.tag, repo='owner/press')


if __name__ == '__main__':
    unittest.main()
