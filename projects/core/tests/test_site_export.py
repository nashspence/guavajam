"""The publisher emits exactly the consumer catalog, without renderer setup.

The source-owned schema is schemas/press-site-v1.schema.json; strict executable
validation is in publication_protocol.py and export_site.py. This test uses
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
from guavajam_core.export_site import export
from guavajam_core.publication_protocol import discover, policy
from guavajam_core.publication_commit import commit_identity,signature
from guavajam_core.catalog import encoded
from guavajam_test_support.discovery_fixture import Publisher


class SiteExportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.p=Publisher(self.temp.name);self.sha=self.p.publish(label='A signed edition')
        self.output=Path(self.temp.name)/'catalog.json'
        environment=patch.dict(os.environ,{'GNUPGHOME':str(self.p.home)})
        environment.start();self.addCleanup(environment.stop)

    def test_real_make_target_emits_exact_catalog_and_noop_keeps_the_file(self):
        args=['make','site-export','PYTHON='+sys.executable,'REPO='+str(self.p.repo),
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
        source=(RESOURCES / 'templates/site.json').read_bytes();policy(source)
        self.assertEqual({'schema','publisher','base_url'},set(json.loads(source)))

    def test_ci_recognizes_a_signed_site_checkpoint_using_external_public_keys(self):
        from guavajam_core.check_publications import check
        self.p.settings['title']='A reviewed site title'
        checkpoint=self.p.amend_settings()
        public=subprocess.check_output(['gpg','--armor','--export',self.p.fingerprint],env=self.p.env).decode()
        with patch.dict(os.environ,{
            'PRESS_PUBLISHER_GPG_FINGERPRINTS':self.p.fingerprint,
            'PRESS_PUBLISHER_GPG_PUBLIC_KEYS':public,
            'PRESS_PUBLISHER_SSH_SIGNERS':'',
        }):
            self.assertEqual([{'site_checkpoint':checkpoint}],check(self.p.repo,self.sha,online=False))

    def test_failed_export_does_not_replace_last_good_configuration(self):
        export(self.p.repo,self.output,fingerprints=[self.p.fingerprint])
        original=self.output.read_bytes()
        self.p.settings['editions']={'a'*40:{'published':'2026-11-13'}};self.p.amend_settings()
        with self.assertRaises(ValueError):export(self.p.repo,self.output,fingerprints=[self.p.fingerprint])
        self.assertEqual(original,self.output.read_bytes())


if __name__=='__main__':unittest.main()
