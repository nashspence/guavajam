"""Executable deployment lifecycle: make test-compose; owns only disposable resources."""
from pathlib import Path
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tests/support'))
from guavajam_test_support.discovery_fixture import Publisher
from guavajam_core.local_assets import initialize


def run(args,*,env=None):
    result=subprocess.run(args,cwd=ROOT,env=env,text=True,capture_output=True)
    if result.returncode:
        diagnostic=ROOT/'.build/compose-failure.log'
        diagnostic.write_text(result.stdout+result.stderr);diagnostic.chmod(0o600)
        raise RuntimeError('qualification command failed; diagnostic in .build/compose-failure.log')
    return result.stdout.strip()


def port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));return str(listener.getsockname()[1])


def wait_for(predicate):
    deadline=time.monotonic()+150
    while time.monotonic()<deadline:
        try:
            if predicate():return
        except (OSError,ValueError,KeyError,RuntimeError):pass
        time.sleep(1)
    raise RuntimeError('qualification readiness deadline exceeded')


class ComposeLifecycleTests(unittest.TestCase):
    def test_signed_updates_isolation_media_and_persistent_recreation(self):
        (ROOT/'.build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='compose-',dir=ROOT/'.build') as temporary:
            work=Path(temporary);work.chmod(0o755)
            p=Publisher(work);p.publish()
            for path in [p.repo,*p.repo.rglob('*')]:
                if not path.is_symlink():path.chmod(0o755 if path.is_dir() else 0o644)
            trust=work/'trust';trust.mkdir();trust.chmod(0o755)
            public=trust/'publisher.asc'
            public.write_text(run(['gpg','--batch','--armor','--export',p.fingerprint],env=p.env));public.chmod(0o644)
            gitconfig=trust/'gitconfig';gitconfig.write_text('[safe]\n\tdirectory = /fixture-source\n\tdirectory = /fixture-source/.git\n');gitconfig.chmod(0o644)
            private=initialize(work/'identities')
            # Synthetic test-only credentials, readable by the non-root image on CI UID mappings.
            for name in ['delivery-access-key','delivery-secret-key','s3-identities.json']:
                target=work/name;target.write_bytes((private/name).read_bytes());target.chmod(0o644)
            env={**os.environ,'GUAVAJAM_SOURCE_URL':'file:///fixture-source','GUAVAJAM_TRUST_DIR':str(trust),
                 'GUAVAJAM_SSH_ALLOWED_SIGNERS':'','GUAVAJAM_TRUSTED_GPG_FINGERPRINTS':p.fingerprint,
                 'GUAVAJAM_GPG_PUBLIC_KEYS':'/trust/publisher.asc','GUAVAJAM_POLL_SECONDS':'5',
                 'GUAVAJAM_PORT':port(),'GUAVAJAM_ASSET_PORT':port(),'GUAVAJAM_S3_PORT':port(),
                 'SEAWEEDFS_IDENTITIES':str(work/'s3-identities.json'),
                 'S3_ACCESS_KEY_ID_FILE':str(work/'delivery-access-key'),'S3_SECRET_ACCESS_KEY_FILE':str(work/'delivery-secret-key'),
                 'S3_ENDPOINT':'https://storage.publisher.test','ASSET_HOST':'assets.publisher.test'}
            cert=work/'tls';cert.mkdir();cert.chmod(0o755)
            run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(cert/'key.pem'),'-out',str(cert/'cert.pem'),'-days','1','-subj','/CN=assets.publisher.test','-addext','subjectAltName=DNS:assets.publisher.test,DNS:storage.publisher.test'])
            (cert/'cert.pem').chmod(0o644)
            config=work/'tls.conf'
            config.write_bytes((ROOT/'tests/deployment/ingress.conf').read_bytes())
            override={'services':{
                'updater':{'environment':{'GIT_CONFIG_GLOBAL':'/trust/gitconfig'},'volumes':[str(p.repo)+':/fixture-source:ro']},
                'assets':{'environment':{'NODE_EXTRA_CA_CERTS':'/tls/cert.pem'},'volumes':[str(cert)+':/tls:ro']},
                'tls':{'profiles':['local-assets'],'image':'nginx:stable-alpine@sha256:901e593c95dc85f5a729f5765516321366ef4ac401821431d408897e95287d0a','entrypoint':['nginx'],'command':['-g','daemon off;'],'volumes':[str(config)+':/etc/nginx/nginx.conf:ro',str(cert)+':/tls:ro'],'networks':{'default':{'aliases':['assets.publisher.test','storage.publisher.test']}}}}}
            override_path=work/'override.json';override_path.write_text(json.dumps(override))
            project='guavajam-check-'+uuid.uuid4().hex[:12]
            compose=['docker','compose','-p',project,'-f',str(ROOT/'compose.yaml'),'-f',str(override_path)]
            def dc(*args):return run(compose+list(args),env=env)
            origin='http://127.0.0.1:'+env['GUAVAJAM_PORT']
            def catalog():
                with urllib.request.urlopen(origin+'/CATALOG.json',timeout=3) as received:return json.load(received)
            def service_health():
                return json.loads(dc('exec','-T','updater','python','-c','from pathlib import Path;print(Path("/state/health.json").read_text())'))
            try:
                dc('up','-d','--no-build','updater','web')
                wait_for(lambda:catalog()['works'][0]['recommended_revision']==p.revisions[-1])
                assert set(dc('ps','--services','--status','running').splitlines())=={'updater','web'}
                selected=dc('exec','-T','updater','readlink','/state/site/current')
                wait_for(lambda:service_health()['status']=='unchanged')
                assert selected==dc('exec','-T','updater','readlink','/state/site/current')
                for path in ['/.last.json','/.cache/','/generations/','/source/.git/config']:
                    try:
                        with urllib.request.urlopen(origin+path,timeout=3) as received:assert received.status!=200
                    except urllib.error.HTTPError as error:assert error.code in {403,404}
                p.source('field-notes','Field Notes','An updated observation.');p.git('add','-A');p.git('commit','-qm','Review updated source');p.publish('2026-11-15')
                for path in [p.repo,*p.repo.rglob('*')]:
                    if not path.is_symlink():path.chmod(0o755 if path.is_dir() else 0o644)
                wait_for(lambda:catalog()['works'][0]['recommended_revision']==p.revisions[-1])
                selected=dc('exec','-T','updater','readlink','/state/site/current')
                saved=public.read_text();public.write_text('invalid test public key')
                wait_for(lambda:service_health()['passed'] is False)
                assert selected==dc('exec','-T','updater','readlink','/state/site/current')
                assert catalog()['works'][0]['recommended_revision']==p.revisions[-1]
                public.write_text(saved)
                dc('restart','updater','web')
                wait_for(lambda:service_health()['passed'] is True)
                assert selected==dc('exec','-T','updater','readlink','/state/site/current')
                print(json.dumps({'default_compose_signed_updates_noop_restart_and_isolation':True}),flush=True)
                dc('--profile','local-assets','up','-d','--no-build')
                subnet=run(['docker','network','inspect',project+'_default','--format','{{(index .IPAM.Config 0).Subnet}}'])
                policy=work/'network.json';policy.write_text(json.dumps({'origin':'https://assets.publisher.test','networks':[subnet]}));policy.chmod(0o644)
                wait_for(lambda:'ok' in urllib.request.urlopen('http://127.0.0.1:'+env['GUAVAJAM_ASSET_PORT']+'/healthz',timeout=3).read().decode())
                # Wait for TLS and storage, using only a fixed disposable namespace.
                wait_for(lambda:subprocess.run(compose+['exec','-T','updater','python','-c',"import socket;socket.create_connection(('seaweedfs',8333),2).close();socket.create_connection(('tls',443),2).close()"],env=env,capture_output=True).returncode==0)
                def asset_check(*args):
                    output=dc('run','--rm','--no-deps','--entrypoint','python',
                        '-e','SSL_CERT_FILE=/tls/cert.pem','-e','GUAVAJAM_ASSET_NETWORK_POLICY=/qualification/network.json',
                        '-e','PYTHONPATH=/qualification/support',
                        '-v',str(ROOT/'tests/support')+':/qualification/support:ro',
                        '-v',str(work/'s3-identities.json')+':/qualification/identities.json:ro',
                        '-v',str(policy)+':/qualification/network.json:ro',
                        '-v',str(cert)+':/tls:ro','updater','-m','guavajam_test_support.compose_assets',*args)
                    for line in output.splitlines():
                        value=json.loads(line);print(json.dumps(value),flush=True)
                asset_check()
                asset_check('--persistence','write')
                # A normal down removes containers/network, retaining publication and object state.
                dc('--profile','local-assets','down')
                assert not run(['docker','ps','-aq','--filter','label=com.docker.compose.project='+project])
                retained=run(['docker','volume','ls','-q','--filter','label=com.docker.compose.project='+project])
                assert set(retained.splitlines())=={project+'_state',project+'_objects'}
                dc('--profile','local-assets','up','-d','--no-build')
                wait_for(lambda:service_health()['passed'] is True)
                wait_for(lambda:catalog()['works'][0]['recommended_revision']==p.revisions[-1])
                assert selected==dc('exec','-T','updater','readlink','/state/site/current')
                wait_for(lambda:subprocess.run(compose+['exec','-T','updater','python','-c',"import socket;socket.create_connection(('seaweedfs',8333),2).close();socket.create_connection(('tls',443),2).close()"],env=env,capture_output=True).returncode==0)
                asset_check('--persistence','verify')
                print(json.dumps({'down_and_recreate_preserves_selected_generation_and_objects':True}),flush=True)
            finally:
                dc('--profile','local-assets','down','--volumes','--remove-orphans')
                assert not run(['docker','ps','-aq','--filter','label=com.docker.compose.project='+project])
                assert not run(['docker','volume','ls','-q','--filter','label=com.docker.compose.project='+project])
                print(json.dumps({'owned_compose_containers_and_volumes_removed':True}),flush=True)


if __name__=='__main__':unittest.main(verbosity=2)
