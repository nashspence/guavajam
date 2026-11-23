"""Executable deployment lifecycle: make test-compose; owns only disposable resources."""
from pathlib import Path
import gzip
from html.parser import HTMLParser
import json
import hashlib
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
from guavajam.local_assets import initialize


def run(args,*,env=None):
    result=subprocess.run(args,cwd=ROOT,env=env,text=True,capture_output=True)
    if result.returncode:
        diagnostic=ROOT/'.build/compose-failure.log'
        diagnostic.write_text(result.stdout+result.stderr);diagnostic.chmod(0o600)
        # This harness provisions only synthetic identities and disposable services.
        print((result.stdout+result.stderr)[-32768:],file=sys.stderr,flush=True)
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
    def qualify_search_downloads(self, origin):
        class SearchForm(HTMLParser):
            descriptor = None

            def handle_starttag(parser, tag, attributes):
                values = dict(attributes)
                if tag == 'form' and values.get('id') == 'guavajam-search':
                    parser.descriptor = values

        def receive(relative, headers=None):
            request = urllib.request.Request(origin + '/' + relative, headers=headers or {})
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertEqual('nosniff', response.headers['X-Content-Type-Options'])
                self.assertEqual('no-referrer', response.headers['Referrer-Policy'])
                return response.read(), response.headers, response.status

        html, headers, _ = receive('index.html')
        self.assertEqual('no-cache', headers['Cache-Control'])
        parser = SearchForm()
        parser.feed(html.decode('utf-8'))
        descriptor = parser.descriptor
        self.assertIsNotNone(descriptor)
        artifact = descriptor['data-database']
        self.assertRegex(artifact, r'^index\.[0-9a-f]{64}\.sqlite\.gz$')
        compressed, headers, _ = receive(artifact)
        self.assertEqual('public, max-age=31536000, immutable', headers['Cache-Control'])
        self.assertIsNone(headers.get('Content-Encoding'), 'The precompressed artifact must not be double encoded')
        self.assertEqual(int(descriptor['data-database-transfer-size']), len(compressed))
        self.assertEqual(artifact.split('.')[1], hashlib.sha256(compressed).hexdigest())
        raw, raw_headers, _ = receive('index.sqlite')
        self.assertEqual('no-cache', raw_headers['Cache-Control'])
        self.assertEqual(int(descriptor['data-database-size']), len(raw))
        self.assertEqual(raw, gzip.decompress(compressed))
        part, part_headers, status = receive(artifact, {'Range': 'bytes=0-63'})
        self.assertEqual(206, status)
        self.assertEqual(compressed[:64], part)
        self.assertEqual(headers['Cache-Control'], part_headers['Cache-Control'])
        with self.assertRaises(urllib.error.HTTPError) as unchanged:
            receive(artifact, {'If-None-Match': headers['ETag']})
        self.assertEqual(304, unchanged.exception.code)
        self.assertEqual(headers['Cache-Control'], unchanged.exception.headers['Cache-Control'])
        with self.assertRaises(urllib.error.HTTPError) as missing:
            receive('index.' + '0' * 64 + '.sqlite.gz')
        self.assertEqual(404, missing.exception.code)
        self.assertEqual('no-cache', missing.exception.headers['Cache-Control'])
        for asset in ('search.js', 'sqlite3.mjs', 'sqlite3.wasm'):
            plain, _, _ = receive(asset, {'Accept-Encoding': 'identity'})
            encoded, encoded_headers, _ = receive(asset, {'Accept-Encoding': 'gzip'})
            self.assertEqual('gzip', encoded_headers['Content-Encoding'])
            self.assertIn('Accept-Encoding', encoded_headers['Vary'])
            self.assertEqual('no-cache', encoded_headers['Cache-Control'])
            self.assertEqual(plain, gzip.decompress(encoded))
            self.assertLess(len(encoded), len(plain))
        return artifact

    def test_signed_updates_isolation_media_and_persistent_recreation(self):
        (ROOT/'.build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='compose-',dir=ROOT/'.build') as temporary:
            work=Path(temporary);work.chmod(0o755)
            p=Publisher(work)
            for path in [p.repo,*p.repo.rglob('*')]:
                if not path.is_symlink():path.chmod(0o755 if path.is_dir() else 0o644)
            trust=work/'trust';trust.mkdir();trust.chmod(0o755)
            public=trust/'allowed-signers'
            public.write_text(p.allowed_signers.read_text());public.chmod(0o644)
            source_url='https://source.publisher.test/work.git'
            source_id=hashlib.sha256(source_url.encode()).hexdigest()
            repositories=trust/'repositories.yaml'
            repositories.write_text(json.dumps({source_url:{'allowed_signers':'/trust/allowed-signers'}}))
            repositories.chmod(0o644)
            served=work/'git-http';served.mkdir();served.chmod(0o755)
            bare=served/'work.git'
            p.git('clone','--bare','-q',str(p.repo),str(bare))
            p.git('remote','add','origin',str(bare))
            (served/'index.yaml').write_text(json.dumps({'repositories':[source_url]}))
            def publish_server():
                p.git('push','-q','origin','main')
                run(['git','--git-dir',str(bare),'update-server-info'])
                for path in [served,*served.rglob('*')]:
                    if not path.is_symlink():path.chmod(0o755 if path.is_dir() else 0o644)
            publish_server()
            private=initialize(work/'identities')
            # Synthetic test-only credentials, readable by the non-root image on CI UID mappings.
            for name in ['delivery-access-key','delivery-secret-key','s3-identities.json']:
                target=work/name;target.write_bytes((private/name).read_bytes());target.chmod(0o644)
            env={**os.environ,'GUAVAJAM_INDEX_URL':'https://source.publisher.test/index.yaml',
                 'GUAVAJAM_TRUST_DIR':str(trust),'GUAVAJAM_TRUST_FILE':'/trust/repositories.yaml',
                 'GUAVAJAM_POLL_SECONDS':'5',
                 'GUAVAJAM_PORT':port(),'GUAVAJAM_ASSET_PORT':port(),'GUAVAJAM_S3_PORT':port(),
                 'SEAWEEDFS_IDENTITIES':str(work/'s3-identities.json'),
                 'S3_ACCESS_KEY_ID_FILE':str(work/'delivery-access-key'),'S3_SECRET_ACCESS_KEY_FILE':str(work/'delivery-secret-key'),
                 'S3_ENDPOINT':'https://storage.publisher.test','ASSET_HOST':'assets.publisher.test'}
            cert=work/'tls';cert.mkdir();cert.chmod(0o755)
            run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(cert/'key.pem'),'-out',str(cert/'cert.pem'),'-days','1','-subj','/CN=assets.publisher.test','-addext','subjectAltName=DNS:assets.publisher.test,DNS:storage.publisher.test,DNS:source.publisher.test'])
            (cert/'cert.pem').chmod(0o644)
            config=work/'tls.conf'
            config.write_bytes((ROOT/'tests/deployment/ingress.conf').read_bytes())
            override={'services':{
                'updater':{'environment':{'SSL_CERT_FILE':'/tls/cert.pem','GIT_SSL_CAINFO':'/tls/cert.pem'},'volumes':[str(cert)+':/tls:ro']},
                'source':{'image':'nginx:stable-alpine@sha256:901e593c95dc85f5a729f5765516321366ef4ac401821431d408897e95287d0a','entrypoint':['nginx'],'command':['-g','daemon off;'],'volumes':[str(ROOT/'tests/deployment/source.conf')+':/etc/nginx/nginx.conf:ro',str(cert)+':/tls:ro',str(served)+':/source:ro'],'networks':{'default':{'aliases':['source.publisher.test']}}},
                'assets':{'environment':{'NODE_EXTRA_CA_CERTS':'/tls/cert.pem'},'volumes':[str(cert)+':/tls:ro']},
                'tls':{'profiles':['local-assets'],'image':'nginx:stable-alpine@sha256:901e593c95dc85f5a729f5765516321366ef4ac401821431d408897e95287d0a','entrypoint':['nginx'],'command':['-g','daemon off;'],'volumes':[str(config)+':/etc/nginx/nginx.conf:ro',str(cert)+':/tls:ro'],'networks':{'default':{'aliases':['assets.publisher.test','storage.publisher.test']}}}}}
            override_path=work/'override.json';override_path.write_text(json.dumps(override))
            project='guavajam-check-'+uuid.uuid4().hex[:12]
            compose=['docker','compose','-p',project,'-f',str(ROOT/'some-implementations/a-guavajam-static-website-generator/compose.yaml'),'-f',str(override_path)]
            def dc(*args):return run(compose+list(args),env=env)
            origin='http://127.0.0.1:'+env['GUAVAJAM_PORT']
            def catalog():
                with urllib.request.urlopen(origin+'/sources/'+source_id+'/CATALOG.json',timeout=3) as received:return json.load(received)
            def service_health():
                return json.loads(dc('exec','-T','updater','python','-c','from pathlib import Path;print(Path("/state/health.json").read_text())'))
            def failure_diagnostics():
                # All sources, identities and credentials in this project are disposable test data.
                # The service deliberately exposes only coarse failures, so qualify independently
                # in fresh temporary state to reveal the actual clone/verification exception.
                commands=[
                    ('container logs',['logs','--no-color','--tail','60','source','updater','web','assets','seaweedfs','tls']),
                    ('updater identity and health',['exec','-T','updater','python','-c',
                     'import os,pwd;from pathlib import Path;print(pwd.getpwuid(os.getuid()));print(Path("/state/health.json").read_text())']),
                    ('independent collection qualification',['exec','-T','updater','python','-m','a_guavajam_static_website_generator','index',
                     '--index',env['GUAVAJAM_INDEX_URL'],'--trust','/trust/repositories.yaml',
                     '--output','/tmp/diagnostic-site-'+uuid.uuid4().hex])]
                for label,args in commands:
                    try:
                        result=subprocess.run(compose+args,cwd=ROOT,env=env,text=True,
                                              capture_output=True,timeout=45)
                        output=(result.stdout+result.stderr)[-32768:]
                        print(f'{label} (exit {result.returncode}):\n{output}',file=sys.stderr,flush=True)
                    except (OSError,subprocess.TimeoutExpired) as error:
                        print(f'{label}: {error}',file=sys.stderr,flush=True)
            try:
                dc('up','-d','--no-build','source','updater','web')
                # OpenSSH requires a passwd entry even when verification needs no home files.
                dc('exec','-T','updater','python','-c','import os,pwd;assert pwd.getpwuid(os.getuid()).pw_uid==1000')
                wait_for(lambda:catalog()['editions'][-1]['revision']==p.revisions[-1])
                import xml.etree.ElementTree as ET
                for feed in ('/feed.atom', '/sources/' + source_id + '/feed.atom'):
                    with urllib.request.urlopen(origin + feed, timeout=3) as response:
                        assert response.headers.get_content_type() == 'application/atom+xml'
                        assert ET.fromstring(response.read()).tag == '{http://www.w3.org/2005/Atom}feed'
                first_download = self.qualify_search_downloads(origin)
                assert set(dc('ps','--services','--status','running').splitlines())=={'source','updater','web'}
                selected=dc('exec','-T','updater','readlink','/state/site/current')
                wait_for(lambda:service_health()['status']=='unchanged')
                assert selected==dc('exec','-T','updater','readlink','/state/site/current')
                for path in ['/.last.json','/.cache/','/generations/','/source/.git/config']:
                    try:
                        with urllib.request.urlopen(origin+path,timeout=3) as received:assert received.status!=200
                    except urllib.error.HTTPError as error:assert error.code in {403,404}
                p.source('Field Notes','An updated observation.');p.publish('Publish updated observation');publish_server()
                for path in [p.repo,*p.repo.rglob('*')]:
                    if not path.is_symlink():path.chmod(0o755 if path.is_dir() else 0o644)
                wait_for(lambda:catalog()['editions'][-1]['revision']==p.revisions[-1])
                self.assertNotEqual(first_download, self.qualify_search_downloads(origin))
                selected=dc('exec','-T','updater','readlink','/state/site/current')
                saved=public.read_text();public.write_text('invalid test signer policy')
                wait_for(lambda:service_health()['passed'] is False)
                assert selected==dc('exec','-T','updater','readlink','/state/site/current')
                assert catalog()['editions'][-1]['revision']==p.revisions[-1]
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
                wait_for(lambda:catalog()['editions'][-1]['revision']==p.revisions[-1])
                assert selected==dc('exec','-T','updater','readlink','/state/site/current')
                wait_for(lambda:subprocess.run(compose+['exec','-T','updater','python','-c',"import socket;socket.create_connection(('seaweedfs',8333),2).close();socket.create_connection(('tls',443),2).close()"],env=env,capture_output=True).returncode==0)
                asset_check('--persistence','verify')
                print(json.dumps({'down_and_recreate_preserves_selected_generation_and_objects':True}),flush=True)
            except Exception:
                failure_diagnostics()
                raise
            finally:
                dc('--profile','local-assets','down','--volumes','--remove-orphans')
                assert not run(['docker','ps','-aq','--filter','label=com.docker.compose.project='+project])
                assert not run(['docker','volume','ls','-q','--filter','label=com.docker.compose.project='+project])
                print(json.dumps({'owned_compose_containers_and_volumes_removed':True}),flush=True)


if __name__=='__main__':unittest.main(verbosity=2)
