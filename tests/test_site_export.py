"""The publisher emits exactly the consumer catalog, without renderer setup.

The source-owned schema is schemas/press-site-v1.schema.json; strict executable
validation is in publication_protocol.py and site_catalog.py. This test uses
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

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from export_site import export
from import_site import convert
from publication_protocol import discover, policy
from publication_commit import commit_identity,signature
from site_catalog import encoded
from discovery_fixture import Publisher


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

    def test_dated_v2_commit_is_accepted_by_existing_publication_verifier(self):
        work,source=commit_identity(self.p.repo,self.sha)
        self.assertEqual('works/field-notes/manuscript.md',work)
        self.assertIn('The first observation.',source)
        self.assertEqual(self.p.fingerprint,signature(self.p.repo,self.sha,fingerprints=[self.p.fingerprint]))

    def test_template_is_minimal_and_import_preserves_existing_administrative_decisions(self):
        source=(ROOT/'templates/site.json').read_bytes();policy(source)
        self.assertEqual({'schema','publisher','base_url'},set(json.loads(source)))
        original,_=discover(self.p.repo,fingerprints=[self.p.fingerprint])
        work=original['works'][0];work.pop('recommended_revision')
        work['concept_doi']='10.1234/work';work['editions'][0]['doi']='10.1234/edition'
        settings=convert(original)
        self.p.settings=settings;self.p.amend_settings()
        migrated,_=discover(self.p.repo,fingerprints=[self.p.fingerprint],previous=original)
        self.assertEqual(original,migrated)

    def test_ci_recognizes_a_signed_site_checkpoint_using_external_public_keys(self):
        from check_publications import check
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
