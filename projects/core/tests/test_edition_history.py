"""Edition numbers bind complete work trees; normal Git history carries the proof.

These histories use real signatures and ordinary commits/merges. An edition's
identity remains its introducing commit, including when a later merge carries
several already published works. Edition zero is an unpublished draft.
"""
import re
import tempfile
import unittest
import zlib

from guavajam_core.publication_protocol import discover, verify_shape
from guavajam_core.publication_commit import verify as verify_publication
from guavajam_test_support.discovery_fixture import Draft, Publisher, command


class SSHPublisher(Publisher):
    """Exercise the same publishing operations with independently trusted SSH keys."""
    def __init__(self, directory):
        Draft.__init__(self, directory, branch='main', base_url='https://publisher.example')
        key = self.root / 'signing-key'
        command(self.repo, 'ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key), env=self.env)
        self.allowed_signers = self.root / 'allowed-signers'
        self.allowed_signers.write_text('publisher@example.org ' + key.with_suffix('.pub').read_text())
        self.git('config', 'gpg.format', 'ssh')
        self.git('config', 'user.signingkey', str(key))
        self.seal()
        self.revisions = []


class EditionHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.p = SSHPublisher(self.temp.name)

    def discover(self, **kwargs):
        return discover(self.p.repo, allowed_signers=self.p.allowed_signers, **kwargs)

    def manuscript(self, slug='field-notes'):
        return self.p.repo / 'works' / slug / 'manuscript.md'

    def edition(self, number, slug='field-notes'):
        manuscript = self.manuscript(slug)
        source, count = re.subn(r'^edition:.*$', f'edition: "{number}"',
                                manuscript.read_text(), count=1, flags=re.M)
        self.assertEqual(1, count)
        manuscript.write_text(source)

    def commit(self, message, *, signed=True):
        self.p.git('add', '-A')
        self.p.git('commit', *(['-S'] if signed else []), '-qm', message)
        return self.p.git('rev-parse', 'HEAD')

    def draft_work(self, slug='other-notes'):
        self.p.source(slug, 'Other Notes', 'An independent observation.')
        self.commit('Start another draft', signed=False)

    def start_merge(self, branch):
        self.p.git('merge', '--no-ff', '--no-commit', branch)

    def test_merge_carries_original_signed_edition_without_reissuing_it(self):
        first = self.p.publish()
        self.p.git('checkout', '-qb', 'revised')
        second = self.p.publish('2026-11-14', label='Clarify the observation')
        self.p.git('checkout', '-q', 'main')
        self.p.git('merge', '--no-ff', '-qm', 'Merge the reviewed revision', 'revised')
        merge = self.p.git('rev-parse', 'HEAD')
        catalog, evidence = self.discover()
        self.assertEqual([first, second], evidence['publications'])
        self.assertEqual(second, catalog['works'][0]['recommended_revision'])
        self.assertNotIn(merge, evidence['publications'])
        self.assertEqual(second, catalog['works'][0]['editions'][1]['revision'])

    def test_one_merge_may_carry_several_separately_signed_work_editions(self):
        first = self.p.publish()
        self.draft_work()
        self.p.git('checkout', '-qb', 'reviewed-works')
        second = self.p.publish('2026-11-14', label='Revise the first work')
        other = self.p.publish('2026-11-15', slug='other-notes', label='Publish the second work')
        self.p.git('checkout', '-q', 'main')
        (self.p.repo / 'README.md').write_text('Repository maintenance.\n')
        self.commit('Add repository notes', signed=False)
        self.p.git('merge', '--no-ff', '-qm', 'Integrate the reviewed works', 'reviewed-works')
        catalog, evidence = self.discover()
        self.assertEqual({first, second, other}, set(evidence['publications']))
        works = {work['slug']: work for work in catalog['works']}
        self.assertEqual(second, works['field-notes']['recommended_revision'])
        self.assertEqual(other, works['other-notes']['recommended_revision'])

    def test_signed_merge_may_introduce_one_new_edition_after_its_parents(self):
        first = self.p.publish()
        self.p.git('checkout', '-qb', 'revised')
        second = self.p.publish('2026-11-14')
        self.p.git('checkout', '-q', 'main')
        self.start_merge('revised')
        self.edition(3)
        with self.manuscript().open('a') as stream:
            stream.write('\nA correction made while integrating the revision.\n')
        merged = self.commit('Resolve the reviewed revision with an additional correction')
        catalog, evidence = self.discover()
        self.assertEqual([first, second, merged], evidence['publications'])
        self.assertEqual(3, verify_shape(self.p.repo, merged, 'publication')['edition'])
        self.assertEqual(merged, catalog['works'][0]['recommended_revision'])

    def test_merge_cannot_restore_an_older_parent_work_even_when_signed(self):
        first = self.p.publish()
        self.p.git('checkout', '-qb', 'revised')
        self.p.publish('2026-11-14')
        self.p.git('checkout', '-q', 'main')
        self.start_merge('revised')
        self.manuscript().write_text(self.p.git('show', first + ':works/field-notes/manuscript.md') + '\n')
        self.commit('Choose the old parent without issuing a new edition')
        with self.assertRaises(ValueError):
            self.discover()

    def test_parallel_origins_of_the_same_edition_are_ambiguous_even_with_identical_trees(self):
        first = self.p.publish()
        self.p.git('checkout', '-qb', 'parallel')
        left = self.p.publish('2026-11-14', label='One edition two')
        self.p.git('checkout', '-q', 'main')
        right = self.p.publish('2026-11-15', label='Another edition two')
        self.assertNotEqual(left, right)
        self.assertEqual(self.p.git('rev-parse', left + ':works/field-notes'),
                         self.p.git('rev-parse', right + ':works/field-notes'))
        self.p.git('merge', '--no-ff', '-qm', 'Merge identical content with conflicting identities', 'parallel')
        with self.assertRaises(ValueError):
            self.discover()
        # Each branch is independently valid before their histories are combined.
        self.assertEqual([first, left], self.discover(ref=left)[1]['publications'])
        self.assertEqual([first, right], self.discover(ref=right)[1]['publications'])

    def test_published_work_cannot_be_deleted_or_reset_to_draft(self):
        first = self.p.publish()
        for change in ('delete', 'draft'):
            with self.subTest(change=change):
                self.p.git('reset', '--hard', first)
                if change == 'delete':
                    self.p.git('rm', '-r', 'works/field-notes')
                else:
                    self.edition(0)
                self.commit('Attempt to remove an existing publication from current source')
                with self.assertRaises(ValueError):
                    self.discover()

    def test_every_part_of_a_numbered_work_including_file_modes_requires_a_bump(self):
        support = self.p.repo / 'works/field-notes/supporting/context.md'
        support.parent.mkdir()
        support.write_text('# Context\n\nAn original observation.\n')
        self.p.git('add', '-A')
        first = self.p.publish()
        for change in ('supporting', 'asset', 'mode'):
            with self.subTest(change=change):
                self.p.git('reset', '--hard', first)
                if change == 'supporting':
                    support.write_text('# Context\n\nA corrected observation.\n')
                elif change == 'asset':
                    asset = self.p.repo / 'works/field-notes/assets/image.json'
                    asset.parent.mkdir(exist_ok=True)
                    asset.write_text('{"changed": true}\n')
                else:
                    self.p.git('update-index', '--chmod=+x', 'works/field-notes/manuscript.md')
                    self.manuscript().chmod(0o755)
                self.commit('Change a work without changing its edition')
                with self.assertRaises(ValueError):
                    self.discover()

    def test_edition_bump_itself_is_a_valid_release_without_a_second_content_change(self):
        first = self.p.publish()
        second = self.p.publish('2026-11-14', label='Reaffirm this edition')
        self.assertEqual([first, second], self.discover()[1]['publications'])

    def test_one_commit_cannot_introduce_two_work_editions(self):
        self.p.publish()
        self.draft_work()
        self.edition(2)
        self.edition(1, 'other-notes')
        self.commit('Two editions cannot share one publication identity')
        with self.assertRaises(ValueError):
            self.discover()

    def test_unsigned_bump_does_not_become_a_publication(self):
        self.p.publish()
        self.edition(2)
        self.commit('An unsigned publication attempt', signed=False)
        with self.assertRaises(ValueError):
            self.discover()

    def test_unmerged_draft_edits_are_private_to_the_selected_history(self):
        first = self.p.publish()
        self.p.git('checkout', '-qb', 'unfinished')
        with self.manuscript().open('a') as stream:
            stream.write('\nAn unfinished edit that still carries edition one.\n')
        self.commit('Continue drafting before making a release', signed=False)
        self.p.git('checkout', '-q', 'main')
        self.assertEqual([first], self.discover()[1]['publications'])
        self.p.git('merge', '--no-ff', '-qm', 'Accept an unfinished history', 'unfinished')
        with self.assertRaises(ValueError):
            self.discover()

    def test_zero_editions_allow_multiple_unpublished_drafts_and_their_removal(self):
        self.draft_work()
        for slug in ('field-notes', 'other-notes'):
            with self.manuscript(slug).open('a') as stream:
                stream.write('\nStill preparing the first edition.\n')
        self.commit('Revise both unpublished drafts', signed=False)
        self.p.git('rm', '-r', 'works/other-notes')
        self.commit('Abandon the second unpublished draft', signed=False)
        catalog, evidence = self.discover()
        self.assertEqual([], catalog['works'])
        self.assertEqual([], evidence['publications'])

    def test_commit_message_keywords_have_no_publication_semantics(self):
        message = ('GuavaJam-Publication: not a protocol\n\n'
                   'GuavaJam-Work: works/some-other-work/manuscript.md\n'
                   'GuavaJam-Repository: nonsense\n'
                   'This is ordinary explanatory text after old-looking trailers.')
        revision = self.p.publish(label=message)
        declared = verify_shape(self.p.repo, revision, 'publication')
        self.assertEqual('works/field-notes/manuscript.md', declared['manuscript'])
        self.assertEqual(message, declared['message'].rstrip('\n'))
        self.p.git('commit', '--allow-empty', '-qm', 'GuavaJam-Publication: 99\n\nAnything may follow.')
        self.p.git('commit', '--allow-empty', '-S', '-qm',
                   'GuavaJam-Publication: 1\nGuavaJam-Work: works/field-notes/manuscript.md\n'
                   'GuavaJam-Published: 2026-11-13')
        self.assertEqual([revision], self.discover()[1]['publications'])

    def test_empty_and_unusual_ordinary_messages_do_not_change_publication_identity(self):
        revisions = []
        messages = ('', 'Re\u0301vision 👨\u200d👩\u200d👧\n\nA body\twith whitespace.\r\n')
        for number, message in enumerate(messages, 1):
            self.edition(number)
            self.p.git('add', '-A')
            self.p.git('commit', '-S', '--allow-empty-message', '--cleanup=verbatim', '-qm', message)
            revision = self.p.git('rev-parse', 'HEAD')
            revisions.append(revision)
            declared = verify_shape(self.p.repo, revision, 'publication')
            self.assertEqual(message.rstrip('\n'), declared['message'].rstrip('\n'))
        self.assertEqual(revisions, self.discover()[1]['publications'])

    def test_commit_dates_do_not_reorder_editions_or_change_the_recommendation(self):
        first = self.p.publish('2026-11-14', label='First edition')
        second = self.p.publish('2026-10-01', label='Second edition with an earlier clock')
        catalog, evidence = self.discover()
        work, = catalog['works']
        self.assertEqual([first, second], evidence['publications'])
        self.assertEqual([first, second], [edition['revision'] for edition in work['editions']])
        self.assertEqual([1, 2], [edition['edition'] for edition in work['editions']])
        self.assertEqual(['2026-11-14', '2026-10-01'], [edition['published'] for edition in work['editions']])
        self.assertEqual(second, work['recommended_revision'])

    def test_a_signed_root_commit_can_publish_the_first_edition(self):
        self.p.git('checkout', '--orphan', 'initial-publication')
        self.edition(1)
        revision = self.commit('An ordinary initial publication commit')
        catalog, evidence = self.discover(ref=revision)
        self.assertEqual([revision], evidence['publications'])
        self.assertEqual(1, verify_shape(self.p.repo, revision, 'publication')['edition'])
        self.assertEqual(revision, catalog['works'][0]['recommended_revision'])

    def test_source_identity_can_be_verified_without_repository_catalog_settings(self):
        self.p.git('checkout', '--orphan', 'standalone-work')
        self.p.git('rm', '-f', 'guavajam.json')
        self.edition(1)
        revision = self.commit('Publish the standalone work')
        record = verify_publication(self.p.repo, revision, allowed_signers=self.p.allowed_signers,
                                    online_media=False, validate_tree=False)
        self.assertEqual(revision, record['revision'])
        self.assertEqual(1, record['edition'])
        self.assertEqual('works/field-notes/manuscript.md', record['manuscript'])
        self.assertEqual('Publish the standalone work', record['message'].rstrip('\n'))

    def test_independent_source_verification_rejects_an_untrusted_edition_ancestor(self):
        outsider = self.p.root / 'outsider-key'
        command(self.p.repo, 'ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(outsider), env=self.p.env)
        self.p.git('config', 'user.signingkey', str(outsider))
        self.p.publish()
        self.p.git('config', 'user.signingkey', str(self.p.root / 'signing-key'))
        revision = self.p.publish('2026-11-14')
        with self.assertRaises(ValueError):
            verify_publication(self.p.repo, revision, allowed_signers=self.p.allowed_signers,
                               online_media=False, validate_tree=False)

    def test_discovery_checks_object_hashes_before_trusting_signed_metadata(self):
        revision = self.p.publish()
        blob_id = self.p.git('rev-parse', revision + ':works/field-notes/manuscript.md')
        loose = self.p.repo / '.git' / 'objects' / blob_id[:2] / blob_id[2:]
        original = zlib.decompress(loose.read_bytes()).split(b'\x00', 1)[1]
        forged = original.replace(b'rights:', b'identifiers:\n  concept_doi: 10.1234/forged\nrights:')
        loose.chmod(0o600)
        loose.write_bytes(zlib.compress(b'blob ' + str(len(forged)).encode() + b'\x00' + forged))
        # The signed commit remains intact, but its named object graph does not.
        # Reading with cat-file alone would attribute the forged DOI to its signer.
        with self.assertRaises(ValueError):
            self.discover()

    def test_unsigned_merge_carries_the_original_signed_repository_approval(self):
        self.p.git('checkout', '-qb', 'reviewed-settings')
        self.p.settings['publisher'] = 'Reviewed Publisher'
        approved = self.p.amend_settings()
        self.p.git('checkout', '-q', 'main')
        (self.p.repo / 'README.md').write_text('Unrelated repository notes.\n')
        self.commit('Add notes without changing publisher settings', signed=False)
        self.p.git('merge', '--no-ff', '-qm', 'Integrate the approved settings', 'reviewed-settings')
        catalog, evidence = self.discover()
        self.assertEqual('Reviewed Publisher', catalog['publisher'])
        self.assertEqual(approved, evidence['repository_checkpoint'])

    def test_unsigned_merge_carries_a_later_matching_approval_after_signed_reversion(self):
        self.p.git('checkout', '-qb', 'reviewed-settings')
        self.p.settings['publisher'] = 'Reviewed Publisher'
        self.p.amend_settings()
        self.p.settings['publisher'] = 'Independent Publisher'
        approved = self.p.amend_settings()
        self.p.git('checkout', '-q', 'main')
        self.p.git('merge', '--no-ff', '-qm', 'Integrate the reviewed settings reversion', 'reviewed-settings')
        catalog, evidence = self.discover()
        self.assertEqual('Independent Publisher', catalog['publisher'])
        self.assertEqual(approved, evidence['repository_checkpoint'])

    def restore_old_settings_in_merge(self, *, signed):
        settings = self.p.repo / 'guavajam.json'
        old = settings.read_bytes()
        self.p.git('checkout', '-qb', 'reviewed-settings')
        self.p.settings['publisher'] = 'Reviewed Publisher'
        self.p.amend_settings()
        self.p.git('checkout', '-q', 'main')
        self.start_merge('reviewed-settings')
        settings.write_bytes(old)
        return self.commit('Restore the earlier publisher settings', signed=signed)

    def test_unsigned_merge_cannot_roll_back_an_approved_repository_snapshot(self):
        self.restore_old_settings_in_merge(signed=False)
        with self.assertRaises(ValueError):
            self.discover()

    def test_signed_merge_can_explicitly_approve_a_repository_settings_reversion(self):
        approved = self.restore_old_settings_in_merge(signed=True)
        catalog, evidence = self.discover()
        self.assertEqual('Independent Publisher', catalog['publisher'])
        self.assertEqual(approved, evidence['repository_checkpoint'])


if __name__ == '__main__':
    unittest.main()
