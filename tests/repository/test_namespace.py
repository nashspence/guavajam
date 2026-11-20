"""Owned format identifiers use the GuavaJam v1 namespace."""
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
        self.assertTrue({'guavajam.catalog.v1', 'guavajam.work.v1',
                         'guavajam.source-citation.v1', 'guavajam.index-view.v1',
                         'guavajam.site.v1', 'guavajam.asset.v1',
                         'guavajam.asset-release.v1',
                         'guavajam.chatgpt-capture-reference.v1'} <= identifiers)

    def test_work_starter_has_only_bibliographic_metadata(self):
        import yaml
        resources = ROOT / 'projects/core/src/guavajam_core/resources'
        source = (resources / 'templates/work/manuscript.md').read_text()
        settings = yaml.safe_load(source.split('---', 2)[1])
        self.assertEqual('guavajam.work.v1', settings['schema'])
        self.assertTrue({'title', 'author', 'language', 'rights'} <= settings.keys())
        self.assertFalse({'edition', 'published', 'edition_reason', 'slug', 'status'} & settings.keys())
        self.assertFalse((resources / 'templates/repository.json').exists())
        self.assertFalse((resources / 'schemas/guavajam-repository-v1.schema.json').exists())
        commands = (resources / 'commands.mk').read_text()
        self.assertNotIn('WORK=slug', commands)
        self.assertNotIn('GuavaJam-Publication', commands)
        self.assertNotIn('repository-template', commands)


if __name__ == '__main__':
    unittest.main(verbosity=2)
