"""Provisional multi-work snapshots cannot weaken issued catalog identity."""
import unittest
from guavajam_core.catalog import validate, CatalogError


def catalog():
    return {'schema':'guavajam.catalog.v1','title':'Drafts','publisher':'Example','language':'en',
            'works':[{'slug':slug,'metadata_revision':'a'*40,'editions':[{
                'revision':'a'*40,'published':'2026-11-16','status':'available'}]} for slug in ['first','second']]}


class ProvisionalCatalogTests(unittest.TestCase):
    def test_shared_snapshot_requires_both_work_paths_to_be_provisional(self):
        value = catalog()
        with self.assertRaises(CatalogError): validate(value)
        with self.assertRaises(CatalogError): validate(value, provisional={('second','a'*40)})
        self.assertEqual(value, validate(value, provisional={('first','a'*40),('second','a'*40)}))

    def test_provisional_context_does_not_allow_duplicate_editions_of_one_work(self):
        value = catalog(); value['works'] = value['works'][:1]
        value['works'][0]['editions'] *= 2
        with self.assertRaises(CatalogError): validate(value, provisional={('first','a'*40)})

    def test_shared_snapshots_still_count_each_edition_against_the_budget(self):
        value = catalog(); candidates = set()
        for work in value['works']:
            work['editions'] = [{'revision':format(i,'040x'),'published':'2026-11-16','status':'available'} for i in range(129)]
            work['metadata_revision'] = format(0,'040x')
            candidates.update((work['slug'],e['revision']) for e in work['editions'])
        with self.assertRaisesRegex(CatalogError,'exceeds 256'): validate(value, provisional=candidates)
