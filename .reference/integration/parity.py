#!/usr/bin/env python3
"""Compare the scoped experiment's complete occurrences, not only search samples.
Usage: python parity.py <directory produced by scopes.py> <report.json>
"""
import itertools,json,sqlite3,sys
from pathlib import Path
base=Path(sys.argv[1]);checks={}
def connect(name):return sqlite3.connect(base/(name+'.sqlite'))
def compare(left,right,label):
    count=0
    for a,b in itertools.zip_longest(left,right):
        assert a==b,(label,count,str(a)[:100],str(b)[:100]);count+=1
    checks[label]=count
with connect('versions-5') as v,connect('works-5') as w,connect('documents-5') as d,connect('passages-5') as p,connect('history-5') as h:
    # Publication order is distinct from the claimed date. This checks selection,
    # not source signature/first-parent verification; those are producer inputs.
    latest=v.execute('SELECT work,position,revision FROM editions ORDER BY position DESC LIMIT 1').fetchone()
    bydate=v.execute('SELECT work,position,revision FROM editions ORDER BY day DESC LIMIT 1').fetchone()
    assert latest!=bydate and latest[1]==5 and bydate[1]==1
    assert w.execute("SELECT work,position,revision FROM editions WHERE work='Riverhog'").fetchone()==latest
    assert p.execute('SELECT work,position,revision FROM editions').fetchone()==latest
    assert d.execute('SELECT work,position,revision FROM editions').fetchone()==latest
    assert w.execute('SELECT count(*) FROM editions').fetchone()[0]==2
    checks['publication_order_not_date']=True
    passages='''SELECT e.work,e.revision,d.path,p.anchor,p.position,f.title,f.authors,f.abstract,f.labels,f.body,f.git FROM passage_fields f JOIN passages p ON p.id=f.id JOIN documents d ON d.id=p.document_id JOIN editions e ON e.id=d.edition_id WHERE e.work='Riverhog' AND e.position=5 ORDER BY d.path,p.position'''
    compare(p.execute(passages),h.execute(passages),'edition_passage_fields_equal_history_occurrences')
    # The collection's evidence corpus is separately body-only, but every
    # anchor must point into exactly the selected edition.
    evidence='''SELECT e.work,e.revision,d.path,p.anchor,p.position,t.body FROM passages p JOIN texts t ON t.id=p.body_id JOIN documents d ON d.id=p.document_id JOIN editions e ON e.id=d.edition_id WHERE e.work='Riverhog' AND e.position=5 ORDER BY d.path,p.position'''
    compare(w.execute(evidence),p.execute(evidence),'latest_collection_anchored_body_evidence')
    metadata='''SELECT e.work,e.revision,d.path,f.name,f.value FROM facets f JOIN documents d ON d.id=f.document_id JOIN editions e ON e.id=d.edition_id WHERE e.work='Riverhog' AND e.position=5 AND d.path<>'content/manuscript.md' ORDER BY d.path,f.name,f.value'''
    compare(d.execute(metadata),p.execute(metadata),'document_passage_facet_memberships')
    compare(d.execute(metadata),h.execute(metadata),'document_history_facet_memberships')
    support='''SELECT e.work,e.revision,d.path,d.title,d.abstract FROM documents d JOIN editions e ON e.id=d.edition_id WHERE e.work='Riverhog' AND e.position=5 AND d.path<>'content/manuscript.md' ORDER BY d.path'''
    compare(d.execute(support),h.execute(support),'directory_title_abstract_identity')
    # No non-selected FTS occurrences can affect the scope's BM25 statistics.
    for db,table in [(v,'version_fts'),(w,'search_fts'),(d,'document_fts'),(p,'passage_fts'),(h,'passage_fts')]:
        assert db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]>0
    for db,table in [(v,'passage_fts'),(v,'search_fts'),(d,'passage_fts'),(p,'search_fts'),(h,'search_fts')]:
        assert db.execute('SELECT count(*) FROM sqlite_schema WHERE name=?',(table,)).fetchone()[0]==0
    checks['no_unrelated_FTS_tables']=True
Path(sys.argv[2]).write_text(json.dumps({'result':'passed','checks':checks},indent=2)+'\n');print(checks)
