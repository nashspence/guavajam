"""Actual source/render/cache pipelines stay complete across large inventories."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from guavajam_test_support.discovery_fixture import Publisher
from a_guavajam_static_website_generator.renderer import render
from a_guavajam_static_website_generator.navigation_manifest import load_navigation, file_inventory
from a_guavajam_static_website_generator.view_cache import cached_render
from guavajam.catalog import CatalogError


class BoundedRenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_large_valid_document_count_is_complete_with_individual_navigation_and_source_identity(self):
        publisher = Publisher(self.root / 'source')
        publisher.source('Large inventory', 'A source paragraph and its exact citation.')
        supporting = publisher.repo / 'content/supporting'
        supporting.mkdir()
        for number in range(140):
            (supporting / ('note-' + str(number).zfill(4) + '.md')).write_text(
                '---\ntitle: Supporting note ' + str(number) +
                '\nabstract: An optional literal summary.\n---\n\n<a name="evidence"></a>\n\n'
                'Independentbodyselector ' + str(number) + '.\n')
        revision = publisher.publish('A complete large source inventory')
        output = self.root / 'view'
        self.assertEqual(141, render(publisher.repo, revision, output, **publisher.trust))
        view = json.loads((output / 'VIEW.json').read_text())
        nav = load_navigation(output, view)
        manifest = json.loads((output / 'NAV.json').read_text())
        self.assertEqual(141, len(nav['documents']))
        self.assertNotIn('Independentbodyselector', (output / 'NAV.json').read_text())
        self.assertEqual('documents', manifest['layout'])
        self.assertEqual(141, len(list((output / 'navigation').glob('*.json'))))
        for source, detail in nav['documents'].items():
            self.assertEqual(hashlib.sha256((publisher.repo / source).read_bytes()).hexdigest(), detail['source_sha256'])
            self.assertTrue((output / detail['view']).is_file())
        self.assertEqual(file_inventory(output, exclude={'VIEW.json'}), view['files'])
        document = next(iter(manifest['documents'].values()))
        (output / document['url']).write_text('{}')
        with self.assertRaisesRegex(CatalogError, 'integrity'):
            list(load_navigation(output, view)['documents'].values())

    def test_rich_source_blocks_reconstruct_each_actual_occurrence_without_concatenating_passages(self):
        from test_navigation import SUPPORT
        publisher = Publisher(self.root / 'source')
        publisher.source('Source blocks', 'First paragraph.\n\n- List item one\n- List item two\n\n'
            '| Header | Value |\n| --- | --- |\n| A | B |\n\n> A quotation.\n\n'
            '~~~text\nliteral source\n~~~')
        supporting = publisher.repo / 'content/supporting'
        supporting.mkdir()
        (supporting / 'note.md').write_text(SUPPORT)
        revision = publisher.publish('Rich source occurrence inventory')
        output = self.root / 'view'
        render(publisher.repo, revision, output, **publisher.trust)
        for document in load_navigation(output)['documents'].values():
            blocks = document['blocks']
            self.assertEqual(sorted(block['position'] for block in blocks), [block['position'] for block in blocks])
            for record in document['anchors']:
                recovered = ' '.join(block['text'] for block in blocks
                    if block['scope'] == record['scope'] and
                    record['block_start'] <= block['position'] < record['block_end'] and block['text'])
                self.assertEqual(record['text'], recovered, record['anchor'])

    def test_render_failure_leaves_no_partial_destination_or_stage(self):
        publisher = Publisher(self.root / 'source')
        publisher.source('Invalid link', 'An unresolved [source](supporting/missing.md).')
        publisher.git('add', 'content')
        publisher.git('commit', '-qm', 'Invalid proposed link')
        revision = publisher.git('rev-parse', 'HEAD')
        output = self.root / 'view'
        with self.assertRaises(ValueError):
            render(publisher.repo, revision, output, preview=True)
        self.assertFalse(output.exists())
        self.assertFalse(list(self.root.glob('.guavajam-render-*')))

    def test_cache_inventory_beyond_one_mib_is_complete_and_rejects_tampering(self):
        revision = 'a' * 40
        canonical = {'hash_algorithm': 'sha1', 'revision': revision, 'manuscript': 'content/manuscript.md'}
        def produce(_repo, _sha, output, **_kwargs):
            output.mkdir()
            directory = output / 'many'
            directory.mkdir()
            for number in range(11000):
                (directory / ('citation-fixture-' + str(number).zfill(8) + '.json')).write_text('{}\n')
            (output / 'VIEW.json').write_text(json.dumps({'canonical': canonical, 'preview': False,
                'facts': {}, 'documents': {}, 'files': file_inventory(output)}))
        cache = self.root / 'cache'
        with patch('a_guavajam_static_website_generator.view_cache.render', side_effect=produce):
            cached_render(cache, self.root, revision, self.root / 'first')
        target = next(cache.iterdir())
        inventory = target / 'CACHE.json'
        self.assertGreater(inventory.stat().st_size, 1024 * 1024)
        record = json.loads(inventory.read_text())
        self.assertEqual('guavajam.view-cache.v1', record['schema'])
        self.assertEqual(canonical, record['canonical'])
        self.assertEqual(11001, len(record['files']))
        self.assertEqual('{}\n', (self.root / 'first/many/citation-fixture-00010999.json').read_text())
        (target / 'many/citation-fixture-00010999.json').write_text('changed')
        with self.assertRaisesRegex(CatalogError, 'corrupt view cache'):
            cached_render(cache, self.root, revision, self.root / 'retry')
        self.assertFalse((self.root / 'retry').exists())
