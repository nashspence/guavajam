#!/usr/bin/env python3
"""Synthetic bibliography-only sizing experiment; not production GuavaJam.
Run: python works_catalog.py OUTPUT_DIR. No body, document, passage, or history rows.
"""
import gzip, hashlib, json, random, shutil, sqlite3, statistics, sys, time
from datetime import date,timedelta
from pathlib import Path
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
words=('archive history ecology coastal estuary hydrology field observation evidence measurement salinity climate sediment biology river water ocean habitat forest plankton microbial isotope analysis survey season depth instrument calibration sample replicate temperature oxygen nitrogen carbon geology morphology transport velocity spatial temporal uncertainty distribution model comparison experiment inference station lagoon peat coral shelf boundary pressure carbonate adaptation diversity abundance migration chemistry atmosphere groundwater glacial marsh estuarine benthic pelagic method result discussion appendix transcript publication source record author language classification synthesis interpretation recovery custody preservation collection technical schema research public policy community').split()
SCHEMA='''PRAGMA foreign_keys=ON; CREATE TABLE works(id INTEGER PRIMARY KEY,repository TEXT NOT NULL UNIQUE,revision TEXT NOT NULL,title TEXT NOT NULL,authors TEXT NOT NULL,abstract TEXT NOT NULL,published TEXT NOT NULL,subjects TEXT NOT NULL,tags TEXT NOT NULL);
CREATE TABLE facets(work_id INTEGER NOT NULL REFERENCES works(id),kind TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(kind,value,work_id)) WITHOUT ROWID;
CREATE INDEX works_date ON works(published,id);
CREATE VIRTUAL TABLE work_fts USING fts5(title,authors,abstract,subjects,tags,content=works,content_rowid=id,tokenize='unicode61 remove_diacritics 2',detail=full,columnsize=1);'''
report={'python':sys.version,'sqlite':sqlite3.sqlite_version,'fixture':'synthetic 80-word latest abstracts, three tags and two subjects per work; no body text; external-content FTS5', 'sizes':[], 'checks':[]}
for n in (2,1000,10000):
 p=out/f'works-{n}.sqlite';p.unlink(missing_ok=True);db=sqlite3.connect(p);db.executescript(SCHEMA);rng=random.Random(113)
 with db:
  for i in range(1,n+1):
   tags=['tag'+str(i%53),'tag'+str((i+13)%53),'latesttag' if i==1 else 'general'];subjects=['subject'+str(i%41),'subject'+str((i+9)%41)]
   title='Latesttitle Riverhog' if i==1 else ' '.join(rng.choices(words,k=5))+f' {i}'
   author='Nashspence' if i==1 else f'Example Author {i%307}'
   abstract=' '.join(rng.choices(words,k=80)) + (' latestabstract' if i==1 else '')
   # Accepted publication order, deliberately NOT maximum claimed date.
   editions=[('oldtitle oldbodytoken','oldabstract',date(2026,11,25)),(title,abstract,date(2026,1,1)+timedelta(days=i%300))]
   latest=editions[-1];db.execute('INSERT INTO works VALUES(?,?,?,?,?,?,?,?,?)',(i,f'https://example.org/work-{i}.git',hashlib.sha1(f'{i}:latest'.encode()).hexdigest(),latest[0],author,latest[1],latest[2].isoformat(),' '.join(subjects),' '.join(tags)))
   db.executemany('INSERT INTO facets VALUES(?,?,?)',[(i,'tag',t) for t in tags]+[(i,'subject',s) for s in subjects])
  db.execute('INSERT INTO work_fts(rowid,title,authors,abstract,subjects,tags) SELECT id,title,authors,abstract,subjects,tags FROM works');db.execute("INSERT INTO work_fts(work_fts) VALUES('optimize')");db.execute("INSERT INTO work_fts(work_fts,rank) VALUES('integrity-check',1)")
 db.execute('VACUUM');assert db.execute('PRAGMA integrity_check').fetchone()==('ok',);assert not db.execute('PRAGMA foreign_key_check').fetchall()
 def count(term):return db.execute('SELECT count(*) FROM work_fts WHERE work_fts MATCH ?',(term,)).fetchone()[0]
 for term,expected in [('latesttitle',1),('Nashspence',1),('latestabstract',1),('latesttag',1),('oldtitle',0),('oldabstract',0),('oldbodytoken',0),('bodyonlytoken',0),('"subject1"',None)]:
  got=count(term);assert expected is None or got==expected,(term,got,expected);report['checks'].append({'works':n,'term':term,'count':got})
 assert db.execute("SELECT w.id FROM works w JOIN facets f ON f.work_id=w.id WHERE f.kind=? AND f.value=? AND w.published BETWEEN ? AND ?",('tag','latesttag','2026-01-01','2026-01-31')).fetchall()==[(1,)]
 assert db.execute("SELECT count(*) FROM facets WHERE kind='tag' AND value='LATESTTAG'").fetchone()==(0,)
 assert db.execute('SELECT count(*) FROM works').fetchone()==(n,)
 times=[]
 for _ in range(5):
  t=time.perf_counter();db.execute('SELECT count(*) FROM work_fts WHERE work_fts MATCH ?',('archive',)).fetchone();db.execute('SELECT w.title,w.revision FROM work_fts JOIN works w ON w.id=work_fts.rowid WHERE work_fts MATCH ? ORDER BY bm25(work_fts),w.id LIMIT 20',('archive',)).fetchall();times.append((time.perf_counter()-t)*1000)
 db.close();z=p.with_suffix('.sqlite.gz')
 with p.open('rb') as f,z.open('wb') as raw,gzip.GzipFile(fileobj=raw,mode='wb',filename='',mtime=0,compresslevel=9) as g:shutil.copyfileobj(f,g)
 report['sizes'].append({'works':n,'sqlite_bytes':p.stat().st_size,'gzip_bytes':z.stat().st_size,'native_warm_count_and_first_page_ms':round(statistics.median(times[1:]),3),'db_sha256':hashlib.file_digest(p.open('rb'),'sha256').hexdigest()})
(out/'works-catalog-results.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report['sizes'],indent=2))
