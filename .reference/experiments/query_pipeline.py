"""Reproduce GuavaJam query semantics while deferring excerpts until after paging.

The reference SQL mirrors search-worker.js at 54fce07b619628a5e92be946be4b189873ddf1a0.
This is an experimental query compiler, not a production replacement. It retains
occurrence-level FTS, exact scores, stable ordering, and the same returned fields.
It does not reimplement the worker's later display highlighting/body-anchor step.

No arguments creates a deterministic synthetic fixture. --database accepts a
current-schema work index (collection cases need its repositories/works tables).
--cases-output exports SQL/bind/expected outputs for the browser harness.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import re
import sqlite3
import statistics
import sys
import time
from pathlib import Path

FIELDS = [('body', 4), ('authors', 1), ('title', 0), ('abstract', 2), ('labels', 3), ('git', 5)]
DOC_FIELDS = [('title', 0), ('abstract', 1)]
PAGE_SIZE = 20


def literal_query(term):
    return ' AND '.join('"' + word + '"*' for word in re.findall(r'[^\W_]+', str(term)[:256])[:12])


def excerpts(table, fields):
    return ', '.join(f"snippet({table}, {column}, char(57344), char(57345), '…', 24) AS {name}_excerpt"
                     for name, column in fields)


def evidence(alias, content, fields):
    def choose(value):
        return 'CASE ' + ' '.join(f'WHEN instr({alias}.{name}_excerpt,char(57344))>0 THEN {value(name)}'
                                 for name, _ in fields) + ' ELSE NULL END'
    return [
        choose(lambda name: f'{alias}.{name}_excerpt') + ' AS excerpt',
        choose(lambda name: "'" + (content if name == 'body' else 'metadata') + "'") + ' AS excerpt_context',
        choose(lambda name: "'" + name + "'") + ' AS excerpt_field',
    ]


def named(expressions):
    return [(expression, re.split(r'\s+AS\s+', expression, flags=re.I)[-1] if re.search(r'\s+AS\s+', expression, re.I)
             else expression.rsplit('.', 1)[-1]) for expression in expressions]


def query_case(kind='passages', term='contract', edition='all', document='', supporting='all',
               sort='relevance', page=1, keyword='', subject=''):
    """Return baseline and optimized count/page SQL with JSON-serializable binds."""
    fts = literal_query(term)
    if not fts:
        raise ValueError('This experiment targets text queries; empty browsing queries are unchanged.')
    grouped = kind in {'versions', 'collection'}
    table = 'document_fts' if kind == 'documents' else 'passage_fts' if kind == 'passages' else 'search_fts'
    fields = DOC_FIELDS if kind == 'documents' else FIELDS
    score = f'bm25({table},4.0,1.0)' if kind == 'documents' else f'bm25({table},4.0,2.0,1.0,2.0,1.0,0.5)'
    if kind == 'documents':
        hit_columns = ['document_fts.rowid AS id', 'document_fts.rowid AS document_id']
        hit_from = 'document_fts'
    elif kind == 'passages':
        hit_columns = ['s.id']
        hit_from = 'passage_fts JOIN passage_records s ON s.id=passage_fts.rowid'
    else:
        hit_columns = ['s.work_id', 's.edition_id', 's.document_id', 's.id', 's.url']
        hit_from = 'search_fts JOIN search_records s ON s.id=search_fts.rowid'
        if kind == 'versions':
            hit_from += ' JOIN documents selected_document ON selected_document.id=s.document_id'
    hit_where = f'{table} MATCH ?'
    hit_binds = [fts]
    if kind == 'versions':
        hit_where += " AND (?='' OR selected_document.path=?)"
        hit_binds += [document, document]
    hit_query = lambda extra: 'SELECT ' + ','.join(hit_columns + extra) + f' FROM {hit_from} WHERE {hit_where}'
    group_key = 'work_id' if kind == 'collection' else 'edition_id'
    rank_cte = (f", ranked AS (SELECT *,ROW_NUMBER() OVER (PARTITION BY {group_key} ORDER BY score,id DESC) AS winning_rank FROM hits)"
                if grouped else '')
    selected_hits = 'ranked' if grouped else 'hits'

    work_where = "(?='all' OR (?='latest' AND e.revision=w.latest_revision) OR e.revision=?)"
    where_binds = [edition, edition, edition]
    if kind == 'documents':
        core = ['d.id', 'd.title', 'd.abstract', 'd.url', 'e.revision', 'e.published', 'e.position',
                'd.title AS document_title', 'd.path AS document_path', "'' AS anchor", "'document' AS scope",
                '0 AS node_position', 'm.score', 'NULL AS hit_url', 'e.position AS match_position', 'd.title AS match_document']
        joins = 'documents d JOIN editions e ON e.id=d.edition_id JOIN works w ON w.id=e.work_id JOIN {hits} m ON m.document_id=d.id'
        where = work_where + " AND d.path<>'content/manuscript.md' AND (?='' OR d.path=?)"
        where_binds += [document, document]
    elif kind == 'passages':
        core = ['p.id', 'p.title', 'p.url', 'p.citation_url', 'e.revision', 'e.published', 'e.position',
                'd.title AS document_title', 'd.path AS document_path', 'd.id AS document_order',
                'p.anchor', 'p.scope', 'p.citation_json', 'p.position AS node_position', 'm.score',
                'e.position AS match_position', 'd.title AS match_document', 'NULL AS hit_url']
        joins = 'passages p JOIN documents d ON d.id=p.document_id JOIN editions e ON e.id=p.edition_id JOIN works w ON w.id=e.work_id JOIN {hits} m ON m.id=p.id'
        where = work_where + " AND (?='' OR d.path=?) AND (?<>'manuscript' OR d.path='content/manuscript.md')"
        where_binds += [document, document, supporting]
    elif kind == 'versions':
        core = ['e.id', "'v'||(e.position+1) AS title", "'editions/'||e.revision||'/read/index.html' AS url",
                'e.revision', 'e.published', 'e.position', 'e.message', "'' AS document_title", "'' AS document_path",
                "'' AS anchor", "'' AS scope", '0 AS node_position', 'm.score', 'm.url AS hit_url',
                'm.document_id AS match_document_id', 'e.position AS match_position', 'd.title AS match_document']
        joins = 'editions e JOIN works w ON w.id=e.work_id JOIN {hits} m ON m.edition_id=e.id JOIN documents d ON d.id=m.document_id'
        where = work_where + " AND (?='' OR EXISTS (SELECT 1 FROM documents selected WHERE selected.edition_id=e.id AND selected.path=?))"
        where_binds += [document, document]
    elif kind == 'collection':
        core = ['w.id', 'w.title', 'w.authors', 'w.abstract', 'w.latest_published', 'w.latest_revision', 'w.edition_count',
                'r.path', 'r.url AS repository', 'm.score', 'm.url AS hit_url', 'm.document_id AS match_document_id',
                'e.position AS match_position', 'd.title AS match_document',
                '(SELECT json_group_array(name) FROM (SELECT name FROM keywords WHERE work_id=w.id ORDER BY name COLLATE NOCASE,name)) AS keywords_json',
                "(SELECT json_group_array(json_object('key',key,'label',label)) FROM (SELECT key,label FROM subjects WHERE work_id=w.id ORDER BY label COLLATE NOCASE,key)) AS subjects_json"]
        joins = 'works w JOIN repositories r ON w.repository_id=r.id JOIN {hits} m ON m.work_id=w.id LEFT JOIN editions e ON e.id=m.edition_id LEFT JOIN documents d ON d.id=m.document_id'
        where = "(?='' OR EXISTS (SELECT 1 FROM keywords k WHERE k.work_id=w.id AND k.name=?)) AND (?='' OR EXISTS (SELECT 1 FROM subjects s WHERE s.work_id=w.id AND s.key=?))"
        where_binds = [keyword, keyword, subject, subject]
    else:
        raise ValueError(kind)

    orders = ({'relevance': 'score ASC,latest_published DESC,title COLLATE NOCASE,id',
               'recent': 'latest_published DESC,title COLLATE NOCASE,id', 'oldest': 'latest_published ASC,title COLLATE NOCASE,id',
               'title': 'title COLLATE NOCASE,id', 'editions': 'edition_count DESC,title COLLATE NOCASE,id'} if kind == 'collection'
              else {'relevance': 'score ASC,document_order,node_position,id', 'publication': 'document_order,node_position,id',
                    'oldest': 'document_order DESC,node_position DESC,id', 'title': 'title COLLATE NOCASE,document_order,node_position,id'} if kind == 'passages'
              else {'relevance': 'score ASC,position DESC,document_path,node_position,id',
                    'publication': 'position DESC,document_path,node_position,id', 'oldest': 'position ASC,document_path,node_position,id',
                    'title': 'title COLLATE NOCASE,position DESC,document_path,node_position,id',
                    'date-newest': 'published DESC,position DESC,document_path,node_position,id',
                    'date-oldest': 'published ASC,position ASC,document_path,node_position,id'})
    order = orders[sort]
    content = 'passage' if kind == 'passages' else 'document'
    selection = ','.join(core)
    ev = evidence('m', content, fields)
    ranked_where = where + (' AND m.winning_rank=1' if grouped else '')
    baseline = f"WITH hits AS MATERIALIZED ({hit_query([score+' AS score',excerpts(table,fields)])}){rank_cte} SELECT {selection},{','.join(ev)} FROM {joins.format(hits=selected_hits)} WHERE {ranked_where}"
    binds = hit_binds + where_binds
    page_binds = [PAGE_SIZE, (page - 1) * PAGE_SIZE]
    # Counting needs only matching membership; grouped searches count entities.
    count_entity = {'documents': 'd.id', 'passages': 'p.id', 'versions': 'e.id', 'collection': 'w.id'}[kind]
    optimized_count = f"WITH hits AS MATERIALIZED ({hit_query([])}) SELECT COUNT({'DISTINCT ' if grouped else ''}{count_entity}) AS total FROM {joins.format(hits='hits')} WHERE {where}"
    # FTS auxiliaries run in MATCH context. The page CTE bounds all evidence work.
    page_select = f"SELECT {selection},m.id AS __match_id FROM {joins.format(hits=selected_hits)} WHERE {ranked_where}"
    projected = ','.join('page.' + name for _, name in named(core))
    optimized_page = f"""WITH hits AS MATERIALIZED ({hit_query([score+' AS score'])}){rank_cte},
        page AS MATERIALIZED (SELECT * FROM ({page_select}) ORDER BY {order} LIMIT ? OFFSET ?),
        evidence AS MATERIALIZED (SELECT {table}.rowid AS __match_id,{excerpts(table,fields)} FROM {table}
            WHERE {table} MATCH ? AND {table}.rowid IN (SELECT __match_id FROM page))
        SELECT {projected},{','.join(ev)} FROM page JOIN evidence m ON m.__match_id=page.__match_id ORDER BY {order}"""
    return {'kind': kind, 'term': term, 'edition': edition, 'document': document, 'sort': sort, 'page': page,
            'baseline_count': {'sql': f'SELECT COUNT(*) AS total FROM ({baseline})', 'bind': binds},
            'baseline_page': {'sql': f'SELECT * FROM ({baseline}) ORDER BY {order} LIMIT ? OFFSET ?', 'bind': binds+page_binds},
            'optimized_count': {'sql': optimized_count, 'bind': binds},
            'optimized_page': {'sql': optimized_page, 'bind': binds+page_binds+[fts]}}


def synthetic_database(path, documents=300, editions=4, works=3):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    db = sqlite3.connect(path)
    db.executescript('''
        CREATE TABLE repositories(id INTEGER PRIMARY KEY,url TEXT,path TEXT,tip TEXT);
        CREATE TABLE works(id INTEGER PRIMARY KEY,repository_id INTEGER,title TEXT,authors TEXT,abstract TEXT,
            latest_published TEXT,latest_revision TEXT,edition_count INTEGER);
        CREATE TABLE editions(id INTEGER PRIMARY KEY,work_id INTEGER,position INTEGER,revision TEXT,published TEXT,message TEXT);
        CREATE TABLE documents(id INTEGER PRIMARY KEY,edition_id INTEGER,work_id INTEGER,title TEXT,path TEXT,url TEXT,abstract TEXT);
        CREATE TABLE search_records(id INTEGER PRIMARY KEY,work_id INTEGER,edition_id INTEGER,document_id INTEGER,url TEXT);
        CREATE TABLE passages(id INTEGER PRIMARY KEY,edition_id INTEGER,document_id INTEGER,work_id INTEGER,anchor TEXT,title TEXT,
            position INTEGER,scope TEXT,url TEXT,citation_url TEXT,citation_json TEXT);
        CREATE TABLE passage_records(id INTEGER PRIMARY KEY,work_id INTEGER,edition_id INTEGER,document_id INTEGER,url TEXT);
        CREATE TABLE keywords(work_id INTEGER,name TEXT); CREATE TABLE subjects(work_id INTEGER,key TEXT,label TEXT);
        CREATE INDEX documents_by_edition ON documents(edition_id,path);
        CREATE INDEX passages_by_edition ON passages(edition_id);
        CREATE INDEX passages_by_document ON passages(document_id,position);
        CREATE VIRTUAL TABLE search_fts USING fts5(title,authors,abstract,labels,body,git,detail=full,columnsize=1,tokenize='unicode61 remove_diacritics 2');
        CREATE VIRTUAL TABLE passage_fts USING fts5(title,authors,abstract,labels,body,git,detail=full,columnsize=1,tokenize='unicode61 remove_diacritics 2');
        CREATE VIRTUAL TABLE document_fts USING fts5(title,abstract,content=documents,content_rowid=id,detail=full,columnsize=1,tokenize='unicode61 remove_diacritics 2');
    ''')
    for work in range(1, works+1):
        db.execute('INSERT INTO repositories VALUES (?,?,?,?)', (work, f'https://github.com/example/work-{work}', f'work-{work}',f'{work*100+editions:040x}'))
        db.execute('INSERT INTO works VALUES (?,?,?,?,?,?,?,?)',(work,work,f'Example Work {work}',f'Writer {work}','Current work abstract',
                     '2026-01-01',f'{work*100+editions:040x}',editions))
        db.execute('INSERT INTO keywords VALUES (?,?)',(work,'Contract' if work%2 else 'Research'))
        db.execute('INSERT INTO subjects VALUES (?,?,?)',(work,'Methods','Methods'))
        for edition in range(1, editions+1):
            revision = f'{work*100+edition:040x}'
            eid = db.execute('INSERT INTO editions(work_id,position,revision,published,message) VALUES (?,?,?,?,?)',
                             (work,edition-1,revision,f'2026-01-{32-edition:02}','Revision observation')).lastrowid
            for ordinal in range(documents):
                path_text = f'content/supporting/d-{ordinal}.md' if ordinal else 'content/manuscript.md'
                title = f'Contract operation {ordinal}'
                abstract = 'Transaction abstract with unicode café' if ordinal%7 else 'Archive abstract'
                url = f'work-{work}/editions/{revision}/read/{ordinal}.html'
                did = db.execute('INSERT INTO documents(edition_id,work_id,title,path,url,abstract) VALUES (?,?,?,?,?,?)',
                                 (eid,work,title,path_text,url,abstract)).lastrowid
                if ordinal:
                    db.execute('INSERT INTO document_fts(rowid,title,abstract) VALUES (?,?,?)',(did,title,abstract))
                author = 'Historical Writer' if edition==1 else f'Writer {work}'
                body = ('Transaction contract archive collection sqlite acceptance guarantee. '*8) + (' needle' if ordinal%13==0 else '')
                git = f'{revision} v{edition} {path_text} rights BSD-3-Clause example work provenance'
                db.execute('INSERT INTO search_records VALUES (?,?,?,?,?)',(did,work,eid,did,url))
                db.execute('INSERT INTO search_fts(rowid,title,authors,abstract,labels,body,git) VALUES (?,?,?,?,?,?,?)',
                           (did,title,author,'Work abstract','Contract Methods',body,git))
                for anchor in range(3):
                    aid = f'anchor-{anchor}'
                    target = url+'#'+aid
                    pid = db.execute('INSERT INTO passages(edition_id,document_id,work_id,anchor,title,position,scope,url,citation_url,citation_json) VALUES (?,?,?,?,?,?,?,?,?,?)',
                             (eid,did,work,aid,f'Passage {anchor}',anchor,'document',target,target+'-cite',json.dumps({'revision':revision,'document':path_text,'anchor':aid}))).lastrowid
                    db.execute('INSERT INTO passage_records VALUES (?,?,?,?,?)',(pid,work,eid,did,target))
                    db.execute('INSERT INTO passage_fts(rowid,title,authors,abstract,labels,body,git) VALUES (?,?,?,?,?,?,?)',
                               (pid,title+f' Passage {anchor}',author,'Work abstract','Contract Methods',body+str(anchor),git+' '+aid))
    for table in ('search_fts','passage_fts','document_fts'):
        db.execute(f"INSERT INTO {table}({table}) VALUES ('optimize')")
    db.commit()
    db.execute('VACUUM')
    db.close()


def execute(db, query):
    return [dict(row) for row in db.execute(query['sql'],query['bind'])]


def cases_for(db):
    path = db.execute("SELECT path FROM documents WHERE path<>'content/manuscript.md' ORDER BY id LIMIT 1").fetchone()[0]
    edition = db.execute('SELECT revision FROM editions ORDER BY position DESC LIMIT 1').fetchone()[0]
    cases=[]
    for kind in ('documents','passages','versions','collection'):
        for term in ('contract','transaction','transaction v1','historical transaction','needle','absentmatchingwordxyz'):
            cases.append({'kind':kind,'term':term,'edition':'all','sort':'relevance'})
        if kind!='collection':
            cases.extend([{'kind':kind,'term':'contract','edition':'latest','sort':'publication'},
                          {'kind':kind,'term':'transaction','edition':edition,'document':path,'sort':'title'},
                          {'kind':kind,'term':'contract','edition':'all','page':2,'sort':'oldest'}])
        else:
            cases.append({'kind':kind,'term':'transaction','keyword':'Contract','subject':'Methods','sort':'recent'})
    # Real contract-element titles need not contain the synthetic word 'contract'.
    # Ensure the Documents directory has positive broad and paged cases too.
    tokens=Counter()
    for title,abstract in db.execute('SELECT title,abstract FROM documents ORDER BY id LIMIT 2000'):
        tokens.update(set(word.lower() for word in re.findall(r'[^\W_]+',(title or '')+' '+(abstract or '')) if len(word)>3))
    if tokens:
        broad=sorted(tokens,key=lambda word:(-tokens[word],word))[0]
        cases.extend([{'kind':'documents','term':broad,'edition':'all','sort':'relevance'},
                      {'kind':'documents','term':broad,'edition':'latest','page':2,'sort':'title'}])
    largest=db.execute('''SELECT d.path,passage_fts.body FROM passage_fts JOIN passages p ON p.id=passage_fts.rowid
        JOIN documents d ON d.id=p.document_id ORDER BY length(passage_fts.body) DESC,p.id LIMIT 1''').fetchone()
    if largest:
        large_words=Counter(word.lower() for word in re.findall(r'[^\W_]+',largest[1] or '') if len(word)>4)
        if large_words:
            word=sorted(large_words,key=lambda value:(-large_words[value],value))[0]
            cases.extend([{'kind':'passages','term':word,'document':largest[0],'edition':'all','sort':'relevance'},
                          {'kind':'versions','term':word,'document':largest[0],'edition':'all','sort':'relevance'}])
    return cases


def canonical_rows(rows):
    """Derived JSON objects may differ in insignificant serialization whitespace."""
    return [{key:json.loads(value) if key.endswith('_json') and value else value for key,value in row.items()} for row in rows]


def run(db_path, repetitions=3, oracle_database=None):
    db=sqlite3.connect(db_path)
    db.row_factory=sqlite3.Row
    oracle=sqlite3.connect(oracle_database) if oracle_database else db
    oracle.row_factory=sqlite3.Row
    report={'database':str(db_path),'python':sys.version.split()[0],'sqlite':sqlite3.sqlite_version,
            'oracle_database':str(oracle_database or db_path),'timing_repetitions':repetitions,
            'scope':'SQL membership/count/order/evidence parity; excludes UI/postprocessing; JSON fields compared structurally', 'cases':[]}
    export=[]
    for params in cases_for(oracle):
        case=query_case(**params)
        expected_count=execute(oracle,case['baseline_count'])[0]['total']
        page=max(1,min(params.get('page',1),(expected_count+PAGE_SIZE-1)//PAGE_SIZE))
        params['page']=page
        case=query_case(**params)
        actual_count=execute(db,case['optimized_count'])[0]['total']
        expected=execute(oracle,case['baseline_page'])
        actual=execute(db,case['optimized_page'])
        assert expected_count==actual_count,(params,expected_count,actual_count)
        assert canonical_rows(expected)==canonical_rows(actual),(params,expected[:1],actual[:1])
        timings={}
        for mode in ('baseline','optimized'):
            samples=[]
            for _ in range(repetitions):
                started=time.perf_counter()
                execute(db,case[mode+'_count']);execute(db,case[mode+'_page'])
                samples.append((time.perf_counter()-started)*1000)
            timings[mode+'_median_ms']=statistics.median(samples)
        report['cases'].append({**params,'total':expected_count,'rows':len(expected),'identical':True,**timings})
        export.append({**case,'expected_count':expected_count,'expected_rows':expected})
    return report,export


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path)
    parser.add_argument('--output',type=Path,default=Path(__file__).with_name('query-pipeline.json'))
    parser.add_argument('--cases-output',type=Path)
    parser.add_argument('--repetitions',type=int,default=3)
    parser.add_argument('--oracle-database',type=Path)
    args=parser.parse_args()
    database=args.database or Path(__file__).with_name('synthetic-query.sqlite')
    if not args.database and not database.exists():
        synthetic_database(database)
    report,cases=run(database,args.repetitions,args.oracle_database)
    args.output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    if args.cases_output:
        args.cases_output.write_text(json.dumps(cases,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'output':str(args.output),'cases':len(report['cases']),'parity':True,
                     'largest_baseline_ms':max(x['baseline_median_ms'] for x in report['cases']),
                     'largest_optimized_ms':max(x['optimized_median_ms'] for x in report['cases'])}))


if __name__=='__main__':
    main()
