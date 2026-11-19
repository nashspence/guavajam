"""Real private-preview image startup and local commit refresh; no live provider."""
from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
import uuid
from test_compose import ROOT, Publisher, port, run, wait_for
from guavajam_test_support.discovery_fixture import Draft


class PrivateComposeTests(unittest.TestCase):
    def test_local_workspace_is_private_and_updates_without_publication(self):
        with tempfile.TemporaryDirectory(prefix='private-compose-',dir=ROOT/'.build') as directory:
            work=Path(directory);work.chmod(0o755)
            p=Publisher(work);p.publish('2026-11-01');p.publish('2026-11-02');p.git('switch','-c','draft')
            p.source('field-notes','Field Notes','An initial private observation.')
            p.git('add','-A');p.git('commit','-qm','Initial private draft')
            inputs=work/'inputs';inputs.mkdir();inputs.chmod(0o700)
            public=inputs/'publisher.asc';public.write_text(run(['gpg','--batch','--armor','--export',p.fingerprint],env=p.env));public.chmod(0o644)
            state=work/'state';state.mkdir()
            token='e'*64
            key=inputs/'session.key';key.write_text(token);key.chmod(0o600)
            readonly=inputs/'readonly.env';readonly.write_text('SYNTHETIC_UNUSED=1\n');readonly.chmod(0o600)
            selected=port()
            env={**os.environ,'GUAVAJAM_PREVIEW_SOURCE':str(p.repo),'GUAVAJAM_PREVIEW_WORK':'field-notes',
                 'GUAVAJAM_PREVIEW_ORIGIN':'http://127.0.0.1:'+selected,
                 'GUAVAJAM_PREVIEW_INPUTS':str(inputs),'GUAVAJAM_PREVIEW_PORT':selected,'GUAVAJAM_POLL_SECONDS':'5',
                 'GUAVAJAM_SSH_ALLOWED_SIGNERS':'','GUAVAJAM_TRUSTED_GPG_FINGERPRINTS':p.fingerprint,
                 'GUAVAJAM_GPG_PUBLIC_KEYS':'/run/preview/publisher.asc'}
            override=work/'override.json'
            override.write_text(json.dumps({'services':{'private-preview':{'user':str(os.getuid())+':'+str(os.getgid()),
                'volumes':[str(state)+':/state']}}}))
            project='guavajam-private-'+uuid.uuid4().hex[:12]
            compose=['docker','compose','-p',project,'-f',str(ROOT/'compose.yaml'),'-f',str(override)]
            def dc(*args):return run(compose+list(args),env=env)
            def get(path='/',auth=True):
                headers={'Host':'127.0.0.1:'+selected}
                if auth:headers['Cookie']='guavajam_preview='+token
                return urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:'+selected+path,headers=headers),timeout=3)
            def content():
                with get('/CATALOG.json') as response:
                    catalog=json.load(response)
                revision=catalog['works'][0]['recommended_revision']
                with get('/works/field-notes/editions/'+revision+'/read/index.html') as response:
                    return response.read()
            try:
                dc('up','-d','--no-build','private-preview')
                wait_for(lambda:b'PRIVATE PROPOSED EDITION' in content())
                self.assertEqual({'private-preview'},set(dc('ps','--services','--status','running').splitlines()))
                for path in ['/','/.git/config','/PREVIEW.json','/_assets/sha256/'+'a'*64+'.png']:
                    with self.subTest(path=path),self.assertRaises(urllib.error.HTTPError) as error:get(path,False)
                    self.assertEqual(404,error.exception.code)
                p.source('field-notes','Field Notes','An updated private local observation.')
                p.git('add','-A');p.git('commit','-qm','Revise private local draft')
                wait_for(lambda:b'updated private local' in content())
                dc('exec','-T','private-preview','python','-m','guavajam.preview_service','--health')
                self.assertEqual('draft',p.git('branch','--show-current'))
                print(json.dumps({'private_preview_image_session_local_refresh_and_health':True}),flush=True)
            finally:
                dc('--profile','private-preview','down','--volumes','--remove-orphans')
                self.assertFalse(run(['docker','ps','-aq','--filter','label=com.docker.compose.project='+project]))
                self.assertFalse(run(['docker','volume','ls','-q','--filter','label=com.docker.compose.project='+project]))

    def test_first_preview_has_no_main_trust_directory_or_asset_credential_file(self):
        with tempfile.TemporaryDirectory(prefix='bootstrap-compose-',dir=ROOT/'.build') as directory:
            work = Path(directory); work.chmod(0o755)
            draft = Draft(work)
            inputs = work/'inputs'; inputs.mkdir()
            token = 'f'*64
            key = inputs/'session.key'; key.write_text(token); key.chmod(0o600)
            state = work/'state'; state.mkdir()
            selected = port()
            env = {**os.environ, 'GUAVAJAM_PREVIEW_SOURCE':str(draft.repo),
                   'GUAVAJAM_PREVIEW_WORK':'field-notes', 'GUAVAJAM_PREVIEW_INPUTS':str(inputs),
                   'GUAVAJAM_PREVIEW_ORIGIN':'http://127.0.0.1:'+selected,
                   'GUAVAJAM_PREVIEW_PORT':selected, 'GUAVAJAM_POLL_SECONDS':'5',
                   'GUAVAJAM_PREVIEW_BASE_REF':'',
                   'GUAVAJAM_SSH_ALLOWED_SIGNERS':'', 'GUAVAJAM_TRUSTED_GPG_FINGERPRINTS':'',
                   'GUAVAJAM_GPG_PUBLIC_KEYS':''}
            override = work/'override.json'
            override.write_text(json.dumps({'services':{'private-preview':{
                'user':str(os.getuid())+':'+str(os.getgid()), 'volumes':[str(state)+':/state']}}}))
            project = 'guavajam-bootstrap-'+uuid.uuid4().hex[:12]
            compose = ['docker','compose','-p',project,'-f',str(ROOT/'compose.yaml'),'-f',str(override)]
            def dc(*args): return run(compose+list(args),env=env)
            def get(auth=True):
                headers = {'Cookie':'guavajam_preview='+token} if auth else {}
                return urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:'+selected+'/',headers=headers),timeout=3)
            def ready():
                with get() as response: return b'publisher settings are provisional' in response.read()
            try:
                dc('up','-d','--no-build','private-preview')
                wait_for(ready)
                with self.assertRaises(urllib.error.HTTPError) as error: get(False)
                self.assertEqual(404,error.exception.code)
                first = draft.git('rev-parse','HEAD')
                draft.source('field-notes','First Local Revision','A reviewed revision without signing.')
                draft.git('add','-A');draft.git('commit','-qm','Revise unsigned first work')
                def revised():
                    with get() as response: return b'First Local Revision' in response.read()
                wait_for(revised)
                dc('exec','-T','private-preview','python','-m','guavajam.preview_service','--health')
                self.assertNotEqual(first,draft.git('rev-parse','HEAD'))
                self.assertEqual('',draft.git('remote'))
                self.assertEqual({'session.key'},{p.name for p in inputs.iterdir()})
                self.assertFalse((state/'gnupg').exists())
                self.assertFalse((state/'baseline.json').exists())
                print(json.dumps({'first_unsigned_compose_preview_refresh_no_trust_or_asset_credentials':True}),flush=True)
            finally:
                dc('--profile','private-preview','down','--volumes','--remove-orphans')
                self.assertFalse(run(['docker','ps','-aq','--filter','label=com.docker.compose.project='+project]))
                self.assertFalse(run(['docker','volume','ls','-q','--filter','label=com.docker.compose.project='+project]))


if __name__=='__main__':unittest.main(verbosity=2)
