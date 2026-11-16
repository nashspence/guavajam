"""The publisher emits exactly the consumer catalog, without renderer setup.

The source-owned schema is schemas/guavajam-repository-v1.schema.json; strict executable
validation is in publication_protocol.py and export_catalog.py. This test uses
actual GNU make, real Git signatures and the copied authoring template.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


from guavajam_test_support import FIXTURES, RESOURCES
ROOT=Path(__file__).resolve().parents[1]
from guavajam_core.export_catalog import export
from guavajam_core.publication_protocol import discover, policy
from guavajam_core.publication_commit import commit_identity,signature
from guavajam_core.catalog import encoded
from guavajam_test_support.discovery_fixture import Publisher


class CatalogExportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.p=Publisher(self.temp.name);self.sha=self.p.publish(label='A signed edition')
        self.output=Path(self.temp.name)/'catalog.json'
        environment=patch.dict(os.environ,{'GNUPGHOME':str(self.p.home)})
        environment.start();self.addCleanup(environment.stop)

    def test_repository_discovery_and_export_need_no_web_origin(self):
        self.p.settings.pop('base_url')
        self.p.amend_settings()
        catalog, evidence = discover(self.p.repo, fingerprints=[self.p.fingerprint])
        self.assertNotIn('base_url', catalog)
        self.assertEqual([self.sha], evidence['publications'])
        _, changed = export(self.p.repo, self.output, fingerprints=[self.p.fingerprint])
        self.assertTrue(changed)
        self.assertEqual(catalog, json.loads(self.output.read_text()))

    def test_real_make_target_emits_exact_catalog_and_noop_keeps_the_file(self):
        args=['make','catalog-export','PYTHON='+sys.executable,'REPO='+str(self.p.repo),
              'OUTPUT='+str(self.output),'TRUSTED_GPG_FINGERPRINT='+self.p.fingerprint,'SSH_ALLOWED_SIGNERS=']
        subprocess.run(args,cwd=ROOT,env=self.p.env,check=True,capture_output=True)
        catalog,_=discover(self.p.repo,fingerprints=[self.p.fingerprint])
        self.assertEqual(encoded(catalog),self.output.read_bytes())
        stamp=self.output.stat().st_mtime_ns
        result=subprocess.run(args,cwd=ROOT,env=self.p.env,check=True,capture_output=True,text=True)
        self.assertIn('Unchanged',result.stdout)
        self.assertEqual(stamp,self.output.stat().st_mtime_ns)

    def test_dated_v1_commit_is_accepted_by_publication_verifier(self):
        work,source=commit_identity(self.p.repo,self.sha)
        self.assertEqual('works/field-notes/manuscript.md',work)
        self.assertIn('The first observation.',source)
        self.assertEqual(self.p.fingerprint,signature(self.p.repo,self.sha,fingerprints=[self.p.fingerprint]))

    def test_template_is_minimal(self):
        source=(RESOURCES / 'templates/repository.json').read_bytes();policy(source)
        self.assertEqual({'schema','publisher'},set(json.loads(source)))

    def test_ci_recognizes_a_signed_repository_checkpoint_using_external_public_keys(self):
        from guavajam_core.check_publications import check
        self.p.settings['title']='A reviewed site title'
        checkpoint=self.p.amend_settings()
        public=subprocess.check_output(['gpg','--armor','--export',self.p.fingerprint],env=self.p.env).decode()
        with patch.dict(os.environ,{
            'GUAVAJAM_PUBLISHER_GPG_FINGERPRINTS':self.p.fingerprint,
            'GUAVAJAM_PUBLISHER_GPG_PUBLIC_KEYS':public,
            'GUAVAJAM_PUBLISHER_SSH_SIGNERS':'',
        }):
            self.assertEqual([{'repository_checkpoint':checkpoint}],check(self.p.repo,self.sha,online=False))

    def test_failed_export_does_not_replace_last_good_configuration(self):
        export(self.p.repo,self.output,fingerprints=[self.p.fingerprint])
        original=self.output.read_bytes()
        self.p.settings['editions']={'a'*40:{'published':'2026-11-13'}};self.p.amend_settings()
        with self.assertRaises(ValueError):export(self.p.repo,self.output,fingerprints=[self.p.fingerprint])
        self.assertEqual(original,self.output.read_bytes())


if __name__=='__main__':unittest.main()
