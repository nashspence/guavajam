"""Publisher-owned automatic index; signed metadata, not tags or Git timestamps.

Copy templates/site.json to press-site.json, choose the publisher's real origin,
review/commit it, then sign an empty checkpoint ending in Press-Site: 1. New
publications use Press-Publication: 2, Press-Work and Press-Published trailers.
Legacy v1 commits remain unchanged; give them reviewed dates in editions[SHA].
Later DOI/status/notices live in that same signed publisher settings file.
The exact downstream output is guavajam.catalog.v1; no duplicate renderer config.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from guavajam_core.publication_protocol import discover, declaration, publication_trailers, policy, verify_shape
from guavajam_core.catalog import CatalogError
from guavajam_test_support.discovery_fixture import Publisher


class DeclarationTests(unittest.TestCase):
    def test_exact_v1_and_dated_v2_are_supported_without_tags(self):
        self.assertEqual(1, declaration('Publish\n\nPress-Publication: 1\nPress-Work: works/essay/manuscript.md\n')['version'])
        lines = publication_trailers('works/essay/manuscript.md', '2026-11-13', 'Second edition')
        parsed = declaration('Publish\n\n' + '\n'.join(lines))
        self.assertEqual('Second edition', parsed['label'])
        self.assertEqual('2026-11-13', parsed['published'])
        for bad in ['\nPress-Published: 2026-11-13', '\nPress-Site: 1', '\nPress-Unknown: value', '\nTrailing prose']:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                declaration('Publish\n\n' + '\n'.join(lines) + bad)
        for date in ('today', '2026-02-30', '2026-11-13T10:00:00Z'):
            with self.assertRaises(ValueError):
                publication_trailers('works/essay/manuscript.md', date)

    def test_configuration_is_closed_data_not_a_template_or_authority(self):
        good = {'schema': 'press.site.v1', 'publisher': 'Publisher', 'base_url': 'https://example.org'}
        _, catalog = policy(json.dumps(good).encode())
        self.assertEqual('Publications', catalog['title'])
        self.assertEqual([], catalog['works'])
        for extra in ({'trusted_keys': []}, {'template': 'code.py'}, {'works': []}, {'schema': 'unknown'}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                policy(json.dumps({**good, **extra}).encode())
        with self.assertRaises(ValueError):
            policy(b'{"schema":1,"schema":2}')


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.p = Publisher(self.temp.name)
        patcher = patch.dict(os.environ, {'GNUPGHOME': str(self.p.home)})
        patcher.start(); self.addCleanup(patcher.stop)

    def discover(self, **kwargs):
        return discover(self.p.repo, fingerprints=[self.p.fingerprint], **kwargs)

    def test_minimal_publisher_configuration_discovers_editions_and_deterministic_defaults(self):
        first = self.p.publish('2026-11-01', label='Initial edition')
        second = self.p.publish('2026-11-13', label='Revised edition')
        catalog, evidence = self.discover()
        work, = catalog['works']
        self.assertEqual(first, work['metadata_revision'])
        self.assertEqual(second, work['recommended_revision'])
        self.assertEqual([first, second], [e['revision'] for e in work['editions']])
        self.assertEqual('Revised edition', work['editions'][1]['label'])
        self.assertEqual((catalog, evidence), self.discover())
        self.assertFalse(evidence['assets_verified'])

    def test_signed_configuration_changes_do_not_reissue_source_or_need_renderer_metadata(self):
        sha = self.p.publish(); original = self.p.git('cat-file', '-p', sha)
        self.p.settings['editions'] = {sha: {'doi': '10.1234/example.v1'}}
        self.p.settings['works'] = {'field-notes': {'concept_doi': '10.1234/example'}}
        self.p.amend_settings()
        catalog, _ = self.discover()
        self.assertEqual('10.1234/example.v1', catalog['works'][0]['editions'][0]['doi'])
        self.assertEqual(original, self.p.git('cat-file', '-p', sha))

    def test_v1_needs_explicit_legacy_date_and_can_be_indexed_without_resigning(self):
        sha = self.p.publish(version=1)
        with self.assertRaisesRegex(ValueError, 'legacy v1'):
            self.discover()
        self.p.settings['editions'] = {sha: {'published': '2026-10-01'}}
        self.p.amend_settings()
        catalog, _ = self.discover()
        self.assertEqual(sha, catalog['works'][0]['editions'][0]['revision'])
        self.assertEqual('2026-10-01', catalog['works'][0]['editions'][0]['published'])

    def test_unsigned_or_unapproved_policy_is_not_applied(self):
        self.p.publish()
        self.p.settings['publisher'] = 'Unapproved'; self.p.write_settings()
        # A dirty checkout is ignored: publication discovery reads committed blobs.
        self.assertEqual('Independent Publisher', self.discover()[0]['publisher'])
        self.p.git('add', 'press-site.json'); self.p.git('commit', '-qm', 'Unsigned settings change')
        with self.assertRaisesRegex(ValueError, 'without a new signed'):
            self.discover()
        self.p.seal(signed=False)
        with self.assertRaisesRegex(ValueError, 'signed commit'):
            self.discover()

    def test_only_the_selected_history_counts_never_tags_or_side_branches(self):
        first = self.p.publish()
        self.p.git('checkout', '-qb', 'unaccepted')
        self.p.publish('2026-11-14')
        self.p.git('tag', 'press/unaccepted')
        self.p.git('checkout', '-q', 'main')
        self.assertEqual([first], self.discover()[1]['publications'])
        with self.assertRaisesRegex(ValueError, 'never a tag'):
            self.discover(ref='refs/tags/press/unaccepted')

    def test_invalid_publication_cannot_be_skipped_or_whitelisted_by_source(self):
        self.p.publish()
        self.p.git('commit', '--allow-empty', '-qm', 'Bad\n\nPress-Publication: 2\nPress-Work: works/field-notes/manuscript.md\nPress-Published: 2026-11-13')
        with self.assertRaisesRegex(ValueError, 'signed commit'):
            self.discover()
        with self.assertRaises(ValueError):
            discover(self.p.repo, fingerprints=['A'*40])

    def test_prior_catalog_keeps_dois_notices_and_withdrawn_sources(self):
        sha = self.p.publish()
        self.p.settings['editions'] = {sha: {'doi': '10.1234/example.v1'}}
        self.p.amend_settings(); old, _ = self.discover()
        override = self.p.settings['editions'][sha]
        override.update(status='withdrawn', notices=[{'date': '2026-11-14', 'text': 'Withdrawn, historical access retained.'}])
        self.p.amend_settings(); new, _ = self.discover(previous=old)
        self.assertEqual('withdrawn', new['works'][0]['editions'][0]['status'])
        self.assertNotIn('recommended_revision', new['works'][0])
        override['doi'] = '10.1234/rebound'; self.p.amend_settings()
        with self.assertRaisesRegex(ValueError, 'rebound'):
            self.discover(previous=new)

    def test_ssh_discovery_uses_only_independently_managed_signers(self):
        # Replace the fixture's GPG checkpoint with an SSH-authorized history.
        self.p.git('reset', '--hard', 'HEAD^')
        key = self.p.root / 'publisher-ssh'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)],
                       check=True, capture_output=True)
        self.p.git('config', 'gpg.format', 'ssh')
        self.p.git('config', 'user.signingkey', str(key))
        checkpoint = self.p.seal()
        revision = self.p.publish(label='SSH-authorized edition')
        signers = self.p.root / 'allowed-signers'
        signers.write_text('publisher@example.org ' + key.with_suffix('.pub').read_text())
        catalog, evidence = discover(self.p.repo, allowed_signers=signers)
        self.assertEqual(checkpoint, evidence['site_checkpoint'])
        self.assertEqual([revision], evidence['publications'])
        self.assertEqual(revision, catalog['works'][0]['recommended_revision'])
        untrusted = self.p.root / 'untrusted-signers'
        untrusted.write_text('')
        with self.assertRaisesRegex(ValueError, 'invalid or unauthorized'):
            discover(self.p.repo, allowed_signers=untrusted)
        source_signers = self.p.repo / 'allowed-signers'
        source_signers.write_bytes(signers.read_bytes())
        with self.assertRaisesRegex(ValueError, 'outside the publication repository'):
            discover(self.p.repo, allowed_signers=source_signers)

    def test_missing_unknown_symlink_and_shallow_data_fail_closed(self):
        self.p.publish()
        self.p.settings['editions'] = {'f'*40: {'published': '2026-11-13'}}
        self.p.amend_settings()
        with self.assertRaisesRegex(ValueError, 'undiscovered'):
            self.discover()
        self.p.settings.pop('editions'); self.p.amend_settings()
        file = self.p.repo/'press-site.json'; file.unlink(); file.symlink_to('README.md')
        self.p.git('add','-A');self.p.git('commit','-qm','Symlink settings');self.p.seal()
        with self.assertRaisesRegex(ValueError, 'regular'):
            self.discover()


if __name__ == '__main__':
    unittest.main()
