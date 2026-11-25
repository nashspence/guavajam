#!/usr/bin/env python3
"""Scoped index prototype using #113's retained NAV text. Python + SQLite FTS5 only.
Five editions are modeled, not observed releases. Sentinel documents/work are synthetic.
No signed-site build, renderer, production worker or browser query qualification.
"""
import gzip,hashlib,json,random,sqlite3,sys,time,statistics,shutil
from pathlib import Path
H=lambda s:hashlib.sha256(s.encode()).hexdigest()
corpus=Path(sys.argv[1]); out=Path(sys.argv[2]); out.mkdir(parents=True,exist_ok=True)
docs=[json.loads(line) for line in gzip.open(corpus,'rt')]
rng=random.Random(729); changed={}; plans={1:{}}
for n in range(2,6):
    changed.update((d['id'],n) for d in rng.sample(docs,round(len(docs)*.05)));plans[n]=dict(changed)
report={'python':sys.version,'sqlite':sqlite3.sqlite_version,'corpus_sha256':hashlib.file_digest(corpus.open('rb'),'sha256').hexdigest(),'source_documents':len(docs),'layouts':{},'checks':[]}
def save(): (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
def fixture(work,n):
    if work=='Other':
        yield 'content/manuscript.md','Other manuscript','',{},[('witness','Other','otherworktoken' if n==5 else 'otheroldtoken')];return
    for d in docs:
        meta=dict(d['metadata']);meta['Example version']=f'v{n}'
        anchors=[(a['anchor'],a['title'],a['text']) for a in d['navigation']['anchors']]
        a,_,b=anchors[0];b=b.replace('Example version v1',f'Example version v{n}');anchors[0]=(a,b[:160],b)
        if d['id'] in plans[n]:
            a,t,b=anchors[-1];b+=f' Modeled observation {plans[n][d["id"]]}.';anchors[-1]=(a,b[:160],b)
        yield d['path'],d['title'],d['abstract'],meta,anchors
    for path,title,abstract,body in [('manuscript.md','Manuscript','','oldonlytoken' if n==1 else 'latestonlytoken'),('supporting/witness.md','Shared witness','Facetexample','sharedbodytoken identical across editions'),('supporting/title.md','titleonlytoken','abstractonlytoken','bodyonlytoken')]:
        yield 'content/'+path,title,abstract,{'Example version':f'v{n}','Exact':['Café','01','1']},[('witness','Witness',body)]
SCHEMA='''CREATE TABLE editions(id INTEGER PRIMARY KEY,work TEXT,position INTEGER,revision TEXT,name TEXT,title TEXT,message TEXT,day TEXT);
CREATE TABLE documents(id INTEGER PRIMARY KEY,edition_id INTEGER REFERENCES editions(id),path TEXT,title TEXT,abstract TEXT,body_id INTEGER REFERENCES texts(id));
CREATE TABLE texts(id INTEGER PRIMARY KEY,digest TEXT UNIQUE,body TEXT);
CREATE TABLE passages(id INTEGER PRIMARY KEY,document_id INTEGER REFERENCES documents(id),anchor TEXT,position INTEGER,title TEXT,body_id INTEGER REFERENCES texts(id));
CREATE TABLE facets(document_id INTEGER REFERENCES documents(id),name TEXT COLLATE BINARY,value TEXT COLLATE BINARY,PRIMARY KEY(document_id,name,value)) WITHOUT ROWID;
CREATE INDEX facet_lookup ON facets(name,value,document_id);
CREATE INDEX doc_edition ON documents(edition_id,path);
CREATE INDEX passage_document ON passages(document_id,position);
CREATE VIEW doc_fields AS SELECT d.id,e.title||' — '||d.title AS title,'Example authors' AS authors,'Reference abstract' AS abstract,'contract' AS labels,t.body,e.work||' '||e.revision||' '||e.day||' '||e.message||' '||d.path||' '||e.name AS git FROM documents d JOIN editions e ON e.id=d.edition_id JOIN texts t ON t.id=d.body_id;
CREATE VIEW passage_fields AS SELECT p.id,e.title||' — '||d.title||' — '||p.title AS title,'Example authors' AS authors,'Reference abstract' AS abstract,'contract' AS labels,t.body,e.work||' '||e.revision||' '||e.day||' '||e.message||' '||d.path||' '||e.name||' '||p.anchor AS git FROM passages p JOIN documents d ON d.id=p.document_id JOIN editions e ON e.id=d.edition_id JOIN texts t ON t.id=p.body_id;'''
FIELDS='title,authors,abstract,labels,body,git'
def build(name,scope,selected):
    start=time.perf_counter();path=out/(name+'.sqlite');path.unlink(missing_ok=True);db=sqlite3.connect(path);db.executescript('PRAGMA page_size=4096;PRAGMA foreign_keys=ON;PRAGMA synchronous=OFF;'+SCHEMA);pool={}
    def intern(b):
        h=H(b)
        if h not in pool:pool[h]=db.execute('INSERT INTO texts(digest,body) VALUES(?,?)',(h,b)).lastrowid
        return pool[h]
    def fts(name,view,cols):
        db.execute(f"CREATE VIRTUAL TABLE {name} USING fts5({cols},content='{view}',content_rowid='id',detail=full,columnsize=1,tokenize='unicode61 remove_diacritics 2')")
        db.execute(f'INSERT INTO {name}(rowid,{cols}) SELECT id,{cols} FROM {view}')
        db.execute(f"INSERT INTO {name}({name}) VALUES('optimize')");db.execute(f"INSERT INTO {name}({name},rank) VALUES('integrity-check',1)")
    for work,n in selected:
        eid=db.execute('INSERT INTO editions(work,position,revision,name,title,message,day) VALUES(?,?,?,?,?,?,?)',(work,n,hashlib.sha1(f'{work}:{n}'.encode()).hexdigest(),f'v{n}',work+' work','messageonlytoken' if n==1 else f'Modeled update {n}',f'2026-01-{28-n%27:02d}')).lastrowid
        if scope=='versions':continue
        for pathstr,title,abstract,meta,anchors in fixture(work,n):
            if scope=='documents' and pathstr=='content/manuscript.md':continue
            did=db.execute('INSERT INTO documents(edition_id,path,title,abstract,body_id) VALUES(?,?,?,?,?)',(eid,pathstr,title,abstract,intern(' '.join([title]+[a[2] for a in anchors])) if scope=='works' else None)).lastrowid
            db.executemany('INSERT OR IGNORE INTO facets VALUES(?,?,?)',((did,k,v) for k,vs in meta.items() for v in (vs if isinstance(vs,list) else [vs])))
            if scope!='documents':db.executemany('INSERT INTO passages(document_id,anchor,position,title,body_id) VALUES(?,?,?,?,?)',((did,a,i,t,intern(b)) for i,(a,t,b) in enumerate(anchors)))
    if scope=='versions':fts('version_fts','editions','name,title,message')
    elif scope=='documents':fts('document_fts','documents','title,abstract')
    elif scope=='works':
        fts('search_fts','doc_fields',FIELDS);db.execute('CREATE VIEW evidence_fields AS SELECT p.id,t.body FROM passages p JOIN texts t ON t.id=p.body_id');fts('evidence_fts','evidence_fields','body')
    else:fts('passage_fts','passage_fields',FIELDS)
    db.commit();db.execute('VACUUM');assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok';assert not db.execute('PRAGMA foreign_key_check').fetchall()
    result={'scope':scope,'raw':path.stat().st_size,'rows':{t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('editions','documents','passages','texts')},'build_s':round(time.perf_counter()-start,3),'sha256':hashlib.file_digest(path.open('rb'),'sha256').hexdigest()};db.close()
    with path.open('rb') as f,path.with_suffix('.gz').open('wb') as raw,gzip.GzipFile(fileobj=raw,mode='wb',filename='',mtime=0,compresslevel=9) as gz:shutil.copyfileobj(f,gz,1<<20)
    result['gzip']=path.with_suffix('.gz').stat().st_size;report['layouts'][name]=result;save();print(name,result,flush=True)

def query(db,scope,term,facet=None,offset=0):
    match=' AND '.join('"'+s+'"*' for s in term.split());table={'versions':'version_fts','documents':'document_fts','works':'search_fts','passages':'passage_fts','history':'passage_fts'}[scope]
    if scope=='versions':join=f'{table} JOIN editions e ON e.id={table}.rowid'; ident='e.id'; fields='e.work,e.position,e.name,e.message';weights='4,2,1'
    elif scope in ('documents','works'):join=f'{table} JOIN documents d ON d.id={table}.rowid JOIN editions e ON e.id=d.edition_id';ident='d.id';fields='e.work,e.position,d.path,d.title';weights='4,1' if scope=='documents' else '4,2,1,2,1,0.5'
    else:join=f'{table} JOIN passages p ON p.id={table}.rowid JOIN documents d ON d.id=p.document_id JOIN editions e ON e.id=d.edition_id';ident='p.id';fields='e.work,e.position,d.path,p.anchor';weights='4,2,1,2,1,0.5'
    pred=f'{table} MATCH ?';bind=[match]
    for key,vals in (facet or {}).items():
        pred+=' AND EXISTS (SELECT 1 FROM facets f WHERE f.document_id=d.id AND f.name=? AND f.value IN ('+','.join('?' for _ in vals)+'))';bind.extend([key,*vals])
    hits=f'SELECT {ident} AS id,{fields},bm25({table},{weights}) AS score FROM {join} WHERE {pred}'
    count=db.execute(f'SELECT count({"DISTINCT e.work" if scope=="works" else "*"}) FROM {join} WHERE {pred}',bind).fetchone()[0]
    sql=f'WITH hits AS MATERIALIZED ({hits}),ranked AS (SELECT *,row_number() OVER(PARTITION BY work ORDER BY score,id) rn FROM hits) SELECT * FROM ranked WHERE rn=1 ORDER BY score,work,id LIMIT 20 OFFSET ?' if scope=='works' else hits+' ORDER BY score,id LIMIT 20 OFFSET ?'
    return count,db.execute(sql,[*bind,offset]).fetchall()
def test(name,term,expected=None,facet=None):
    scope=report['layouts'][name]['scope'];times=[]
    with sqlite3.connect(out/(name+'.sqlite')) as db:
        for _ in range(4):
            start=time.perf_counter();count,rows=query(db,scope,term,facet);times.append((time.perf_counter()-start)*1000)
    if expected is not None:assert count==expected,(name,term,count,expected)
    r={'file':name,'term':term,'facet':facet,'count':count,'page':len(rows),'median_warm_ms':round(statistics.median(times[1:]),3),'first':rows[:1]};report['checks'].append(r);return rows
for n in (5,50,10000):build(f'versions-{n}','versions',[('Riverhog',i) for i in range(1,n+1)])
for n in (1,5):
    for scope in ('documents','passages','works'):
        build(f'{scope}-{n}',scope,[('Riverhog',n)]+([('Other',n)] if scope=='works' else []))
build('history-5','history',[('Riverhog',i) for i in range(1,6)])
for n in (5,50,10000):
    for term,num in [('messageonlytoken',1),('oldonlytoken',0),('bodyonlytoken',0),('v1',None)]:test(f'versions-{n}',term,num)
for n in (1,5):
    for term,num in [('titleonlytoken',1),('abstractonlytoken',1),('bodyonlytoken',0),('contract',None)]:test(f'documents-{n}',term,num)
    test(f'documents-{n}','titleonlytoken',1,{'Exact':['01','missing']});test(f'documents-{n}','titleonlytoken',0,{'Exact':['café']});test(f'documents-{n}','titleonlytoken',0,{'Exact':['01'],'Example version':['v9']})
for name,terms in {'passages-1':[('oldonlytoken',1),('latestonlytoken',0)],'passages-5':[('oldonlytoken',0),('latestonlytoken',1),('otherworktoken',0),('sharedbodytoken',1),('contract',None),('transaction',None)],'works-1':[('oldonlytoken',1)],'works-5':[('oldonlytoken',0),('latestonlytoken',1),('otherworktoken',1),('otheroldtoken',0),('contract',None)],'history-5':[('oldonlytoken',1),('latestonlytoken',4),('otherworktoken',0),('sharedbodytoken',5),('contract',None),('transaction',None)]}.items():
    for term,num in terms:test(name,term,num)
rows=test('history-5','sharedbodytoken',5);assert {r[2] for r in rows}==set(range(1,6));assert len({tuple(r[1:5]) for r in rows})==5
h=test('history-5','sharedbodytoken',1,{'Example version':['v5']})[0];e=test('passages-5','sharedbodytoken',1)[0];assert h[1:-1]==e[1:-1];report['ranking_scope_example']={'occurrence':e[1:-1],'edition_score':e[-1],'history_score':h[-1]};test('history-5','sharedbodytoken',2,{'Example version':['v1','v5']});test('passages-5','sharedbodytoken',0,{'Example version':['v1']})
build('passages-1-rebuilt','passages',[('Riverhog',1)]);assert report['layouts']['passages-1']['sha256']==report['layouts']['passages-1-rebuilt']['sha256']
report['result']='passed';save();print('PASS',len(report['checks']),flush=True)
