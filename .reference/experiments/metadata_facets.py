"""Generic exact-metadata facet SQL: membership, inherited passages and bounded options.

Native SQLite experiment. Synthetic inputs retain original JSON as an independent
predicate oracle. For external databases the JSON comparator is reconstructed
from existing facet membership; it compares query plans, not source extraction.
No external database is modified. Timings measure exact count plus first 20 IDs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
import sys
import time
from pathlib import Path

SCHEMA = '''
CREATE TABLE documents(id INTEGER PRIMARY KEY,edition_id INTEGER NOT NULL);
CREATE TABLE passages(id INTEGER PRIMARY KEY,document_id INTEGER NOT NULL);
CREATE INDEX passages_by_document ON passages(document_id);
CREATE TABLE facet_values(id INTEGER PRIMARY KEY,name TEXT COLLATE BINARY NOT NULL,
  value TEXT COLLATE BINARY NOT NULL,UNIQUE(name,value));
CREATE TABLE document_facets(document_id INTEGER NOT NULL,facet_id INTEGER NOT NULL,
  PRIMARY KEY(document_id,facet_id)) WITHOUT ROWID;
CREATE INDEX facets_by_value ON document_facets(facet_id,document_id);
CREATE TABLE document_metadata_json(document_id INTEGER PRIMARY KEY,metadata TEXT NOT NULL);
'''


def synthetic_database(path, elements=4000, editions=5):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    db=sqlite3.connect(path)
    db.executescript(SCHEMA)
    values={}
    did=pid=0
    for edition in range(1,editions+1):
        for element in range(elements):
            did+=1
            metadata={'Release':[f'v{edition}'],'Kind':['method' if element%3 else 'type'],
                      'Component':[f'c{element%100:03}'],'Element ID':[f'element:{element:05}'],
                      'Label':['all',['Café','café','Cafe','CAFE'][element%4]],
                      'Numeric':['1' if element%2 else '1.0']}
            if element==0:
                metadata['Boundary']=['']
            elif element==1:
                metadata['Boundary']=[]
            if element==2:
                metadata['Odd']=["x' OR 1=1 --"]
            db.execute('INSERT INTO documents VALUES (?,?)',(did,edition))
            db.execute('INSERT INTO document_metadata_json VALUES (?,?)',(did,json.dumps(metadata,ensure_ascii=False)))
            for key,items in metadata.items():
                for value in dict.fromkeys(items):
                    pair=(key,value)
                    if pair not in values:
                        values[pair]=db.execute('INSERT INTO facet_values(name,value) VALUES (?,?)',pair).lastrowid
                    db.execute('INSERT INTO document_facets VALUES (?,?)',(did,values[pair]))
            for _ in range(3):
                pid+=1
                db.execute('INSERT INTO passages VALUES (?,?)',(pid,did))
    db.commit();db.execute('VACUUM');db.close()


def prepare_json(db):
    present=db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='document_metadata_json'").fetchone()
    if present:
        return 'original synthetic JSON'
    db.executescript('''CREATE TEMP TABLE document_metadata_json(document_id INTEGER PRIMARY KEY,metadata TEXT NOT NULL);
      INSERT INTO document_metadata_json
      SELECT document_id,json_group_object(name,json(items)) FROM (
        SELECT df.document_id,v.name,json_group_array(v.value) AS items
        FROM document_facets df JOIN facet_values v ON v.id=df.facet_id
        GROUP BY df.document_id,v.name
      ) GROUP BY document_id;
      INSERT INTO document_metadata_json SELECT d.id,'{}' FROM documents d
        WHERE NOT EXISTS (SELECT 1 FROM document_metadata_json j WHERE j.document_id=d.id);
    ''')
    return 'JSON reconstructed from normalized facet membership; not independent source extraction'


def query(filters,kind='documents',mode='indexed',edition=None,term=None):
    clean={}
    for key,values in filters.items():
        if not isinstance(key,str) or not values or not all(isinstance(v,str) for v in values):
            raise ValueError('Filters require literal field names and nonempty lists of literal string values.')
        clean[key]=list(dict.fromkeys(values))
    binds=[]
    parts=[]
    for key,values in clean.items():
        placeholders=','.join('?' for _ in values)
        if mode=='indexed':
            parts.append(f'''SELECT df.document_id FROM facet_values v JOIN document_facets df ON df.facet_id=v.id
                WHERE v.name=? AND v.value IN ({placeholders})''')
        elif mode=='correlated':
            parts.append(f'''EXISTS (SELECT 1 FROM document_facets df JOIN facet_values v ON v.id=df.facet_id
                WHERE df.document_id=d.id AND v.name=? AND v.value IN ({placeholders}))''')
        elif mode=='json':
            parts.append(f'''EXISTS (SELECT 1 FROM document_metadata_json j,json_each(j.metadata) k,json_each(k.value) item
                WHERE j.document_id=d.id AND k.key=? AND item.value IN ({placeholders}))''')
        else:
            raise ValueError(mode)
        binds.extend([key,*values])
    cte=''
    clauses=[]
    if mode=='indexed' and parts:
        cte='WITH selected AS MATERIALIZED ('+' INTERSECT '.join(parts)+') '
        clauses.append('d.id IN (SELECT document_id FROM selected)')
    else:
        clauses.extend(parts)
    if edition is not None:
        clauses.append('d.edition_id=?');binds.append(edition)
    relation='documents d'+(' JOIN passages p ON p.document_id=d.id' if kind=='passages' else '')
    key='p.id' if kind=='passages' else 'd.id'
    if term is not None:
        from query_pipeline import literal_query
        table='passage_fts' if kind=='passages' else 'document_fts'
        clauses.append(f'{key} IN (SELECT rowid FROM {table} WHERE {table} MATCH ?)')
        binds.append(literal_query(term))
    where=(' WHERE '+' AND '.join(clauses)) if clauses else ''
    base=f'{cte}SELECT {key} AS id FROM {relation}{where}'
    return {'all':base+' ORDER BY id','count':'SELECT count(*) FROM ('+base+')','page':base+' ORDER BY id LIMIT 20','bind':binds}


def digest_rows(cursor):
    digest=hashlib.sha256();count=0;first=[]
    for row in cursor:
        value=row[0]
        count+=1;digest.update(str(value).encode()+b'\n')
        if len(first)<20:
            first.append(value)
    return {'count':count,'sha256_of_ordered_ids':digest.hexdigest(),'first_ids':first}


def make_cases(db):
    pairs={}
    for name,value,count in db.execute('''SELECT v.name,v.value,count(*) FROM facet_values v
        JOIN document_facets f ON f.facet_id=v.id GROUP BY v.id ORDER BY v.name,count(*) DESC,v.value'''):
        pairs.setdefault(name,[]).append((value,count))
    names=list(pairs)
    high=max(names,key=lambda n:len(pairs[n]))
    low=min((n for n in names if len(pairs[n])>1),key=lambda n:len(pairs[n]))
    low_values=[v for v,_ in pairs[low]][:2]
    first=db.execute('SELECT document_id FROM document_facets ORDER BY document_id LIMIT 1').fetchone()[0]
    compatible=db.execute('SELECT v.name,v.value FROM document_facets f JOIN facet_values v ON v.id=f.facet_id WHERE f.document_id=? ORDER BY v.name',(first,)).fetchall()
    combined={}
    for name,value in compatible[:3]:
        combined.setdefault(name,[]).append(value)
    cases=[('broad-equality',{low:[low_values[0]]}),('or-within-field',{low:low_values}),
           ('and-across-fields',combined),('high-cardinality-exact',{high:[pairs[high][0][0]]}),
           ('missing-field',{'Missing field example':['anything']}),
           ('missing-value',{low:['absent matching value example']}),
           ('duplicate-selected-values',{low:[low_values[0],low_values[0]]})]
    for name,value in [('Label','Café'),('Label','café'),('Label','Cafe'),('Numeric','1'),('Numeric','1.0'),('Boundary',''),('Odd',"x' OR 1=1 --")]:
        if name in pairs:
            cases.append(('literal-'+name+'-'+repr(value),{name:[value]}))
    return cases,high


def options(db,name,cursor=None,limit=50,edition=None):
    sql='SELECT v.value FROM facet_values v WHERE v.name=?'
    binds=[name]
    if cursor is not None:
        sql+=' AND v.value>?';binds.append(cursor)
    if edition is not None:
        sql+=' AND EXISTS (SELECT 1 FROM document_facets f JOIN documents d ON d.id=f.document_id WHERE f.facet_id=v.id AND d.edition_id=?)'
        binds.append(edition)
    sql+=' ORDER BY v.value COLLATE BINARY LIMIT ?';binds.append(limit+1)
    started=time.perf_counter();rows=[r[0] for r in db.execute(sql,binds)]
    return {'values':rows[:limit],'has_more':len(rows)>limit,'next_cursor':rows[limit-1] if len(rows)>limit else None,
            'ms':(time.perf_counter()-started)*1000,'plan':[list(r) for r in db.execute('EXPLAIN QUERY PLAN '+sql,binds)]}


def run(path,repetitions=3):
    db=sqlite3.connect(f'file:{path.absolute()}?mode=ro',uri=True)
    source=prepare_json(db)
    report={'database':str(path),'python':sys.version.split()[0],'sqlite':sqlite3.sqlite_version,
            'json_comparator_source':source,'cases':[]}
    cases,high=make_cases(db)
    edition=db.execute('SELECT max(edition_id) FROM documents').fetchone()[0]
    cases=[(name,filters,None) for name,filters in cases]
    if db.execute("SELECT name FROM sqlite_master WHERE name='document_fts'").fetchone():
        cases.extend([('fts-and-broad-facet',cases[0][1],'contract'),
                      ('fts-and-exact-id',cases[3][1],'contract'),
                      ('fts-no-match',cases[0][1],'absentmatchingwordxyz')])
    for name,filters,term in cases:
        for kind in ('documents','passages'):
            record={'name':name,'filters':filters,'term':term,'kind':kind,'modes':{}}
            truth=None
            for mode in ('indexed','correlated','json'):
                q=query(filters,kind,mode,term=term)
                identity=digest_rows(db.execute(q['all'],q['bind']))
                if truth is None:
                    truth=identity
                else:
                    assert identity==truth,(name,kind,mode,identity,truth)
                times=[]
                for _ in range(repetitions):
                    started=time.perf_counter()
                    count=db.execute(q['count'],q['bind']).fetchone()[0]
                    first=[r[0] for r in db.execute(q['page'],q['bind'])]
                    assert count==identity['count'] and first==identity['first_ids']
                    times.append((time.perf_counter()-started)*1000)
                record['modes'][mode]={'count_and_first_page_median_ms':statistics.median(times),
                                       'plan':[list(r) for r in db.execute('EXPLAIN QUERY PLAN '+q['page'],q['bind'])]}
            record.update(truth);record['identical']=True
            report['cases'].append(record)
    first=options(db,high,edition=edition)
    second=options(db,high,cursor=first['next_cursor'],edition=edition) if first['has_more'] else None
    if second:
        assert not set(first['values'])&set(second['values'])
        assert first['values'][-1]<second['values'][0]
    report['bounded_high_cardinality_options']={'field':high,'distinct_values':db.execute('SELECT count(*) FROM facet_values WHERE name=?',(high,)).fetchone()[0],
                                               'page_size':50,'edition_id':edition,'first':first,'second':second}
    if source=='original synthetic JSON':
        # Empty scalar is selectable; an empty list and missing field produce no value membership.
        empty=[r[0] for r in db.execute("SELECT document_id FROM document_metadata_json WHERE EXISTS (SELECT 1 FROM json_each(metadata) WHERE key='Boundary' AND value='[]')")]
        blank=query({'Boundary':['']})
        selected={r[0] for r in db.execute(blank['all'],blank['bind'])}
        assert empty and not selected.intersection(empty)
        blank_options=options(db,'Boundary')
        assert blank_options['values']==['']
        report['boundary_semantics']={'empty_string_matches':len(selected),'empty_list_documents':len(empty),
                                     'empty_list_not_coerced_to_blank':True,'original_json_preserves_missing_vs_declared_empty':True,
                                     'empty_string_is_offered_as_literal_option':True}
    db.close()
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path)
    parser.add_argument('--output',type=Path,default=Path(__file__).with_name('metadata-facets.json'))
    parser.add_argument('--repetitions',type=int,default=3)
    args=parser.parse_args()
    path=args.database or Path(__file__).with_name('synthetic-facets.sqlite')
    if not args.database and not path.exists():
        synthetic_database(path)
    report=run(path,args.repetitions)
    args.output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'output':str(args.output),'cases':len(report['cases']),'parity':True,
                     'options_cardinality':report['bounded_high_cardinality_options']['distinct_values']}))


if __name__=='__main__':
    main()
