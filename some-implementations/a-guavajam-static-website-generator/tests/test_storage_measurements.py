"""Storage alternatives retain all scope-global result and score fingerprints."""
from pathlib import Path
import unittest
import benchmark_search_storage as benchmark
import test_scoped_search as fixtures


class StorageMeasurementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ScopedSearchTests.setUpClass.__func__(cls)

    def test_current_scope_alternatives_match_the_native_production_oracle(self):
        paths = {'collection': self.first / 'index.sqlite',
                 'versions': self.work / 'versions.sqlite',
                 'documents': self.work / 'editions' / self.latest / 'search/documents.sqlite',
                 'passages': self.work / 'editions' / self.latest / 'search/passages.sqlite',
                 'history': self.work / 'history.sqlite'}
        for scope, source in paths.items():
            with self.subTest(scope=scope):
                report,cases = benchmark.benchmark(source,self.root / ('measure-'+scope),repeats=1)
                self.assertEqual(2,len(cases))
                self.assertTrue(all(row['rankingEquivalent'] for row in cases))
                self.assertTrue(all(row['scope']==scope for row in cases))
                self.assertTrue(all(row['raw_bytes']>0 and row['gzip']['9']['bytes']>0 for row in report['cases']))
                self.assertEqual(report['cases'][0]['queries'].keys(),report['cases'][1]['queries'].keys())
                self.assertTrue(any(row['matches'] for row in report['cases'][0]['queries'].values()))
