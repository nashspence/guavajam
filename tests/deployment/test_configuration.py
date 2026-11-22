"""Deployment configuration contracts; run without Docker via make test-deployment-config."""
from pathlib import Path
import json
import re
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / 'some-implementations/a-guavajam-static-website-generator'


class DeploymentConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = yaml.safe_load((APP / 'compose.yaml').read_text())
        cls.services = cls.compose['services']
        cls.dockerfile = (APP / 'Dockerfile').read_text()
        cls.stages = {
            name: body for name, body in re.findall(
                r'^FROM \S+ AS ([\w-]+)\n(.*?)(?=^FROM |\Z)', cls.dockerfile, re.M | re.S)
        }

    def test_hosted_preview_has_read_only_source_and_only_reader_credentials(self):
        preview = self.services['private-preview']
        self.assertEqual(['private-preview'], preview['profiles'])
        self.assertTrue(preview['read_only'])
        self.assertEqual(['ALL'], preview['cap_drop'])
        self.assertEqual({'context': '../..', 'dockerfile': 'some-implementations/a-guavajam-static-website-generator/Dockerfile', 'target': 'preview'}, preview['build'])
        mounts = [m for m in preview['volumes'] if isinstance(m, dict)]
        self.assertEqual({'/source', '/run/preview'}, {m['target'] for m in mounts})
        self.assertTrue(all(m['read_only'] and not m['bind']['create_host_path'] for m in mounts))
        self.assertEqual('/run/preview/readonly.env', preview['environment']['S3_ENV'])
        self.assertNotIn('GUAVAJAM_PREVIEW_WORK', preview['environment'])
        self.assertNotIn('GUAVAJAM_SSH_ALLOWED_SIGNERS', preview['environment'])
        self.assertEqual('/run/preview/session.key', preview['environment']['GUAVAJAM_PREVIEW_TOKEN_FILE'])
        self.assertEqual(['${GUAVAJAM_BIND_ADDRESS:-127.0.0.1}:${GUAVAJAM_PREVIEW_PORT:-8082}:8080'], preview['ports'])
        self.assertFalse(preview.get('depends_on'))
        self.assertIn('COPY --from=node /usr/local/bin/node /usr/local/bin/node', self.stages['preview'])

    def test_generation_origin_is_optional_for_public_and_private_views(self):
        for name in ['updater', 'private-preview']:
            self.assertEqual('${GUAVAJAM_SITE_ORIGIN:-}', self.services[name]['environment']['GUAVAJAM_SITE_ORIGIN'])

    def test_default_view_and_optional_local_storage_have_separate_images(self):
        self.assertEqual({'updater', 'web'}, {
            name for name, service in self.services.items() if not service.get('profiles')
        })
        self.assertEqual({'assets', 'seaweedfs'}, {
            name for name, service in self.services.items() if service.get('profiles') == ['local-assets']
        })
        for name, target in [('updater', 'a-guavajam-static-website-generator'), ('web', 'web'), ('assets', 'gateway')]:
            with self.subTest(service=name):
                self.assertEqual({'context': '../..', 'dockerfile': 'some-implementations/a-guavajam-static-website-generator/Dockerfile', 'target': target}, self.services[name]['build'])
        # The gateway can target an existing S3 service without starting local storage.
        self.assertFalse(self.services['assets'].get('depends_on'))

    def test_application_containers_are_unprivileged_with_read_only_roots(self):
        for name, stage, user in [('updater', 'a-guavajam-static-website-generator', '1000:1000'),
                                  ('assets', 'gateway', '1000:1000'), ('web', 'web', '101:101')]:
            with self.subTest(service=name):
                service = self.services[name]
                self.assertTrue(service['read_only'])
                self.assertEqual(['ALL'], service['cap_drop'])
                self.assertIn('no-new-privileges:true', service['security_opt'])
                self.assertRegex(self.stages[stage], rf'(?m)^USER {user}$')
                self.assertFalse(service.get('privileged'))
                self.assertNotIn(service.get('network_mode'), {'host'})
        for service in self.services.values():
            for mount in service.get('volumes', []):
                self.assertNotIn('docker.sock', str(mount))

    def test_public_view_mounts_state_read_only_and_receives_no_operator_secrets(self):
        self.assertEqual(['state:/srv/guavajam:ro'], self.services['web']['volumes'])
        self.assertFalse(self.services['web'].get('environment'))
        updater = self.services['updater']
        self.assertIn('state:/state', updater['volumes'])
        self.assertFalse(any(key.startswith('S3_') for key in updater['environment']))
        self.assertEqual({'state', 'objects', 'preview-state'}, set(self.compose['volumes']))
        self.assertIn('objects:/data', self.services['seaweedfs']['volumes'])
        self.assertNotIn('objects:/data', updater['volumes'])

    def test_independent_trust_and_delivery_credentials_are_existing_read_only_binds(self):
        expected = {'updater': {'/trust'},
                    'assets': {'/run/secrets/delivery-access-key', '/run/secrets/delivery-secret-key'},
                    'seaweedfs': {'/run/secrets/s3-identities.json'}}
        for name, targets in expected.items():
            mounts = [mount for mount in self.services[name]['volumes'] if isinstance(mount, dict)]
            self.assertEqual(targets, {mount['target'] for mount in mounts})
            for mount in mounts:
                with self.subTest(service=name, target=mount['target']):
                    self.assertEqual('bind', mount['type'])
                    self.assertTrue(mount['read_only'])
                    self.assertFalse(mount['bind']['create_host_path'])
        self.assertEqual(
            'ssh -F /trust/ssh_config -o BatchMode=yes -o StrictHostKeyChecking=yes',
            self.services['updater']['environment']['GIT_SSH_COMMAND'])
        self.assertIn('-s3.config=/run/secrets/s3-identities.json', self.services['seaweedfs']['command'])

    def test_index_lists_locations_and_trust_is_operator_owned(self):
        updater = self.services['updater']['environment']
        self.assertEqual('${GUAVAJAM_INDEX_URL:-}', updater['GUAVAJAM_INDEX_URL'])
        self.assertEqual('${GUAVAJAM_TRUST_FILE:-/trust/repositories.yaml}', updater['GUAVAJAM_TRUST_FILE'])
        self.assertNotIn('GUAVAJAM_SOURCE_URL', updater)
        index = yaml.safe_load((APP / 'deploy/index.example.yaml').read_text())
        trust = yaml.safe_load((APP / 'deploy/trust.example.yaml').read_text())
        self.assertEqual({'repositories'}, set(index))
        self.assertEqual(set(index['repositories']), set(trust))
        self.assertTrue(all(url.startswith('https://') for url in index['repositories']))
        self.assertTrue(all(set(policy) <= {'allowed_signers', 'fingerprints', 'public_keys'} for policy in trust.values()))

    def test_only_view_delivery_and_s3_ports_are_published_on_loopback_by_default(self):
        for name, host_port, container_port in [('web', 'GUAVAJAM_PORT:-8080', '8080'),
                                               ('assets', 'GUAVAJAM_ASSET_PORT:-8081', '8080'),
                                               ('seaweedfs', 'GUAVAJAM_S3_PORT:-8333', '8333')]:
            with self.subTest(service=name):
                self.assertEqual(['${GUAVAJAM_BIND_ADDRESS:-127.0.0.1}:${' + host_port + '}:' + container_port],
                                 self.services[name]['ports'])
        self.assertFalse(self.services['updater'].get('ports'))

    def test_builds_lock_packages_pin_external_images_and_install_the_shared_gateway(self):
        images = [value for value in re.findall(r'^FROM (\S+)', self.dockerfile, re.M) if value not in self.stages]
        images += [self.services['seaweedfs']['image']]
        for image in images:
            with self.subTest(image=image):
                self.assertRegex(image, r'@sha256:[0-9a-f]{64}$')
        self.assertIn('RUN uv sync --locked --all-packages --no-dev --no-editable', self.stages['packages'])
        self.assertIn('RUN python /tmp/install_pandoc.py', self.stages['a-guavajam-static-website-generator'])
        self.assertIn('COPY --from=packages /build/.venv/lib/python3.12/site-packages/guavajam/resources/storage /opt/gateway',
                      self.stages['gateway'])
        self.assertIn('ENTRYPOINT ["node", "server.mjs"]', self.stages['gateway'])

    def test_search_runtime_is_locked_self_hosted_and_enabled_for_pages(self):
        package = json.loads((ROOT / 'package.json').read_text())
        lock = json.loads((ROOT / 'package-lock.json').read_text())
        name = '@sqlite.org/sqlite-wasm'
        version = package['dependencies'][name]
        self.assertRegex(version, r'^\d+\.\d+\.\d+-build\d+$')
        self.assertEqual(version, lock['packages']['']['dependencies'][name])
        runtime = lock['packages']['node_modules/' + name]
        self.assertEqual(version, runtime['version'])
        self.assertRegex(runtime['integrity'], r'^sha512-[A-Za-z0-9+/]+=*$')
        install = 'npm ci --omit=dev --ignore-scripts --no-audit --no-fund'
        self.assertIn('COPY package.json package-lock.json ./', self.stages['search_assets'])
        self.assertIn(install, self.stages['search_assets'])
        for name in ['package.json', 'package-lock.json']:
            self.assertIn('!' + name, (APP / 'Dockerfile.dockerignore').read_text().splitlines())
        for source, target in [('index.mjs', 'sqlite3.mjs'), ('sqlite3.wasm', 'sqlite3.wasm')]:
            self.assertIn('/dist/' + source + ' some-implementations/a-guavajam-static-website-generator/src/a_guavajam_static_website_generator/assets/' + target,
                          self.stages['packages'])
        template = (ROOT / 'some-implementations/a-guavajam-static-website-generator/src/a_guavajam_static_website_generator/templates/pages-index.yml').read_text()
        workflow = yaml.safe_load(template.replace('@@GUAVAJAM_REF@@', '0' * 40))
        steps = workflow['jobs']['pages']['steps']
        self.assertTrue(any(install in step.get('run', '') for step in steps))
        generation = next(step for step in steps if 'python -m a_guavajam_static_website_generator.index' in step.get('run', ''))
        self.assertEqual('1', generation['env']['GUAVAJAM_REQUIRE_SEARCH'])
        self.assertTrue(generation['env']['GUAVAJAM_SQLITE_WASM_DIR'].endswith(
            '/.guavajam-tool/node_modules/@sqlite.org/sqlite-wasm/dist'))

    def test_static_web_server_supplies_wasm_and_module_mime_types(self):
        configuration = (APP / 'deploy/nginx.conf').read_text()
        self.assertIn(r'location ~ \.mjs$ { default_type application/javascript;', configuration)
        self.assertIn(r'location ~ \.wasm$ { default_type application/wasm;', configuration)

    def test_only_successful_content_addressed_downloads_are_immutable(self):
        configuration = (APP / 'deploy/nginx.conf').read_text()
        self.assertIn('map "$status:$uri" $guavajam_cache_control', configuration)
        rule = re.search(r'"~([^"\n]+)" "public, max-age=31536000, immutable";', configuration)
        self.assertIsNotNone(rule)
        pattern = re.compile(rule[1])
        hashed = 'index.' + 'a' * 64 + '.sqlite.gz'
        work = 'sources/example/work.' + 'b' * 64 + '.sqlite.gz'
        for status in (200, 206, 304):
            for relative in (hashed, work):
                self.assertIsNotNone(pattern.fullmatch(f'{status}:/{relative}'))
        for value in ('404:/' + hashed, '503:/' + work, '200:/index.html',
                      '200:/index.sqlite', '200:/sqlite3.wasm', '200:/index.latest.sqlite.gz'):
            self.assertIsNone(pattern.fullmatch(value))
        self.assertIn('default "no-cache";', configuration)
        self.assertIn('add_header Cache-Control $guavajam_cache_control always;', configuration)
        self.assertIn('gzip_types application/javascript text/javascript application/wasm;', configuration)


if __name__ == '__main__':
    unittest.main(verbosity=2)
