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


class PrivateComposeTests(unittest.TestCase):
    def test_local_workspace_is_private_and_updates_without_publication(self):
        with tempfile.TemporaryDirectory(prefix='private-compose-',dir=ROOT/'.build') as directory:
            work=Path(directory);work.chmod(0o755)
            p=Publisher(work);p.git('switch','-c','draft')
            p.source('field-notes','Field Notes','An initial private observation.')
            p.git('add','-A');p.git('commit','-qm','Initial private draft')
            trust=work/'trust';trust.mkdir();trust.chmod(0o755)
            public=trust/'publisher.asc';public.write_text(run(['gpg','--batch','--armor','--export',p.fingerprint],env=p.env));public.chmod(0o644)
            state=work/'state';state.mkdir()
            token='e'*64
            key=work/'session.key';key.write_text(token);key.chmod(0o600)
            readonly=work/'readonly.env';readonly.write_text('SYNTHETIC_UNUSED=1\n');readonly.chmod(0o600)
            selected=port()
            env={**os.environ,'GUAVAJAM_PREVIEW_SOURCE':str(p.repo),'GUAVAJAM_PREVIEW_WORK':'field-notes',
                 'GUAVAJAM_PREVIEW_ORIGIN':'http://127.0.0.1:'+selected,'GUAVAJAM_PREVIEW_TOKEN_FILE':str(key),
                 'GUAVAJAM_PREVIEW_READONLY_ENV':str(readonly),'GUAVAJAM_PREVIEW_PORT':selected,'GUAVAJAM_POLL_SECONDS':'5',
                 'GUAVAJAM_PREVIEW_PUBLISHED':'2026-11-15','GUAVAJAM_TRUST_DIR':str(trust),
                 'GUAVAJAM_SSH_ALLOWED_SIGNERS':'','GUAVAJAM_TRUSTED_GPG_FINGERPRINTS':p.fingerprint,
                 'GUAVAJAM_GPG_PUBLIC_KEYS':'/trust/publisher.asc'}
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
                with get('/works/field-notes/index.html') as response:return response.read()
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


if __name__=='__main__':unittest.main(verbosity=2)
