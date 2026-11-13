"""Raw Git source, external publisher trust and required media verification."""
import os,sys,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from guavajam_test_support import FIXTURES, RESOURCES
sys.path.insert(0, str(Path(__file__).parent))
from test_signed_commit_publication import PublicationCommitTests,cmd,MANUSCRIPT
from guavajam_core.publication_commit import PublicationError,citation
from guavajam_core.check_publications import check
from guavajam_core.verify_source_assets import verify_source_assets

class SourceProofTests(PublicationCommitTests):
    def commit_source(self):
        cmd(self.repo,'git','add','-A');cmd(self.repo,'git','commit','-qm','Review source change');self._sign(self.signed_message)
    def test_archive_export_attributes_cannot_hide_native_private_input(self):
        (self.repo/'private.dat').write_bytes((FIXTURES/'capture/conversation.json').read_bytes())
        (self.repo/'.gitattributes').write_text('private.dat export-ignore\n')
        self.commit_source()
        with self.assertRaisesRegex(PublicationError,'native conversation'):self._verify()
    def test_citation_cannot_target_an_anchor_lookalike_inside_literal_code(self):
        source=self.repo/'works/recorded-notes/manuscript.md'
        with source.open('a') as stream:stream.write('\n```html\n<a id="phantom"></a>\n```\n')
        self.commit_source();record=self._verify()
        with self.assertRaisesRegex(PublicationError,'existing stable'):citation(record,'phantom')
    def test_signed_publication_cannot_change_the_reviewed_content_tree(self):
        source=self.repo/'works/recorded-notes/manuscript.md';source.write_text(source.read_text().replace('quiet field','changed field'))
        cmd(self.repo,'git','-c','commit.gpgSign=true','commit','-am','Publish changed content','-m',self.signed_message,env=self.env)
        self.signed=cmd(self.repo,'git','rev-parse','HEAD')
        with self.assertRaisesRegex(PublicationError,'empty, single-parent'):self._verify()
    def test_external_policy_is_required_and_real_gpg_public_policy_verifies_incoming_commits(self):
        with patch.dict(os.environ,{'PRESS_PUBLISHER_GPG_FINGERPRINTS':'','PRESS_PUBLISHER_SSH_SIGNERS':''}):
            with self.assertRaisesRegex(PublicationError,'externally configured'):check(self.repo,self.draft,online=False)
        public=cmd(self.repo,'gpg','--armor','--export',self.fingerprint,env=self.env)
        with patch.dict(os.environ,{'PRESS_PUBLISHER_GPG_FINGERPRINTS':self.fingerprint,'PRESS_PUBLISHER_GPG_PUBLIC_KEYS':public,'PRESS_PUBLISHER_SSH_SIGNERS':''}):
            self.assertEqual([self.signed],[r['revision'] for r in check(self.repo,self.draft,online=False)])
    def test_every_source_asset_check_verifies_media_declarations(self):
        verifier=Mock(return_value={})
        with patch('guavajam_core.verify_source_assets.git_sources',return_value=[Path('works/recorded-notes/manuscript.md')]):
            self.assertEqual(1,verify_source_assets(self.repo,media_verifier=verifier))
        verifier.assert_called_once();self.assertEqual('audio',verifier.call_args.args[0][0]['kind'])

    def test_repository_cannot_select_a_signature_verification_program(self):
        marker=Path(self.temporary.name)/'executed'
        program=self.repo/'fake-verifier';program.write_text('#!/bin/sh\ntouch '+str(marker)+'\nexit 1\n');program.chmod(0o700)
        cmd(self.repo,'git','config','gpg.openpgp.program',str(program));cmd(self.repo,'git','config','gpg.program',str(program))
        self.assertEqual(self.signed,self._verify()['revision']);self.assertFalse(marker.exists())
    def test_supporting_citation_uses_actual_document_path_and_explicit_source_anchor(self):
        doc=self.repo/'works/recorded-notes/supporting/note.md';doc.parent.mkdir();doc.write_text('<a name="document"></a>\n\n# Reviewed note\n\n<a name="claim"></a>\n\nA reviewed claim.\n')
        self.commit_source();record=self._verify();result=citation(record,'claim','works/recorded-notes/supporting/note.md')
        self.assertEqual('works/recorded-notes/supporting/note.md',result['document'])
        with self.assertRaises(PublicationError):citation(record,'missing',result['document'])

    def test_owner_preparation_reads_actual_private_retention_and_same_public_version(self):
        from guavajam_core.prepare_publication import prepare
        from test_asset_versions import Store,KEY
        from guavajam_core.gateway_index import Index
        from guavajam_core.asset_versions import pin_key
        from guavajam_core.asset_policy import AssetError
        import contextlib,io,re,time
        store=Store();until=int(time.time()*1000)+100000
        self.enterContext(patch('guavajam_core.gateway_index.response',side_effect=store.read));self.enterContext(patch('guavajam_core.asset_versions.response',side_effect=store.read))
        pin_key(store,Index(store),KEY,retain_until=until,mode='governance')
        source=self.repo/'works/recorded-notes/manuscript.md'
        source.write_text(re.sub(r'```press-media\n.*?\n```','![A circle](https://assets.example.org/'+KEY+')',source.read_text(),flags=re.S))
        self.commit_source()
        @contextlib.contextmanager
        def public(*args,**kwargs):
            response=Mock(status=200);response.getheader.return_value='version_0';yield response
        with patch('guavajam_core.prepare_publication.verify_source_assets') as full,patch('guavajam_core.asset_policy.public_response',side_effect=public),patch('guavajam_core.prepare_publication.public_response',side_effect=public):
            result=prepare(self.repo,self.signed,'recorded-notes',store=store)
            self.assertTrue(result['retention_verified']);self.assertEqual(until,result['assets'][0]['retain_until'])
            full.assert_called_once();self.assertTrue(full.call_args.kwargs['require_retention'])
            store.info('version_0')['fileRetention']['isClientAuthorizedToRead']=False
            with self.assertRaisesRegex(AssetError,'private B2'):prepare(self.repo,self.signed,'recorded-notes',store=store)

def load_tests(loader,tests,pattern):return unittest.TestSuite(loader.loadTestsFromName(n,SourceProofTests) for n in SourceProofTests.__dict__ if n.startswith('test_'))
