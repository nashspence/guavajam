"""Pages initialization is owned by the optional generator."""
from pathlib import Path
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from a_guavajam_static_website_generator import bootstrap

REF = '3c3ee519a5b4a5201a79074bd2e0fb0a7c017048'


class IndexBootstrapTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        config = self.root / 'gitconfig'
        config.write_text('[user]\nname = Example Publisher\nemail = publisher@example.org\n')
        override = patch.dict(os.environ, {'GIT_CONFIG_GLOBAL': str(config), 'GIT_CONFIG_SYSTEM': os.devnull})
        override.start(); self.addCleanup(override.stop)
        self.settings = self.root / 'index.env'
        self.settings.write_text('GUAVAJAM_REPO=example/collection\nGUAVAJAM_TOOL_REF=' + REF + '\n')

    def test_index_uses_ordinary_main_and_separate_unpublished_checkpoint_branch(self):
        destination = self.root / 'collection'
        bootstrap.bootstrap(self.settings, destination, local=True)
        self.assertEqual('repositories: []\n', (destination / 'index.yaml').read_text())
        self.assertEqual('{}\n', (destination / 'trust.yaml').read_text())
        self.assertFalse((destination / 'site').exists())
        self.assertFalse((destination / '_site').exists())
        self.assertFalse((destination / '.env').exists())
        yaml_content = (destination / '.github/workflows/pages.yml').read_text()
        self.assertIn('actions/deploy-pages@', yaml_content)
        self.assertIn('refs/heads/guavajam-state', yaml_content)
        self.assertIn(REF, yaml_content)
        self.assertIn('mise where pandoc', yaml_content)
        self.assertEqual('2', subprocess.check_output(['git', 'rev-list', '--count', 'main'], cwd=destination).decode().strip())
        state = subprocess.check_output(['git', 'show', 'guavajam-state:STATE.json'], cwd=destination)
        self.assertEqual(bootstrap.CHECKPOINT, json.loads(state))
        self.assertNotIn('STATE.json', subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', 'main'], cwd=destination).decode())

    def test_installed_command_initializes_index_without_signing_configuration(self):
        import sys
        destination = self.root / 'from-cli'
        result = subprocess.run([sys.executable, '-m', 'a_guavajam_static_website_generator',
                                 'init', 'index', '--env', str(self.settings), '--output', str(destination),
                                 '--local'], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue((destination / '.github/workflows/pages.yml').is_file())

    def test_remote_checkpoint_and_pages_setup_precede_workflow_publication(self):
        from guavajam import bootstrap as shared
        upstream = self.root / 'origin.git'
        subprocess.run(['git', 'init', '--bare', '-q', str(upstream)], check=True)
        events = []
        original = shared.command

        def command(*args, **kwargs):
            if args[0] == 'gh':
                events.append(args[1])
                if args[1] == 'repo':
                    original('git', 'remote', 'add', 'origin', str(upstream), cwd=Path(args[-3]))
                if args[1] == 'api':
                    branches = original('git', '--git-dir', str(upstream), 'for-each-ref', '--format=%(refname)')
                    self.assertIn('refs/heads/main', branches)
                    self.assertIn('refs/heads/guavajam-state', branches)
                    main = original('git', '--git-dir', str(upstream), 'ls-tree', '-r', '--name-only', 'main')
                    self.assertNotIn('.github/workflows/pages.yml', main)
                return ''
            return original(*args, **kwargs)

        with patch('guavajam.bootstrap.command', side_effect=command), \
             patch('a_guavajam_static_website_generator.bootstrap.command', side_effect=command):
            bootstrap.bootstrap(self.settings, self.root / 'live-index')
        self.assertEqual(['repo', 'api'], events)
        main = original('git', '--git-dir', str(upstream), 'ls-tree', '-r', '--name-only', 'main')
        self.assertIn('.github/workflows/pages.yml', main)
