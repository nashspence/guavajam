"""Executable protocol: a work is content/, editions follow signed Git history.

Only the selected first-parent history is accepted. Each changed content tree
requires an independently trusted signature and valid source. Ordinary messages,
merges, reverts and housekeeping retain their normal Git meaning.
"""
from pathlib import Path
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from guavajam.catalog import CatalogError
from guavajam.publication_protocol import discover, content_tree, verify_shape
from guavajam.publication_commit import verify
from guavajam_test_support.discovery_fixture import Publisher, Draft


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.p = Publisher(self.temp.name)
        self.first = self.p.revisions[0]

    def discover(self, **kwargs):
        return discover(self.p.repo, **self.p.trust, **kwargs)[0]

    def change(self, body='A second observation.', message='Revise the observation', **kwargs):
        self.p.source('Field Notes', body)
        return self.p.publish(message, **kwargs)

    def test_one_work_needs_no_registry_declared_version_or_view(self):
        result = self.discover()
        self.assertEqual({'schema', 'hash_algorithm', 'tip', 'editions'}, result.keys())
        self.assertEqual([self.first], [e['revision'] for e in result['editions']])
        edition = result['editions'][0]
        self.assertEqual(content_tree(self.p.repo, self.first), edition['tree'])
        self.assertEqual('Initial edition\n', edition['message'])
        self.assertFalse((self.p.repo / 'guavajam.json').exists())
        proof = verify(self.p.repo, self.first, online_media=False, **self.p.trust)
        self.assertEqual('content/manuscript.md', proof['manuscript'])

    def test_messages_are_opaque_even_when_they_look_like_old_protocol_trailers(self):
        message = 'Correct an observation\n\nWhy this changed.\n\nGuavaJam-Publication: anything\nGuavaJam-Work: prose'
        second = self.change(message=message)
        edition = self.discover()['editions'][-1]
        self.assertEqual(second, edition['revision'])
        self.assertEqual(message + '\n', edition['message'])

    def test_unsigned_housekeeping_and_empty_commits_do_not_publish(self):
        (self.p.repo / 'Makefile').write_text('all:\n\t@true\n')
        self.p.git('add', 'Makefile')
        self.p.git('commit', '-qm', 'Maintain tooling\n\nGuavaJam-Publication: 1')
        self.p.git('commit', '-S', '--allow-empty', '-qm', 'A signed empty commit')
        result = self.discover()
        self.assertEqual(1, len(result['editions']))
        self.assertEqual(self.p.git('rev-parse', 'HEAD'), result['tip'])
        with self.assertRaisesRegex(ValueError, 'must change content'):
            verify_shape(self.p.repo, result['tip'])

    def test_unsigned_content_is_rejected_not_skipped_before_a_later_signed_change(self):
        self.p.source('Field Notes', 'Unsigned change.')
        self.p.git('add', 'content')
        self.p.git('commit', '-qm', 'An unsigned revision')
        self.change('Signed after unsigned.')
        with self.assertRaisesRegex(ValueError, 'signed commit'):
            self.discover()

    def test_trust_cannot_be_asserted_by_the_source_repository(self):
        bad = Path(self.temp.name) / 'untrusted-signers'
        bad.write_text('somebody ssh-ed25519 AAAA\n')
        with self.assertRaises(ValueError):
            discover(self.p.repo, allowed_signers=bad)
        inside = self.p.repo / 'allowed-signers'
        shutil.copyfile(self.p.allowed_signers, inside)
        with self.assertRaisesRegex(ValueError, 'outside the publication repository'):
            discover(self.p.repo, allowed_signers=inside)
        with self.assertRaisesRegex(ValueError, 'external'):
            discover(self.p.repo)

    def test_first_content_can_be_the_signed_root_commit(self):
        self.p.git('checkout', '--orphan', 'root-publication')
        self.p.git('add', '-A')
        self.p.git('commit', '-S', '-qm', 'Publish the initial tree')
        sha = self.p.git('rev-parse', 'HEAD')
        result = self.discover(ref='refs/heads/root-publication')
        self.assertEqual([sha], [e['revision'] for e in result['editions']])

    def test_bootstrap_history_without_content_has_no_editions(self):
        first_parent = self.p.git('rev-parse', self.first + '^')
        result, _ = discover(self.p.repo, ref=first_parent)
        self.assertEqual([], result['editions'])

    def test_side_branch_drafts_are_not_published_by_being_reachable(self):
        self.p.git('switch', '-c', 'draft')
        self.p.source('Field Notes', 'Intermediate unsigned draft.')
        self.p.git('add', 'content')
        self.p.git('commit', '-qm', 'Explore the argument')
        branch_sha = self.change('Reviewed branch result.')
        self.p.git('switch', 'main')
        self.p.git('merge', '--no-ff', '-S', '-m', 'Revise the argument\n\nExplain the integrated result.', 'draft')
        merge = self.p.git('rev-parse', 'HEAD')
        editions = self.discover()['editions']
        self.assertEqual([self.first, merge], [e['revision'] for e in editions])
        self.assertNotIn(branch_sha, [e['revision'] for e in editions])
        self.assertEqual(content_tree(self.p.repo, branch_sha), editions[-1]['tree'])
        # The branch revision still provides an independently verifiable source
        # snapshot; branch acceptance is a separate discovery operation.
        self.assertEqual(branch_sha, verify(self.p.repo, branch_sha, online_media=False, **self.p.trust)['revision'])

    def test_unsigned_merge_of_signed_content_is_rejected(self):
        self.p.git('switch', '-c', 'draft')
        self.change()
        self.p.git('switch', 'main')
        self.p.git('merge', '--no-ff', '-m', 'Merge reviewed work without signing', 'draft')
        with self.assertRaisesRegex(ValueError, 'signed commit'):
            self.discover()

    def test_fast_forward_preserves_individual_signed_editions(self):
        self.p.git('switch', '-c', 'draft')
        second = self.change()
        third = self.change('A third observation.')
        self.p.git('switch', 'main')
        self.p.git('merge', '--ff-only', 'draft')
        self.assertEqual([self.first, second, third], [e['revision'] for e in self.discover()['editions']])

    def test_fast_forward_of_unsigned_drafts_is_not_a_publication_shortcut(self):
        self.p.git('switch', '-c', 'draft')
        self.p.source('Field Notes', 'Unsigned branch draft.')
        self.p.git('add', 'content')
        self.p.git('commit', '-qm', 'Draft')
        self.p.git('switch', 'main')
        self.p.git('merge', '--ff-only', 'draft')
        with self.assertRaisesRegex(ValueError, 'signed commit'):
            self.discover()

    def test_revert_is_a_new_edition_even_when_a_previous_tree_returns(self):
        second = self.change()
        self.p.git('revert', '--no-edit', '-S', second)
        reverted = self.p.git('rev-parse', 'HEAD')
        editions = self.discover()['editions']
        self.assertEqual([self.first, second, reverted], [e['revision'] for e in editions])
        self.assertEqual(editions[0]['tree'], editions[-1]['tree'])

    def test_content_mode_supplement_and_asset_policy_changes_are_editions(self):
        notes = self.p.repo / 'content' / 'calculation.py'
        notes.write_text('print(1 + 1)\n')
        added = self.p.publish('Add a supporting calculation')
        self.p.git('update-index', '--chmod=+x', 'content/calculation.py')
        self.p.git('commit', '-S', '-qm', 'Make the calculation executable')
        executable = self.p.git('rev-parse', 'HEAD')
        (self.p.repo / 'content' / 'asset-policy.json').write_text('{"origin":"https://media.example.org"}\n')
        policy = self.p.publish('Set the asset origin')
        self.assertEqual([self.first, added, executable, policy],
                         [e['revision'] for e in self.discover()['editions']])

    def test_commit_date_is_utc_and_never_controls_chronology(self):
        second = self.change(date='2026-01-02T00:30:00+02:00')
        third = self.change('Backdated deliberately.', date='2025-12-31T23:30:00-02:00')
        editions = self.discover()['editions']
        self.assertEqual([self.first, second, third], [e['revision'] for e in editions])
        self.assertEqual('2026-01-01', editions[-2]['published'])
        self.assertEqual('2026-01-01', editions[-1]['published'])

    def test_current_checkout_and_checkout_filters_cannot_replace_source(self):
        (self.p.repo / 'content' / 'manuscript.md').write_text('Dirty checkout')
        self.p.git('config', 'filter.substitute.smudge', 'false')
        self.assertEqual(self.first, self.discover()['editions'][0]['revision'])

    def test_content_deletion_is_rejected(self):
        self.p.git('rm', '-r', 'content')
        self.p.git('commit', '-S', '-qm', 'Remove the work')
        with self.assertRaisesRegex(ValueError, 'cannot be deleted'):
            self.discover()

    def test_content_and_manuscript_must_be_real_tree_and_regular_blob(self):
        source = self.p.repo / 'content' / 'manuscript.md'
        source.unlink()
        source.symlink_to('../README.md')
        self.p.publish('Try a symlink')
        with self.assertRaisesRegex(ValueError, 'regular'):
            self.discover()

    def test_invalid_manuscript_is_not_discoverable_even_with_a_trusted_signature(self):
        (self.p.repo / 'content' / 'manuscript.md').write_text('# No required metadata\n')
        self.p.publish('An invalid source')
        with self.assertRaisesRegex(ValueError, 'conventions'):
            self.discover()

    def test_independently_retained_tip_and_exact_editions_are_preserved(self):
        previous = self.discover()
        self.change()
        current = self.discover(previous=previous)
        self.assertEqual(previous['editions'], current['editions'][:1])
        changed = json.loads(json.dumps(previous))
        changed['editions'][0]['message'] = 'Fabricated description'
        with self.assertRaisesRegex(ValueError, 'cannot disappear, change or be reordered'):
            self.discover(previous=changed)
        self.p.git('reset', '--hard', self.first)
        with self.assertRaisesRegex(ValueError, 'first-parent'):
            self.discover(previous=current)

    def test_accepted_tip_cannot_be_demoted_into_second_parent_ancestry(self):
        second = self.change()
        previous = self.discover()
        self.p.git('switch', '-c', 'alternative', self.first)
        (self.p.repo / 'README.md').write_text('An alternative integration line.\n')
        self.p.git('add', 'README.md')
        self.p.git('commit', '-qm', 'Different first parent')
        self.p.git('merge', '--no-ff', '-S', '-m', 'Merge old main as second parent', 'main')
        self.p.git('branch', '-f', 'main', 'HEAD')
        self.p.git('merge-base', '--is-ancestor', second, 'main')
        with self.assertRaisesRegex(ValueError, 'first-parent'):
            self.discover(previous=previous)

    def test_empty_housekeeping_merge_does_not_publish(self):
        self.p.git('switch', '-c', 'housekeeping')
        (self.p.repo / 'README.md').write_text('Improve authoring documentation.\n')
        self.p.git('add', 'README.md')
        self.p.git('commit', '-qm', 'Document tools')
        self.p.git('switch', 'main')
        self.p.git('merge', '--no-ff', '-m', 'Merge tooling documentation', 'housekeeping')
        self.assertEqual(1, len(self.discover()['editions']))

    def test_shallow_history_is_not_silently_treated_as_the_first_edition(self):
        clone = Path(self.temp.name) / 'shallow'
        self.p.git('clone', '-q', '--depth=1', self.p.repo.as_uri(), str(clone))
        with self.assertRaisesRegex(ValueError, 'complete history'):
            discover(clone, **self.p.trust)

    def test_history_budget_fails_closed(self):
        self.change()
        with patch('guavajam.publication_protocol.MAX_HISTORY', 1):
            with self.assertRaisesRegex(ValueError, 'silent truncation'):
                self.discover()

    def test_local_grafts_cannot_hide_an_unsigned_content_ancestor(self):
        self.p.source('Field Notes', 'Unsigned content.')
        self.p.git('add', 'content')
        self.p.git('commit', '-qm', 'Unsigned ancestor')
        second = self.change('A signed descendant.')
        grafts = self.p.repo / '.git' / 'info' / 'grafts'
        grafts.parent.mkdir(exist_ok=True)
        grafts.write_text(second + ' ' + self.first + '\n')
        with self.assertRaisesRegex(ValueError, 'signed commit'):
            self.discover()

    def test_local_replace_refs_do_not_change_the_verified_history(self):
        self.p.source('Field Notes', 'Unsigned content.')
        self.p.git('add', 'content')
        self.p.git('commit', '-qm', 'Unsigned ancestor')
        unsigned = self.p.git('rev-parse', 'HEAD')
        second = self.change('A signed descendant.')
        self.p.git('replace', unsigned, self.first)
        with self.assertRaisesRegex(ValueError, 'signed commit'):
            self.discover()


if __name__ == '__main__':
    unittest.main()
