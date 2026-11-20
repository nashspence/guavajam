"""Catalogs retain exact Git-derived facts; no edition overrides or provisional mode."""
import copy
from pathlib import Path
import tempfile
import unittest

from guavajam_core.catalog import validate, compare, read_catalog, CatalogError


def catalog():
    return {'schema': 'guavajam.catalog.v1', 'hash_algorithm': 'sha1', 'tip': 'a' * 40,
            'editions': [{'revision': 'a' * 40, 'tree': 'b' * 40,
                          'published': '2026-11-19', 'message': 'Ordinary subject\n\nBody.\n'}]}


class CatalogDataTests(unittest.TestCase):
    def test_only_exact_git_derived_fields_are_accepted(self):
        self.assertEqual(catalog(), validate(catalog()))
        for key, value in [('works', []), ('publisher', 'Someone'), ('version', 1)]:
            current = catalog()
            current[key] = value
            with self.assertRaises(CatalogError):
                validate(current)
        for key in ('status', 'label', 'doi', 'notices', 'edition'):
            current = catalog()
            current['editions'][0][key] = 'override'
            with self.assertRaises(CatalogError):
                validate(current)

    def test_duplicate_revisions_are_rejected_but_reused_trees_are_valid(self):
        current = catalog()
        current['editions'].append(copy.deepcopy(current['editions'][0]))
        with self.assertRaises(CatalogError):
            validate(current)
        current['editions'][1]['revision'] = 'c' * 40
        validate(current)

    def test_retained_editions_are_an_immutable_ordered_prefix(self):
        old = catalog()
        current = copy.deepcopy(old)
        current['editions'].append({**old['editions'][0], 'revision': 'c' * 40})
        current['tip'] = 'c' * 40
        self.assertEqual(current, compare(old, current))
        for field, value in [('message', 'Different'), ('tree', 'd' * 40), ('published', '2026-11-20')]:
            changed = copy.deepcopy(current)
            changed['editions'][0][field] = value
            with self.assertRaises(CatalogError):
                compare(old, changed)
        current['editions'].reverse()
        with self.assertRaises(CatalogError):
            compare(old, current)

    def test_old_multiwork_and_duplicate_json_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'catalog.json'
            for raw in ['{"schema":"guavajam.catalog.v1","works":[]}',
                        '{"schema":"guavajam.catalog.v1","schema":"guavajam.catalog.v1"}']:
                path.write_text(raw)
                with self.assertRaises(CatalogError):
                    read_catalog(path)

    def test_empty_history_is_valid_before_the_first_content_commit(self):
        value = catalog()
        value['editions'] = []
        self.assertEqual(value, validate(value))


if __name__ == '__main__':
    unittest.main()
