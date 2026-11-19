"""Bootstrap is a one-time GitHub setup, not a recurring publication operation."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml
from guavajam_core import bootstrap

REF = '3c3ee519a5b4a5201a79074bd2e0fb0a7c017048'


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = self.root / 'test-key'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(self.key)], check=True)
        self.signers = self.root / 'allowed-signers'
        self.signers.write_text('publisher ' + (self.root / 'test-key.pub').read_text())
        self.gitconfig = self.root / 'gitconfig'
        for key, value in [('user.name', 'Example Publisher'), ('user.email', 'publisher@example.org'),
                           ('gpg.format', 'ssh'), ('user.signingkey', str(self.key))]:
            subprocess.run(['git', 'config', '--file', str(self.gitconfig), key, value], check=True)
        env = {**os.environ, 'GIT_CONFIG_GLOBAL': str(self.gitconfig), 'GIT_CONFIG_SYSTEM': os.devnull}
        self.override = patch.dict(os.environ, env); self.override.start(); self.addCleanup(self.override.stop)

    def env_file(self, kind='work'):
        settings = self.root / ('settings-' + kind + '.env')
        rows = ['GUAVAJAM_REPO=example/' + ('field-notes' if kind == 'work' else 'collection'),
                'GUAVAJAM_TOOL_REF=' + REF, 'GH_TOKEN=local-bootstrap-token']
        if kind == 'work':
            rows += ['GUAVAJAM_TITLE="Field Notes"', 'GUAVAJAM_AUTHOR="Example Author"',
                     'GUAVAJAM_ALLOWED_SIGNERS=' + str(self.signers)]
        settings.write_text('\n'.join(rows) + '\n')
        return settings

    def test_bootstrap_signed_work_has_one_content_tree_without_work_commands(self):
        destination = self.root / 'field-notes'
        bootstrap.bootstrap('work', self.env_file(), destination, local=True)
        source = destination / 'content/manuscript.md'
        fields = yaml.safe_load(source.read_text().split('---', 2)[1])
        self.assertEqual('Field Notes', fields['title'])
        self.assertFalse({'slug', 'status', 'edition', 'published'} & fields.keys())
        self.assertFalse((destination / 'Makefile').exists())
        self.assertFalse((destination / '.env').exists())
        self.assertFalse((destination / 'guavajam.json').exists())
        workflow = (destination / '.github/workflows/verify.yml').read_text()
        self.assertIn(REF, workflow)
        self.assertIn('secrets.GUAVAJAM_ALLOWED_SIGNERS', workflow)
        self.assertNotIn('local-bootstrap-token', workflow)
        self.assertEqual('1', subprocess.check_output(['git', 'rev-list', '--count', 'main'], cwd=destination).decode().strip())
        self.assertNotIn('GuavaJam-Publication', source.read_text())
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.bootstrap('work', self.env_file(), destination, local=True)

    def test_index_uses_ordinary_main_and_separate_unpublished_checkpoint_branch(self):
        destination = self.root / 'collection'
        bootstrap.bootstrap('index', self.env_file('index'), destination, local=True)
        self.assertEqual('repositories: []\n', (destination / 'index.yaml').read_text())
        self.assertEqual('{}\n', (destination / 'trust.yaml').read_text())
        self.assertFalse((destination / 'site').exists())
        self.assertFalse((destination / '_site').exists())
        self.assertFalse((destination / '.env').exists())
        yaml_content = (destination / '.github/workflows/pages.yml').read_text()
        self.assertIn('actions/deploy-pages@', yaml_content)
        self.assertIn('refs/heads/guavajam-state', yaml_content)
        self.assertIn(REF, yaml_content)
        self.assertEqual('2', subprocess.check_output(['git', 'rev-list', '--count', 'main'], cwd=destination).decode().strip())
        state = subprocess.check_output(['git', 'show', 'guavajam-state:STATE.json'], cwd=destination)
        self.assertEqual(bootstrap.CHECKPOINT, json.loads(state))
        self.assertNotIn('STATE.json', subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', 'main'], cwd=destination).decode())

    def test_live_provisioning_sets_secret_before_publishing_main_and_pages_after_state(self):
        upstream = self.root / 'origin.git'
        subprocess.run(['git', 'init', '--bare', '-q', str(upstream)], check=True)
        commands = []
        original = bootstrap.command

        def fake(*args, **kw):
            if args[0] == 'gh':
                commands.append((args, kw.get('data')))
                if args[1:3] == ('repo', 'create'):
                    original('git', 'remote', 'add', 'origin', str(upstream), cwd=Path(args[-3]))
                return ''
            return original(*args, **kw)
        # gh is mocked, but both Git branches and signed commits are real objects.
        with patch('guavajam_core.bootstrap.command', side_effect=fake):
            bootstrap.bootstrap('work', self.env_file(), self.root / 'field-notes', local=False)
        self.assertEqual(['repo', 'secret'], [args[1] for args, _ in commands])
        self.assertEqual(self.signers.read_text(), commands[-1][1])
        self.assertEqual(REF, (self.root / 'field-notes/.github/workflows/verify.yml').read_text().split('ref: ')[1].splitlines()[0])
        self.assertIn('refs/heads/main', subprocess.check_output(['git', '--git-dir=' + str(upstream), 'show-ref']).decode())

    def test_no_shell_evaluation_or_unpinned_tool_ref(self):
        envfile = self.env_file()
        envfile.write_text(envfile.read_text().replace(REF, 'main'))
        with self.assertRaisesRegex(ValueError, 'pinned'):
            bootstrap.bootstrap('work', envfile, self.root / 'fail', local=True)
        self.assertFalse((self.root / 'fail').exists())
        envfile.write_text('GUAVAJAM_REPO=owner/repo\nTOKEN=$(whoami)\n')
        with self.assertRaisesRegex(ValueError, 'literal'):
            bootstrap.dotenv(envfile)

    def test_resources_need_neither_git_nor_optional_view(self):
        destination = self.root / 'capture'
        result = subprocess.run([sys.executable, '-m', 'guavajam_core', 'resources',
                                 'capture', '--output', str(destination)], cwd=self.root,
                                capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue((destination / 'capture.mjs').is_file())
        removed = subprocess.run([sys.executable, '-m', 'guavajam_core', 'new-work'], cwd=self.root,
                                 capture_output=True)
        self.assertNotEqual(0, removed.returncode)


if __name__ == '__main__':
    unittest.main(verbosity=2)
