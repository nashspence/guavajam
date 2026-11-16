"""Owned format identifiers use the GuavaJam v1 namespace."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]


class NamespaceTests(unittest.TestCase):
    def test_owned_runtime_format_identifiers_use_one_namespace_and_version(self):
        identifiers = set()
        pattern = re.compile(r'''['"]([a-z][a-z0-9_-]*(?:[./][a-z0-9_-]+)*[./]v[0-9]+)['"]''')
        for project in ['core', 'view']:
            for path in (ROOT / 'projects' / project / 'src').rglob('*'):
                if path.suffix not in {'.py', '.js', '.mjs', '.json', '.md'}:
                    continue
                for identifier in pattern.findall(path.read_text()):
                    identifiers.add(identifier)
                    with self.subTest(path=path.relative_to(ROOT), identifier=identifier):
                        self.assertTrue(identifier.startswith('guavajam.'))
                        self.assertTrue(identifier.endswith('.v1'))
        self.assertTrue({'guavajam.repository.v1', 'guavajam.work.v1',
                         'guavajam.source-citation.v1', 'guavajam.citation.v1',
                         'guavajam.site.v1', 'guavajam.asset.v1',
                         'guavajam.asset-release.v1',
                         'guavajam.chatgpt-capture-reference.v1'} <= identifiers)

    def test_repository_starter_is_core_configuration_without_a_web_origin(self):
        resources = ROOT / 'projects/core/src/guavajam_core/resources'
        settings = json.loads((resources / 'templates/repository.json').read_text())
        schema = json.loads((resources / 'schemas/guavajam-repository-v1.schema.json').read_text())
        self.assertEqual('guavajam.repository.v1', settings['schema'])
        self.assertEqual(settings['schema'], schema['properties']['schema']['const'])
        self.assertEqual({'schema', 'publisher'}, set(schema['required']))
        self.assertNotIn('base_url', settings)
        self.assertTrue(settings['publisher'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
