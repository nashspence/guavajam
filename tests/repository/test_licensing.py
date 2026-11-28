"""Repository Apache-2.0 and standalone package licensing contracts."""
import hashlib
import json
from pathlib import Path
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[2]
LICENSE = 'LICENSES/Apache-2.0.txt'
# Verbatim https://www.apache.org/licenses/LICENSE-2.0.txt; no modified license text.
APACHE_SHA256 = 'cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30'


class LicensingTests(unittest.TestCase):
    def test_apache_text_is_verbatim_and_github_and_packages_receive_the_same_license(self):
        canonical = (ROOT / LICENSE).read_bytes()
        self.assertEqual(APACHE_SHA256, hashlib.sha256(canonical).hexdigest())
        self.assertEqual(canonical, (ROOT / 'LICENSE').read_bytes())
        for package in ['.', 'some-implementations/a-guavajam-static-website-generator', 'some-implementations/a-chatgpt-conversation-archive-gfm-extractor', 'some-implementations/a-chatgpt-conversation-archiver-browser-extension']:
            with self.subTest(package=package):
                self.assertEqual(canonical, (ROOT / package / LICENSE).read_bytes())

    def test_reuse_defaults_cover_files_and_preserve_explicit_file_notices(self):
        reuse = (ROOT / 'REUSE.toml').read_bytes()
        data = tomllib.loads(reuse.decode())
        self.assertEqual(1, data['version'])
        self.assertEqual([{'path': '**', 'precedence': 'closest',
                           'SPDX-FileCopyrightText': '2026 Nash Spence',
                           'SPDX-License-Identifier': 'Apache-2.0'}], data['annotations'])
        for package in ['.', 'some-implementations/a-guavajam-static-website-generator', 'some-implementations/a-chatgpt-conversation-archive-gfm-extractor', 'some-implementations/a-chatgpt-conversation-archiver-browser-extension']:
            with self.subTest(package=package):
                self.assertEqual(reuse, (ROOT / package / 'REUSE.toml').read_bytes())

    def test_owned_distributions_declare_spdx_license_and_ship_legal_artifacts(self):
        for package in ['.', 'some-implementations/a-guavajam-static-website-generator', 'some-implementations/a-chatgpt-conversation-archive-gfm-extractor', 'some-implementations/a-chatgpt-conversation-archiver-browser-extension']:
            with self.subTest(package=package):
                project = ROOT / package
                if package.endswith('a-chatgpt-conversation-archiver-browser-extension'):
                    self.assertEqual('Apache-2.0',json.loads((project/'package.json').read_text())['license'])
                    continue
                metadata = tomllib.loads((project / 'pyproject.toml').read_text())['project']
                self.assertEqual('Apache-2.0', metadata['license'])
                expected = [LICENSE, 'REUSE.toml']
                if package.endswith('a-guavajam-static-website-generator'):
                    expected.extend(['src/a_guavajam_static_website_generator/assets/lucide-LICENSE.txt',
                        'src/a_guavajam_static_website_generator/assets/fflate-LICENSE.txt', 'LICENSES/MIT.txt'])
                self.assertEqual(expected, metadata['license-files'])

    def test_browser_tool_metadata_agrees_with_repository_license(self):
        package = json.loads((ROOT / 'package.json').read_text())
        lock = json.loads((ROOT / 'package-lock.json').read_text())
        self.assertEqual('Apache-2.0', package['license'])
        self.assertEqual(package['license'], lock['packages']['']['license'])

    def test_pinned_inflater_has_exact_source_identity_and_mit_notice(self):
        package = json.loads((ROOT / 'package.json').read_text())
        lock = json.loads((ROOT / 'package-lock.json').read_text())
        self.assertEqual('0.8.3', package['dependencies']['fflate'])
        self.assertEqual('0.8.3', lock['packages']['node_modules/fflate']['version'])
        self.assertTrue(lock['packages']['node_modules/fflate']['integrity'].startswith('sha512-'))
        assets = ROOT / 'some-implementations/a-guavajam-static-website-generator/src/a_guavajam_static_website_generator/assets'
        notice = (assets / 'fflate-LICENSE.txt').read_text()
        self.assertIn('fflate ' + package['dependencies']['fflate'], notice)
        self.assertIn(hashlib.sha256((assets / 'fflate.mjs').read_bytes()).hexdigest(), notice)
        self.assertIn('Copyright (c) 2026 Arjun Barrett', notice)
        self.assertIn('SPDX-License-Identifier' + ': MIT', (assets / 'fflate.mjs.license').read_text())

    def test_self_hosted_sqlite_runtime_preserves_third_party_notices(self):
        package = json.loads((ROOT / 'package.json').read_text())
        notice = (ROOT / 'some-implementations/a-guavajam-static-website-generator/src/a_guavajam_static_website_generator/assets/sqlite3-LICENSE.txt').read_text()
        self.assertIn('@sqlite.org/sqlite-wasm ' + package['dependencies']['@sqlite.org/sqlite-wasm'], notice)
        self.assertIn((ROOT / LICENSE).read_text(), notice)
        self.assertIn('Copyright (c) 2010-2014 Emscripten authors', notice)
        self.assertIn('Copyright © 2005-2020 Rich Felker', notice)
        self.assertIn('The author disclaims copyright to this source code.', notice)
        data = tomllib.loads((ROOT / 'some-implementations/a-guavajam-static-website-generator/pyproject.toml').read_text())
        self.assertIn('assets/*.txt', data['tool']['setuptools']['package-data']['a_guavajam_static_website_generator'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
