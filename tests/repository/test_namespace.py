"""GuavaJam source contracts and the external native archive use v1 identifiers."""
from pathlib import Path
import json
import re
import sys
import tomllib
import unittest
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools'))
from workspace import PROJECTS, PYTHON_PROJECTS, MODULES, GENERATOR_NAME, EXTRACTOR_NAME, ARCHIVER_NAME

class NamespaceTests(unittest.TestCase):
    def test_runtime_format_identifiers_follow_their_authority_and_use_v1(self):
        identifiers=set()
        pattern=re.compile(r"""['"]([a-z][a-z0-9_-]*(?:[./][a-z0-9_-]+)*[./]v[0-9]+)['"]""")
        for name,project in PROJECTS.items():
            for path in (project/'src').rglob('*'):
                if path.suffix not in {'.py','.js','.mjs','.json','.md'}: continue
                for identifier in pattern.findall(path.read_text()):
                    identifiers.add(identifier)
                    self.assertTrue(identifier.endswith('.v1'),str(path))
                    if name == ARCHIVER_NAME:
                        self.assertEqual('chatgpt.conversation-archive.v1',identifier)
                    elif identifier == 'chatgpt.conversation-archive.v1':
                        self.assertEqual(ROOT/'src/guavajam/source_custody.py',path)
                    else: self.assertTrue(identifier.startswith('guavajam.'),str(path))
        self.assertTrue({'guavajam.catalog.v1','guavajam.work.v1','guavajam.source-citation.v1',
            'guavajam.index-view.v1','guavajam.site.v1','guavajam.asset.v1',
            'guavajam.asset-release.v1','chatgpt.conversation-archive.v1'} <= identifiers)

    def test_runtime_distribution_and_command_ownership(self):
        root=tomllib.loads((ROOT/'pyproject.toml').read_text())
        self.assertEqual(['some-implementations/'+name for name in PYTHON_PROJECTS if name!='guavajam'],root['tool']['uv']['workspace']['members'])
        for name,project in PYTHON_PROJECTS.items():
            metadata=tomllib.loads((project/'pyproject.toml').read_text())['project']
            entry='guavajam.cli:main' if name=='guavajam' else MODULES[name]+('.renderer:main' if name==GENERATOR_NAME else '.extractor:main')
            self.assertEqual({name:entry},metadata['scripts'])
            if name!='guavajam': self.assertEqual(['guavajam=='+metadata['version']],metadata['dependencies'])
        manifest=json.loads((PROJECTS[ARCHIVER_NAME]/'src/manifest.json').read_text())
        self.assertEqual(ARCHIVER_NAME,manifest['name'])
        self.assertEqual(['activeTab','scripting'],manifest['permissions'])

    def test_work_starter_has_only_bibliographic_metadata(self):
        import yaml
        resources = ROOT / 'src/guavajam/resources'
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
