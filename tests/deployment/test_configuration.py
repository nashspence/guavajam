"""Deployment configuration contracts; run without Docker via make test-deployment-config."""
from pathlib import Path
import re
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]


class DeploymentConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = yaml.safe_load((ROOT / 'compose.yaml').read_text())
        cls.services = cls.compose['services']
        cls.dockerfile = (ROOT / 'Dockerfile').read_text()
        cls.stages = {
            name: body for name, body in re.findall(
                r'^FROM \S+ AS (\w+)\n(.*?)(?=^FROM |\Z)', cls.dockerfile, re.M | re.S)
        }

    def test_hosted_preview_has_read_only_source_and_only_reader_credentials(self):
        preview = self.services['private-preview']
        self.assertEqual(['private-preview'], preview['profiles'])
        self.assertTrue(preview['read_only'])
        self.assertEqual(['ALL'], preview['cap_drop'])
        self.assertEqual({'context': '.', 'target': 'preview'}, preview['build'])
        mounts = [m for m in preview['volumes'] if isinstance(m, dict)]
        self.assertEqual({'/source', '/run/preview'}, {m['target'] for m in mounts})
        self.assertTrue(all(m['read_only'] and not m['bind']['create_host_path'] for m in mounts))
        self.assertEqual('/run/preview/readonly.env', preview['environment']['S3_ENV'])
        self.assertEqual('${GUAVAJAM_SSH_ALLOWED_SIGNERS:-}', preview['environment']['GUAVAJAM_SSH_ALLOWED_SIGNERS'])
        self.assertEqual('/run/preview/session.key', preview['environment']['GUAVAJAM_PREVIEW_TOKEN_FILE'])
        self.assertNotIn('GUAVAJAM_PREVIEW_PUBLISHED', preview['environment'])
        self.assertNotIn('GUAVAJAM_PREVIEW_LABEL', preview['environment'])
        self.assertEqual(['${GUAVAJAM_BIND_ADDRESS:-127.0.0.1}:${GUAVAJAM_PREVIEW_PORT:-8082}:8080'], preview['ports'])
        self.assertFalse(preview.get('depends_on'))
        self.assertIn('COPY --from=node /usr/local/bin/node /usr/local/bin/node', self.stages['preview'])

    def test_default_view_and_optional_local_storage_have_separate_images(self):
        self.assertEqual({'updater', 'web'}, {
            name for name, service in self.services.items() if not service.get('profiles')
        })
        self.assertEqual({'assets', 'seaweedfs'}, {
            name for name, service in self.services.items() if service.get('profiles') == ['local-assets']
        })
        for name, target in [('updater', 'view'), ('web', 'web'), ('assets', 'gateway')]:
            with self.subTest(service=name):
                self.assertEqual({'context': '.', 'target': target}, self.services[name]['build'])
        # The gateway can target an existing S3 service without starting local storage.
        self.assertFalse(self.services['assets'].get('depends_on'))

    def test_application_containers_are_unprivileged_with_read_only_roots(self):
        for name, stage, user in [('updater', 'view', '1000:1000'),
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
        self.assertIn('RUN python /tmp/install_pandoc.py', self.stages['view'])
        self.assertIn('COPY --from=packages /build/.venv/lib/python3.12/site-packages/guavajam_core/resources/storage /opt/gateway',
                      self.stages['gateway'])
        self.assertIn('ENTRYPOINT ["node", "server.mjs"]', self.stages['gateway'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
