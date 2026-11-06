"""Exercise the history check on real Git baselines and tracked checkouts."""
from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from asset_policy import AssetError
from check_editorial_history import check_editorial_history
from editorial_notices import render_initial_body
from issuance import authorization


class EditorialHistoryTests(unittest.TestCase):
    def setUp(self):
        self.clock = self.enterContext(patch('check_editorial_history.datetime')).now
        self.clock.return_value = datetime(2026, 11, 30, tzinfo=UTC)
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.git('init', '-q')
        self.git('config', 'user.name', 'Press test')
        self.git('config', 'user.email', 'press@example.invalid')
        self.work = self.root / 'works/essay/manuscript.md'
        self.work.parent.mkdir(parents=True)
        self.work.write_text('Test work\n')
        self.registration = self.root / 'registrations/editions/essay/v1.0.0.json'
        self.registration.parent.mkdir(parents=True)
        self.directory = self.root / 'notices/essay/v1.0.0'
        self.directory.mkdir(parents=True)
        self.record = {
            'schema': 'press.edition-registration.v1', 'work': 'essay', 'version': 'v1.0.0',
            'canonical_artifact': 'essay-v1.0.0.html', 'rights': 'all-rights-reserved',
            'record_url': 'https://press.example.org/works/essay/v1.0.0', 'source_commit': 'a' * 40,
            'artifact_sha256': 'b' * 64, 'provenance_sha256': 'c' * 64,
            'initial_notes_sha256': 'd' * 64,
        }
        self.timestamp = '2026-04-10T12:30:00Z'
        self.first = '2026-04-11 Correction: The source spells the name differently.\n'
        self.save()

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, text=True, capture_output=True,
                              check=True).stdout.strip()

    def save(self):
        self.registration.write_text(json.dumps(self.record, sort_keys=True, indent=2) + '\n')

    def baseline(self):
        self.git('add', '-A')
        self.git('commit', '-qm', 'Baseline')
        return self.git('rev-parse', 'HEAD')

    def claim(self):
        self.record['issuing'] = True
        self.save()

    def issue(self):
        self.claim()
        self.record['published_at'] = self.timestamp
        self.save()

    def check(self, base=None, **options):
        self.git('add', '-A')
        check_editorial_history(self.root, base, **options)

    def test_work_doi_changes_require_a_dated_append_only_transition(self):
        path = self.root / 'registrations/works/essay.json'
        path.parent.mkdir()
        old, new = '10.1234/old', '10.1234/new'
        record = {'schema': 'press.work-registration.v1', 'work': 'essay', 'identifiers': {'concept_doi': old}}

        def write():
            path.write_text(json.dumps(record, sort_keys=True, indent=2) + '\n')

        empty = self.baseline()
        write()
        base = self.baseline()
        record['identifiers']['concept_doi'] = new
        write()
        with self.assertRaisesRegex(AssetError, 'record one dated'):
            self.check(base)
        path.unlink()
        with self.assertRaisesRegex(AssetError, 'record one dated'):
            self.check(base)
        history = record['concept_doi_history'] = [{'date': '2026-11-06', 'from': old, 'to': new}]
        write()
        self.check(base)
        with self.assertRaisesRegex(AssetError, 'commit the initial'):
            self.check(empty)
        corrected = self.baseline()
        history[0]['date'] = '2026-11-01'
        write()
        with self.assertRaisesRegex(AssetError, 'append-only'):
            self.check(corrected)
        history[0]['date'] = '2026-11-06'
        record['identifiers'] = {}
        history.append({'date': '2026-11-07', 'from': new, 'to': None})
        write()
        self.check(corrected)
        with self.assertRaisesRegex(AssetError, 'record one dated'):
            self.check(base)
        retired = self.baseline()
        path.unlink()
        with self.assertRaisesRegex(AssetError, 'append-only'):
            self.check(retired)
        record['identifiers']['concept_doi'] = '10.1234/fresh'
        history.append({'date': '2026-11-08', 'from': None, 'to': '10.1234/fresh'})
        write()
        self.check(retired)

    def test_committed_authorization_is_immutable_and_cancelled_only_once(self):
        base = self.baseline()
        self.record['rights'] = 'CC0-1.0'
        self.save()
        with self.assertRaisesRegex(AssetError, 'immutable'):
            self.check(base)
        self.registration.unlink()
        with self.assertRaisesRegex(AssetError, 'cannot be removed'):
            self.check(base)
        self.record['rights'] = 'all-rights-reserved'
        self.record['cancellation_reason'] = 'Rights were declined.'
        self.save()
        with self.assertRaisesRegex(AssetError, 'cancellation requires'):
            self.check(base)
        self.record['cancellation_run_id'] = '42'
        self.save()
        with patch('check_editorial_history.verify_confirmation') as verify:
            self.check(base, repo='owner/press')
            verify.assert_called_once()
            self.assertEqual(verify.call_args.args[-1], '42')
            self.assertNotIn('cancellation_reason', verify.call_args.args[-2])
        cancelled = self.baseline()
        del self.record['cancellation_reason']
        del self.record['cancellation_run_id']
        self.save()
        with self.assertRaisesRegex(AssetError, 'immutable'):
            self.check(cancelled)

    def test_protected_confirmation_detects_an_untagged_draft_on_an_unclaimed_reservation(self):
        from abandon_edition import confirm_absence
        tag = 'works/essay/v1.0.0'
        with (patch('abandon_edition.tag_commit', return_value=None),
              patch('abandon_edition.release_records', return_value=[{'tag_name': tag, 'draft': True}]) as releases):
            with self.assertRaisesRegex(AssetError, 'absence'):
                confirm_absence(self.root, 'owner/press', tag)
            releases.return_value = []
            confirm_absence(self.root, 'owner/press', tag)
            self.claim()
            with self.assertRaisesRegex(AssetError, 'stopped unpublished claim'):
                confirm_absence(self.root, 'owner/press', tag)

    def test_claim_and_cancellation_cannot_both_win(self):
        reserved = self.baseline()
        self.issue()
        with self.assertRaisesRegex(AssetError, 'merge the issuing claim'):
            self.check(reserved)
        del self.record['published_at']
        self.save()
        self.check(reserved)
        claimed = self.baseline()
        del self.record['issuing']
        self.save()
        with self.assertRaisesRegex(AssetError, 'immutable'):
            self.check(claimed)
        self.record['cancellation_reason'] = 'Permission withdrawn.'
        self.record['cancellation_run_id'] = '42'
        self.save()
        self.check(reserved)
        with self.assertRaisesRegex(AssetError, 'cannot be cancelled'):
            self.check(claimed)
        cancelled = self.baseline()
        del self.record['cancellation_reason']
        del self.record['cancellation_run_id']
        self.claim()
        with self.assertRaisesRegex(AssetError, 'cannot be claimed'):
            self.check(cancelled)

    def test_stop_is_permanent_and_can_reconcile_a_publication_that_won_the_race(self):
        reserved = self.baseline()
        self.claim()
        self.record['stop_reason'] = 'Illustration clearance withdrawn.'
        self.save()
        with self.assertRaisesRegex(AssetError, 'stop an existing unpublished claim'):
            self.check(reserved)
        del self.record['stop_reason']
        self.save()
        claimed = self.baseline()
        self.record['stop_reason'] = 'Illustration clearance withdrawn.'
        self.save()
        self.check(claimed)
        stopped = self.baseline()
        for field in ('stop_reason', 'issuing'):
            with self.subTest(field=field):
                value = self.record.pop(field)
                self.save()
                with self.assertRaises(AssetError):
                    self.check(stopped)
                self.record[field] = value
        self.record['published_at'] = self.timestamp
        self.save()
        self.check(stopped)
        issued = self.baseline()
        self.record['abandoned_run_id'] = '42'
        self.save()
        with self.assertRaisesRegex(AssetError, 'abandonment requires a stopped unpublished claim'):
            self.check(issued)

    def test_terminal_decision_requires_exact_stopped_claim_and_serialized_run(self):
        from abandon_edition import confirm_absence
        self.claim()
        claimed = self.baseline()
        self.record['stop_reason'] = 'Rights were withdrawn.'
        self.record['abandoned_run_id'] = '42'
        self.save()
        with self.assertRaisesRegex(AssetError, 'merge the stop'):
            self.check(claimed)
        del self.record['abandoned_run_id']
        self.save()
        stopped = self.baseline()
        tag, repo = 'works/essay/v1.0.0', 'owner/press'
        run = {'head_sha': stopped, 'head_branch': 'main', 'head_repository': {'full_name': repo},
               'path': '.github/workflows/confirm-abandonment.yml', 'event': 'workflow_dispatch',
               'status': 'completed', 'conclusion': 'success', 'run_attempt': 2}
        job = {'name': 'Confirm ' + tag, 'conclusion': 'success',
               'steps': [{'name': 'Verify release and tag absence', 'conclusion': 'success'}]}
        def get_run(repository, path):
            self.assertEqual(repository, repo)
            if path == 'actions/runs/42':
                return run
            self.assertEqual(path, 'actions/runs/42/attempts/2/jobs?per_page=100')
            return {'total_count': 1, 'jobs': [job]}

        with (patch('abandon_edition.api', side_effect=get_run),
              patch('abandon_edition.tag_commit', return_value=None) as ref,
              patch('abandon_edition.release_records', return_value=[]) as releases):
            confirm_absence(self.root, repo, tag)
            for draft in (True, False):
                releases.return_value = [{'tag_name': tag, 'draft': draft}]
                with self.assertRaisesRegex(AssetError, 'absence'):
                    confirm_absence(self.root, repo, tag)
            releases.return_value = []
            self.record['abandoned_run_id'] = '42'
            self.save()
            self.check(stopped, repo=repo)
            for obj, changes in ((run, {'head_sha': claimed}), (run, {'conclusion': 'failure'}),
                                 (run, {'head_branch': 'feature'}), (job, {'name': 'Confirm works/other/v1.0.0'}),
                                 (job, {'steps': []})):
                with self.subTest(changes=changes), patch.dict(obj, changes), self.assertRaises(AssetError):
                    self.check(stopped, repo=repo)
            ref.return_value = 'a' * 40
            with self.assertRaisesRegex(AssetError, 'absence'):
                self.check(stopped, repo=repo)
            ref.return_value = None
            abandoned = self.baseline()
            del self.record['abandoned_run_id']
            self.save()
            with self.assertRaisesRegex(AssetError, 'immutable'):
                self.check(abandoned)

    def test_confirmation_uses_the_promotion_lock_and_reviewer(self):
        import yaml
        workflows = Path(__file__).resolve().parents[1] / '.github/workflows'
        def job(filename, name):
            return yaml.safe_load((workflows / filename).read_text())['jobs'][name]
        release = job('prepare-edition.yml', 'release')
        confirmation = job('confirm-abandonment.yml', 'confirm')
        self.assertEqual(confirmation['concurrency'], release['concurrency'])
        self.assertEqual(confirmation['environment'], release['environment'])
        self.assertEqual(confirmation['permissions']['contents'], 'write')
        self.assertEqual(confirmation['name'], 'Confirm ${{ inputs.edition_tag }}')
        self.assertTrue(any(step.get('name') == 'Verify release and tag absence' for step in confirmation['steps']))

    def test_late_identifier_and_documented_correction_or_retirement(self):
        self.claim()
        base = self.baseline()
        self.issue()
        self.check(base)
        issued = self.baseline()
        self.record['identifiers'] = {'version_doi': '10.1234/example'}
        self.save()
        self.check(issued)
        assigned = self.baseline()
        self.record['identifiers'] = {'version_doi': '10.1234/replacement'}
        self.save()
        with self.assertRaisesRegex(AssetError, 'record one dated version DOI'):
            self.check(assigned)
        history = self.record['version_doi_history'] = [
            {'date': '2026-04-11', 'from': '10.1234/example', 'to': '10.1234/replacement'}]
        self.save()
        with self.assertRaisesRegex(AssetError, 'new dated Correction'):
            self.check(assigned)
        text = 'Correction: Identifier version_doi changed from 10.1234/example to 10.1234/replacement.\n'
        (self.directory / '0001.md').write_text('2026-04-12 ' + text)
        with self.assertRaisesRegex(AssetError, '2026-04-11 Correction'):
            self.check(assigned)
        (self.directory / '0001.md').write_text('2026-04-11 ' + text)
        self.check(assigned)
        corrected = self.baseline()
        history[0]['date'] = '2026-04-10'
        self.save()
        with self.assertRaisesRegex(AssetError, 'append-only'):
            self.check(corrected)
        history[0]['date'] = '2026-04-11'
        del self.record['identifiers']
        history.append({'date': '2026-04-12', 'from': '10.1234/replacement', 'to': None})
        self.save()
        with self.assertRaisesRegex(AssetError, 'new dated Correction'):
            self.check(corrected)
        (self.directory / '0002.md').write_text('2026-04-12 Correction: Identifier version_doi retired: 10.1234/replacement.\n')
        self.check(corrected)
        retired = self.baseline()
        self.record['identifiers'] = {'version_doi': '10.1234/later'}
        history.append({'date': '2026-12-01', 'from': None, 'to': '10.1234/later'})
        self.save()
        with self.assertRaisesRegex(AssetError, 'in the future'):
            self.check(retired)
        history[-1]['date'] = '2026-04-13'
        self.save()
        (self.directory / '0003.md').write_text('2026-04-13 Correction: Identifier version_doi assigned: 10.1234/later.\n')
        self.check(retired)

    def test_transcription_correction_reserves_the_doi_for_its_receipted_destination(self):
        self.issue()
        doi = '10.1234/transcription'
        target = 'works/essay/v2.0.0'
        other = self.registration.with_name('v2.0.0.json')
        destination = {**self.record, 'version': 'v2.0.0', 'canonical_artifact': 'essay-v2.0.0.html',
                       'record_url': 'https://press.example.org/' + target, 'published_at': '2026-04-11T12:00:00Z'}

        def save_destination():
            other.write_text(json.dumps(destination, sort_keys=True, indent=2) + '\n')

        save_destination()
        self.record['identifiers'] = {'version_doi': doi}
        self.save()
        base = self.baseline()
        del self.record['identifiers']
        change = {'date': '2026-04-10', 'from': doi, 'to': None, 'correct_edition': target}
        self.record['version_doi_history'] = [change]
        self.save()
        with self.assertRaisesRegex(AssetError, 'must remain associated with'):
            self.check(base)
        destination['identifiers'] = {'version_doi': doi}
        save_destination()
        with self.assertRaisesRegex(AssetError, 'published by the correction date'):
            self.check(base)
        change['date'] = '2026-04-11'
        self.save()
        path = self.directory / '0001.md'
        path.write_text(f'2026-04-11 Correction: Identifier version_doi retired: {doi}.\n')
        with self.assertRaisesRegex(AssetError, target):
            self.check(base)
        path.write_text(f'2026-04-11 Correction: Identifier version_doi recorded in error: {doi} belongs to {target}.\n')
        self.check(base)
        corrected = self.baseline()
        del destination['identifiers']
        destination['version_doi_history'] = [{'date': '2026-04-12', 'from': doi, 'to': None}]
        save_destination()
        notices = self.directory.with_name('v2.0.0')
        notices.mkdir()
        (notices / '0001.md').write_text(f'2026-04-12 Correction: Identifier version_doi retired: {doi}.\n')
        self.check(corrected)

    def test_first_receipt_cannot_replace_the_approved_package(self):
        self.claim()
        base = self.baseline()
        self.record['provenance_sha256'] = 'e' * 64
        self.issue()
        with self.assertRaisesRegex(AssetError, 'immutable'):
            self.check(base)

    def test_new_registration_must_be_pending(self):
        self.registration.unlink()
        base = self.baseline()
        self.issue()
        with self.assertRaisesRegex(AssetError, 'pending authorization'):
            self.check(base)
        del self.record['published_at']
        self.save()
        with self.assertRaisesRegex(AssetError, 'pending authorization'):
            self.check(base)
        del self.record['issuing']
        self.save()
        self.check(base)

    def test_online_reservation_requires_exact_authorization_from_staged_package(self):
        self.registration.unlink()
        origin = self.root / 'edition-origin.txt'
        origin.write_text('https://press.example.org\n')
        workflow = self.root / '.github/workflows/validate-source.yml'
        workflow.parent.mkdir(parents=True)
        workflow.write_text('original source workflow\n')
        base = self.baseline()
        self.record['source_commit'] = base
        self.save()
        with self.assertRaisesRegex(AssetError, 'staged_run_id'):
            self.check(base, repo='owner/rehearsal')

        manifest = {'tag': 'works/essay/v1.0.0', 'title': 'Essay', 'html_sha256': 'a' * 64,
                    **{k: self.record[k] for k in ('source_commit', 'canonical_artifact', 'rights', 'record_url')}}
        manifest['initial_release_body'] = render_initial_body(manifest)
        raw = json.dumps(manifest).encode()
        package = self.root / 'proof'
        package.mkdir()
        (package / 'provenance.json').write_bytes(raw)
        self.record = authorization(package, manifest, staged_run_id='42')

        def download(repo, run, digest, directory):
            self.assertEqual((repo, run, digest), ('owner/rehearsal', '42', self.record['provenance_sha256']))
            directory.mkdir()
            (directory / 'provenance.json').write_bytes(raw)

        with (patch('check_editorial_history.download_reviewed', side_effect=download) as fetch,
              patch('check_editorial_history.verify_edition', return_value=manifest) as verify):
            self.save()
            self.check(base, repo='owner/rehearsal')
            verify.assert_called_once_with(fetch.call_args.args[-1], tag=manifest['tag'], commit=manifest['source_commit'])
            manifest['work_identifiers'] = {'concept_doi': '10.1234/manuscript'}
            with self.assertRaisesRegex(AssetError, 'register the staged concept DOI'):
                self.check(base, repo='owner/rehearsal')
            del manifest['work_identifiers']
            workflow.write_text('changed workflow\n')
            with self.assertRaisesRegex(AssetError, r'workflow files differ.*\.github/workflows/validate-source\.yml'):
                self.check(base, repo='owner/rehearsal')
            workflow.write_text('original source workflow\n')
            origin.write_text('https://changed.example.org\n')
            with self.assertRaisesRegex(AssetError, 'different permanent record origin'):
                self.check(base, repo='owner/rehearsal')
            origin.write_text('https://press.example.org\n')
            self.record['rights'] = 'CC0-1.0'
            self.save()
            with self.assertRaisesRegex(AssetError, 'differs from the verified staged proof'):
                self.check(base, repo='owner/rehearsal')
            fetch.side_effect = AssetError('staging artifact expired')
            with self.assertRaisesRegex(AssetError, 'artifact expired'):
                self.check(base, repo='owner/rehearsal')
            fetch.reset_mock()
            self.record['rights'] = manifest['rights']
            self.save()
            claimed_base = self.baseline()
            self.claim()
            self.check(claimed_base, repo='owner/rehearsal')
            fetch.assert_not_called()
            workflow.write_text('changed since reservation\n')
            with self.assertRaisesRegex(AssetError, r'workflow files differ.*\.github/workflows/validate-source\.yml'):
                self.check(claimed_base, repo='owner/rehearsal')

    def test_first_receipt_precedes_later_identifiers_and_notices(self):
        self.claim()
        base = self.baseline()
        self.issue()
        self.record['identifiers'] = {'oclc': '42'}
        self.save()
        with self.assertRaisesRegex(AssetError, 'before adding notices or identifiers'):
            self.check(base)
        del self.record['identifiers']
        self.save()
        (self.directory / '0001.md').write_text(self.first)
        with self.assertRaisesRegex(AssetError, 'before adding notices or identifiers'):
            self.check(base)

    def test_online_first_receipt_requires_exact_public_release(self):
        self.claim()
        base = self.baseline()
        self.issue()
        release = {'tag_name': 'works/essay/v1.0.0', 'published_at': self.timestamp,
                   'draft': False, 'prerelease': False}
        with (patch('check_editorial_history.api', return_value=release) as api,
              patch('check_editorial_history.verify_public_release') as verify):
            self.check(base, repo='owner/rehearsal')
            api.assert_called_once_with('owner/rehearsal', 'releases/tags/works%2Fessay%2Fv1.0.0')
            verify.assert_called_once_with('owner/rehearsal', release, root=self.root, require_receipt=True)
            verify.side_effect = AssetError('release attachments differ from approved package')
            with self.assertRaisesRegex(AssetError, 'attachments differ'):
                self.check(base, repo='owner/rehearsal')
            for change in ({'tag_name': 'works/essay/v1.0.1'}, {'published_at': '2026-04-10T12:30:01Z'}):
                with self.subTest(change=change):
                    api.return_value = {**release, **change}
                    with self.assertRaisesRegex(AssetError, 'official public release'):
                        self.check(base, repo='owner/rehearsal')
            api.reset_mock()
            self.check(base)
            self.check(self.baseline(), repo='owner/rehearsal')
            api.assert_not_called()

    def test_issued_fields_and_timestamp_are_frozen(self):
        self.issue()
        base = self.baseline()
        for field, replacement in (('rights', 'CC0-1.0'), ('source_commit', 'e' * 40),
                                   ('artifact_sha256', 'e' * 64), ('provenance_sha256', 'e' * 64),
                                   ('initial_notes_sha256', 'e' * 64),
                                   ('published_at', '2026-04-09T12:30:00Z')):
            with self.subTest(field=field):
                previous = self.record[field]
                self.record[field] = replacement
                self.save()
                with self.assertRaisesRegex(AssetError, 'immutable'):
                    self.check(base)
                self.record[field] = previous
        del self.record['published_at']
        self.save()
        with self.assertRaisesRegex(AssetError, 'immutable'):
            self.check(base)
        self.registration.rename(self.root / 'old-record.json')
        with self.assertRaisesRegex(AssetError, 'cannot be removed'):
            self.check(base)

    def test_notices_require_a_matching_receipt_even_without_a_base(self):
        (self.directory / '0001.md').write_text(self.first)
        with self.assertRaisesRegex(AssetError, 'publication receipt'):
            self.check()
        self.registration.unlink()
        with self.assertRaisesRegex(AssetError, 'tracked edition registration'):
            self.check()

    def test_append_new_notice_but_preserve_existing_bytes_and_path(self):
        self.issue()
        first = self.directory / '0001.md'
        first.write_text(self.first)
        base = self.baseline()
        (self.directory / '0002.md').write_text('2026-04-12 Supersession: See the new edition.\n')
        self.check(base)
        first.write_text(self.first.replace('name', 'word'))
        with self.assertRaisesRegex(AssetError, 'append-only'):
            self.check(base)
        (self.directory / '0002.md').unlink()
        first.rename(self.root / 'moved-notice.md')
        with self.assertRaisesRegex(AssetError, 'append-only'):
            self.check(base)

    def test_numbering_dates_and_notice_directory_shape(self):
        self.issue()
        first = self.directory / '0001.md'
        first.write_text('2026-04-09 Correction: Too early.\n')
        with self.assertRaisesRegex(AssetError, 'predates'):
            self.check()
        first.write_text(self.first)
        (self.directory / '0002.md').write_text('2026-04-10 Correction: Out of order.\n')
        with self.assertRaisesRegex(AssetError, 'predates'):
            self.check()
        (self.directory / '0002.md').rename(self.directory / '0003.md')
        with self.assertRaisesRegex(AssetError, 'unexpected notice file'):
            self.check()
        (self.directory / '0003.md').unlink()
        first.rename(self.root / 'notices/out-of-place.md')
        with self.assertRaisesRegex(AssetError, 'expected notices/'):
            self.check()

    def test_date_cutoff_applies_only_to_new_notices(self):
        self.issue()
        base = self.baseline()
        first = self.directory / '0001.md'
        first.write_text('2026-12-01 Withdrawal: Review in progress.\n')
        with self.assertRaisesRegex(AssetError, 'in the future'):
            self.check(base)
        self.clock.assert_called_with(UTC)
        first.write_text('2026-11-30 Withdrawal: Review in progress.\n')
        self.check(base)
        base = self.baseline()
        self.clock.return_value = datetime(2026, 11, 29, tzinfo=UTC)
        self.check(base)

    def test_symlinks_and_untracked_notice_gaps_are_rejected(self):
        self.issue()
        first = self.directory / '0001.md'
        first.symlink_to(self.work)
        with self.assertRaisesRegex(AssetError, 'without symlinks'):
            self.check()
        first.unlink()
        first.write_text(self.first)
        (self.directory / '0002.md').write_text(self.first)
        self.git('add', '-A')
        self.git('rm', '--cached', 'notices/essay/v1.0.0/0001.md')
        with self.assertRaisesRegex(AssetError, 'must be tracked'):
            check_editorial_history(self.root)

    def test_base_requires_existing_full_commit(self):
        self.baseline()
        for base in ('HEAD', '0' * 40):
            with self.subTest(base=base), self.assertRaises(AssetError):
                self.check(base)
