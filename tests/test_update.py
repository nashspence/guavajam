"""Periodic, publisher-driven build; real signed history and real HTML generation."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from discovery_fixture import Publisher
from guavajam.update import update, locked_root, atomic
from guavajam.catalog import CatalogError, encoded
from guavajam.renderer import render
from guavajam.publication_protocol import discover


class PeriodicUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.p=Publisher(self.temp.name)
        self.root=Path(self.temp.name);self.output=self.root/'deployed'
        self.revision=self.p.publish('2026-11-13')
        env=patch.dict(os.environ,{'GNUPGHOME':str(self.p.home)})
        env.start();self.addCleanup(env.stop)

    def update(self, **kwargs):
        kwargs.setdefault('no_fetch',True)
        return update(self.p.repo,self.output,fingerprints=[self.p.fingerprint],**kwargs)

    def selected(self):
        return os.readlink(self.output/'current')

    def test_initial_build_and_noop_need_no_renderer_catalog_and_skip_all_rendering(self):
        with patch('guavajam.view_cache.render', wraps=render) as called:
            first=self.update();self.assertTrue(first['changed']);self.assertEqual(1,called.call_count)
            target=self.selected();stamp=(self.output/'current').lstat().st_mtime_ns
            again=self.update();self.assertFalse(again['changed']);self.assertEqual(1,called.call_count)
            self.assertEqual(target,self.selected());self.assertEqual(stamp,(self.output/'current').lstat().st_mtime_ns)
        current=self.output/'current'
        catalog=json.loads((current/'CATALOG.json').read_text())
        self.assertEqual(self.revision,catalog['works'][0]['recommended_revision'])
        download=current/'works/field-notes/editions'/self.revision/'read/source.gfm'
        self.assertEqual(self.p.git('show',self.revision+':works/field-notes/manuscript.md'),download.read_text().strip())

    def test_ordinary_source_changes_do_not_rebuild_and_new_publications_render_only_once(self):
        self.update()
        with patch('guavajam.view_cache.render', wraps=render) as called:
            self.p.git('commit','--allow-empty','-qm','Unrelated administrative change')
            self.assertFalse(self.update()['changed']);self.assertEqual(0,called.call_count)
            self.p.source('field-notes','Revised Field Notes','A new observation.')
            self.p.git('add','-A');self.p.git('commit','-qm','Review new content')
            sha=self.p.publish('2026-11-14')
            self.assertTrue(self.update()['changed']);self.assertEqual(1,called.call_count)
            self.assertIn(sha,(self.output/'current/CATALOG.json').read_text())

    def test_metadata_updates_reuse_views_and_keep_withdrawn_sources(self):
        self.update();before=self.selected()
        self.p.settings['editions']={self.revision:{'status':'withdrawn','notices':[{'date':'2026-11-14','text':'Withdrawn. Historical source retained.'}]}}
        self.p.amend_settings()
        with patch('guavajam.view_cache.render',side_effect=AssertionError('unchanged view must be reused')):
            self.assertTrue(self.update()['changed'])
        self.assertNotEqual(before,self.selected())
        reader=self.output/'current/works/field-notes/editions'/self.revision/'read'
        self.assertTrue((reader/'source.gfm').is_file())
        self.assertIn('Withdrawn. Historical source retained.',(reader/'index.html').read_text())
        self.p.settings['editions'][self.revision].update(status='unavailable')
        self.p.settings['editions'][self.revision]['notices'].append({'date':'2026-11-15','text':'Distribution is unavailable for a separate stated reason.'})
        self.p.amend_settings();self.update()
        self.assertFalse(reader.exists())
        # The previous full generation is retained, but is not the served root.
        self.assertTrue((self.output/before).is_dir())

    def test_bad_signature_unsealed_policy_or_rebinding_never_switches_the_live_site(self):
        self.p.settings['editions']={self.revision:{'doi':'10.1234/field-notes.v1'}}
        self.p.amend_settings();self.update();old=self.selected()
        self.p.settings['editions'][self.revision]['doi']='10.1234/wrong'
        self.p.amend_settings()
        with self.assertRaisesRegex(ValueError,'rebound'):self.update()
        self.assertEqual(old,self.selected())
        with self.assertRaises(ValueError):
            update(self.p.repo,self.output,no_fetch=True,fingerprints=['F'*40])
        self.assertEqual(old,self.selected())

    def test_fetches_only_configured_branch_without_changing_checkout_and_refuses_rewind(self):
        remote=self.root/'origin.git'
        subprocess.run(['git','clone','--bare','--quiet',str(self.p.repo),str(remote)],check=True)
        self.p.git('remote','add','origin',str(remote))
        checkout=self.p.git('rev-parse','HEAD')
        self.update(no_fetch=False)
        self.assertEqual(checkout,self.p.git('rev-parse','HEAD'))
        old=self.selected()
        subprocess.run(['git','--git-dir='+str(remote),'update-ref','refs/heads/main',self.p.git('rev-parse','HEAD^')],check=True)
        with self.assertRaises(ValueError):self.update(no_fetch=False)
        self.assertEqual(old,self.selected())

    def test_no_fetch_also_refuses_local_history_rollback(self):
        self.update();old=self.selected()
        self.p.git('reset','--hard','HEAD^')
        with self.assertRaises(ValueError):self.update()
        self.assertEqual(old,self.selected())

    def test_overlapping_runs_do_not_duplicate_work(self):
        with locked_root(self.output,self.p.repo.resolve()) as locked:
            self.assertIsNotNone(locked)
            self.assertEqual('busy',self.update()['status'])
        self.assertFalse((self.output/'current').exists())

    def test_failed_build_keeps_current_and_leaves_no_half_deployed_generation(self):
        self.update();old=self.selected()
        self.p.publish('2026-11-14')
        with patch('guavajam.update.build_site',side_effect=ValueError('synthetic failed build')):
            with self.assertRaisesRegex(ValueError,'synthetic'):self.update()
        self.assertEqual(old,self.selected());self.assertEqual([],list(self.output.glob('.staging-*')))

    def test_a_new_renderer_or_explicit_rebuild_cannot_reuse_obsolete_views(self):
        self.update()
        with patch('guavajam.update.renderer_identity',return_value='different-renderer'),patch('guavajam.view_cache.render',wraps=render) as called:
            self.assertTrue(self.update()['changed']);self.assertEqual(1,called.call_count)
        with patch('guavajam.view_cache.render',wraps=render) as called:
            self.assertTrue(self.update(rebuild=True)['changed']);self.assertEqual(1,called.call_count)

    def test_signer_rotation_reverifies_history_and_invalidates_cached_views(self):
        self.update()
        subprocess.run(['gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '',
                        '--quick-generate-key', 'Rotated Fixture <rotation@example.org>',
                        'ed25519', 'sign', '0'], env=self.p.env, check=True, capture_output=True)
        listing = subprocess.check_output(['gpg', '--with-colons', '--list-keys',
                                          'rotation@example.org'], env=self.p.env).decode()
        rotated = next(row.split(':')[9] for row in listing.splitlines()
                       if row.startswith('fpr:'))
        self.p.git('config', 'user.signingkey', rotated)
        revision = self.p.publish('2026-11-14')
        trusted = [self.p.fingerprint, rotated]
        with patch('guavajam.view_cache.render', wraps=render) as called:
            result = update(self.p.repo, self.output, no_fetch=True, fingerprints=trusted)
        self.assertTrue(result['changed'])
        self.assertEqual(2, called.call_count)
        self.assertIn(revision, (self.output / 'current/CATALOG.json').read_text())
        selected = self.selected()
        with patch('guavajam.view_cache.render', side_effect=AssertionError('no-op must reuse views')):
            self.assertFalse(update(self.p.repo, self.output, no_fetch=True,
                                    fingerprints=trusted)['changed'])
        with self.assertRaisesRegex(ValueError, 'untrusted'):
            update(self.p.repo, self.output, no_fetch=True, fingerprints=[rotated])
        self.assertEqual(selected, self.selected())

    def test_never_overwrites_unmanaged_site_or_recovers_by_discarding_baseline(self):
        self.output.mkdir();(self.output/'index.html').write_text('Existing website')
        with self.assertRaisesRegex(ValueError,'not an updater-managed'):self.update()
        self.assertEqual('Existing website',(self.output/'index.html').read_text())
        shutil.rmtree(self.output);self.update();(self.output/'current').unlink()
        with self.assertRaisesRegex(ValueError,'baseline'):self.update()

    def test_first_switch_state_failure_cannot_reset_the_catalog_baseline(self):
        self.p.settings['editions'] = {self.revision: {'doi': '10.1234/original'}}
        self.p.amend_settings()

        def fail_last_record(path, data):
            if path.name == '.last.json':
                raise OSError('synthetic state-write failure after the switch')
            return atomic(path, data)

        with patch('guavajam.update.atomic', side_effect=fail_last_record):
            with self.assertRaisesRegex(OSError, 'state-write failure'):
                self.update()
        self.assertTrue((self.output / 'current').is_symlink())
        self.assertFalse((self.output / '.last.json').exists())
        selected = self.selected()
        self.p.settings['editions'][self.revision]['doi'] = '10.1234/rebound'
        self.p.amend_settings()
        with self.assertRaisesRegex(ValueError, 'rebound'):
            self.update()
        self.assertEqual(selected, self.selected())
        (self.output / 'current').unlink()
        with self.assertRaisesRegex(ValueError, 'baseline'):
            self.update()
        self.assertFalse((self.output / 'current').exists())
        self.assertEqual(1, len(list((self.output / 'generations').iterdir())))

    def test_migration_baseline_preserves_existing_doi_and_requires_publisher_overrides(self):
        catalog,_=discover(self.p.repo,fingerprints=[self.p.fingerprint])
        catalog['works'][0]['editions'][0]['doi']='10.1234/old'
        baseline=self.root/'old.json';baseline.write_bytes(encoded(catalog))
        with self.assertRaisesRegex(ValueError,'rebound'):
            self.update(baseline=baseline)
        self.assertFalse((self.output/'current').exists())
        self.p.settings['editions']={self.revision:{'doi':'10.1234/old'}};self.p.amend_settings()
        self.update(baseline=baseline)
        with self.assertRaisesRegex(ValueError,'only allowed before'):
            self.update(baseline=baseline)

    def test_real_make_update_defaults_do_not_need_a_catalog_argument_or_execute_source_code(self):
        import sys
        empty_root=self.root/'empty';empty_root.mkdir()
        publisher=Publisher(empty_root)
        (publisher.repo/'Makefile').write_text('all:\n\ttouch SHOULD_NOT_RUN\n')
        publisher.git('add','Makefile');publisher.git('commit','-qm','An unrelated executable source file')
        root=Path(__file__).resolve().parents[1]
        result=subprocess.run(['make','--no-print-directory','update','PYTHON='+sys.executable,
                               'REPO='+str(publisher.repo),'OUTPUT='+str(self.output),
                               'NO_FETCH=1','TRUSTED_GPG_FINGERPRINT='+publisher.fingerprint,
                               'SSH_ALLOWED_SIGNERS='],cwd=root,env=publisher.env,
                              capture_output=True,text=True,check=True)
        self.assertEqual('updated',json.loads(result.stdout)['status'])
        self.assertFalse((publisher.repo/'SHOULD_NOT_RUN').exists())
        self.assertTrue((self.output/'current/index.html').is_file())

    def test_cache_corruption_fails_closed_and_rebuild_recovers_without_source_changes(self):
        self.update();old=self.selected()
        cached=next((self.output/'.cache').glob('*/*/index.html'))
        cached.write_text('corrupted cache')
        self.p.settings['description']='A new site description';self.p.amend_settings()
        with self.assertRaisesRegex(ValueError,'corrupt view cache'):self.update()
        self.assertEqual(old,self.selected())
        self.assertTrue(self.update(rebuild=True)['changed'])
        self.p.settings['description']='Another reviewed description';self.p.amend_settings()
        with patch('guavajam.view_cache.render',side_effect=AssertionError('new cache must be reusable')):
            self.assertTrue(self.update()['changed'])


if __name__=='__main__':unittest.main()
