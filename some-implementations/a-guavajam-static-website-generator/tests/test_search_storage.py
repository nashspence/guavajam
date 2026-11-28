"""Scope-specific ranking, token positions and evidence retain exact source fields."""
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import unittest
from unittest.mock import patch

import test_scoped_search as fixtures
from a_guavajam_static_website_generator.search_index import create_index

QUERIES = ('"Currenttitle"*', '"Supersededtitle"*', '"current"*', '"complete"*',
           '"supporting"*', '"Directoryabstractselector"*', '"zzabsent"*')


class SearchStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ScopedSearchTests.setUpClass.__func__(cls)

    connect = fixtures.ScopedSearchTests.connect

    def assert_corpus(self, actual, table, fields, rows, weights):
        with closing(sqlite3.connect(':memory:')) as reference:
            reference.execute('CREATE VIRTUAL TABLE expected USING fts5(' + ','.join(fields) +
                              ",tokenize='unicode61 remove_diacritics 2',detail=full,columnsize=1)")
            for identifier, *values in rows:
                reference.execute('INSERT INTO expected(rowid,' + ','.join(fields) + ') VALUES (' +
                                  ','.join('?' for _ in range(len(fields) + 1)) + ')', (identifier,*values))
            self.assertEqual(rows,actual.execute('SELECT rowid,' + ','.join(fields) + ' FROM ' + table +
                                                ' ORDER BY rowid').fetchall())
            for term in QUERIES:
                with self.subTest(table=table,term=term):
                    def ranked(db, name):
                        return db.execute('SELECT rowid,bm25(' + name + ',' + weights + ') FROM ' + name +
                                          ' WHERE ' + name + ' MATCH ? ORDER BY 2,rowid',(term,)).fetchall()
                    self.assertEqual(ranked(reference,'expected'),ranked(actual,table))
                    excerpts = actual.execute("SELECT snippet(" + table +
                        ",-1,char(57344),char(57345),'…',24) FROM " + table +
                        ' WHERE ' + table + ' MATCH ?',(term,)).fetchall()
                    self.assertTrue(all('\ue000' in text and '\ue001' in text for (text,) in excerpts))
            reference.execute("CREATE VIRTUAL TABLE tokens USING fts5vocab(expected,'instance')")
            actual.execute('CREATE VIRTUAL TABLE temp.tokens USING fts5vocab(main,' + table + ",'instance')")
            sql='SELECT term,doc,col,offset FROM {} ORDER BY doc,col,offset,term'
            self.assertEqual(reference.execute(sql.format('tokens')).fetchall(),
                             actual.execute(sql.format('temp.tokens')).fetchall())
            actual.execute('INSERT INTO ' + table + '(' + table + ",rank) VALUES ('integrity-check',1)")

    def test_physically_separate_bibliography_versions_and_document_corpora(self):
        # The explicit Works row uses only the latest signed bibliography.
        labels='Currenttag Shared subject Authority A Shared subject Authority B'
        with closing(sqlite3.connect(self.first/'index.sqlite')) as db:
            self.assert_corpus(db,'work_fts',['title','authors','abstract','labels'],
                              [(1,'Currenttitle','Firstcurrentauthor, Secondcurrentauthor','',labels)],'4,2,1,2')
        catalog=json.loads((self.work/'CATALOG.json').read_text())
        versions=[]
        for number,edition in enumerate(catalog['editions'],1):
            facts=json.loads((self.work/'editions'/edition['revision']/'BIBLIOGRAPHY.json').read_text())['facts']
            versions.append((number,'v'+str(number),facts['title'],edition['message']))
        with self.connect('versions') as db:
            self.assert_corpus(db,'version_fts',['name','title','message'],versions,'4,2,1')
        with self.connect('documents',revision=self.latest) as db:
            self.assert_corpus(db,'document_fts',['title','abstract'],
                              [(1,'Current supporting title','Directoryabstractselector.')],'4,1')

    def test_navigation_inventory_has_its_own_finite_budget_and_atomic_failure(self):
        root=self.root/'inventory-budget'
        shutil.copytree(self.work,root)
        self.addCleanup(shutil.rmtree,root)
        ordinary_stat=Path.stat
        reported=8*1024**2+1
        def inventory_size(path,*args,**kwargs):
            result=ordinary_stat(path,*args,**kwargs)
            if path.name=='VIEW.json':
                fields=list(result);fields[6]=reported;return __import__('os').stat_result(fields)
            return result
        entry={'repository':'https://example.org/work.git','path':'','tip':self.latest}
        with patch.object(Path,'stat',inventory_size):
            target=create_index(root,[entry],filename='check/history.sqlite',scope='history')
            self.assertTrue(target.is_file())
            target.unlink()
            reported=64*1024**2+1
            with self.assertRaisesRegex(ValueError,'operation budget'):
                create_index(root,[entry],filename='rejected/history.sqlite',scope='history')
            self.assertFalse((root/'rejected/history.sqlite').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
