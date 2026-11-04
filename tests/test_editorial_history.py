"""Exercise the history check on real Git baselines and tracked checkouts."""
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
from identifier_schema import edition_release_url


class EditorialHistoryTests(unittest.TestCase):
    def setUp(self):
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
            'release_url': edition_release_url('works/essay/v1.0.0'), 'source_commit': 'a' * 40,
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

    def issue(self):
        self.record['published_at'] = self.timestamp
        self.save()

    def check(self, base=None, **options):
        self.git('add', '-A')
        check_editorial_history(self.root, base, **options)

    def test_pending_authorization_can_be_corrected_and_withdrawn(self):
        base = self.baseline()
        self.record['rights'] = 'CC0-1.0'
        self.save()
        self.check(base)
        self.registration.unlink()
        self.check(base)

    def test_receipt_can_be_recorded_then_identifiers_can_change(self):
        base = self.baseline()
        self.issue()
        self.check(base)
        issued = self.baseline()
        for doi in ('10.1234/example', '10.1234/replacement'):
            self.record['identifiers'] = {'version_doi': doi}
            self.save()
            self.check(issued)
            issued = self.baseline()

    def test_first_receipt_cannot_replace_the_approved_package(self):
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
        self.check(base)

    def test_online_first_receipt_requires_exact_public_release(self):
        base = self.baseline()
        self.issue()
        release = {'tag_name': 'works/essay/v1.0.0', 'published_at': self.timestamp,
                   'draft': False, 'prerelease': False}
        with patch('check_editorial_history.api', return_value=release) as api:
            self.check(base, repo='owner/rehearsal')
            api.assert_called_once_with('owner/rehearsal', 'releases/tags/works%2Fessay%2Fv1.0.0')
            for change in ({'tag_name': 'works/essay/v1.0.1'}, {'draft': True}, {'prerelease': True},
                           {'published_at': '2026-04-10T12:30:01Z'}):
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
