"""Source-exact bounded evidence windows for unusually large FTS body fields.

This intentionally changes excerpt selection, never primary membership/ranking.
It adds an external-content FTS index to a copy of the normalized prototype,
reusing its text_pool. Oversized unbroken lexical spans explicitly retain the
original full-body snippet path. No source text or publication anchor is added.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
import gzip
import hashlib
import json
import re
import shutil
import sqlite3
import statistics
import sys
import time
from pathlib import Path

from query_pipeline import literal_query

MAX_BYTES = 8192
OVERLAP_BYTES = 1024
SAFE_BREAK = re.compile(rb'[\x00-\x20\x21-\x2f\x3a-\x40\x5b-\x60\x7b-\x7e]')


def windows(value,maximum=MAX_BYTES,overlap=OVERLAP_BYTES):
    """Return overlapping UTF-8 ranges; never split at an invented token edge.

    Every ASCII non-alphanumeric character is a separator for the unchanged
    default unicode61 tokenizer. Unicode word boundaries need not be guessed.
    If no safe boundary fits, report an explicit full-body fallback.
    """
    raw=value.encode('utf-8')
    if not 0<=overlap<maximum:
        raise ValueError('Invalid window budget')
    boundaries=[0,*[m.end() for m in SAFE_BREAK.finditer(raw)],len(raw)]
    boundaries=sorted(set(boundaries))
    result=[];start=0
    while start<len(raw):
        end=min(len(raw),start+maximum)
        if end<len(raw):
            end=boundaries[bisect_right(boundaries,end)-1]
        if end<=start:
            return [],'unbroken lexical span exceeds evidence-window byte budget'
        part=raw[start:end].decode('utf-8')
        result.append((start,end-start,part))
        if end==len(raw):
            break
        next_start=boundaries[bisect_left(boundaries,end-overlap)]
        start=next_start if next_start>start else end
    assert not result or (result[0][0]==0 and result[-1][0]+result[-1][1]==len(raw))
    for index,(start,size,text) in enumerate(result):
        assert size<=maximum and raw[start:start+size]==text.encode('utf-8')
        if index:
            previous=result[index-1]
            assert start<=previous[0]+previous[1]
    return result,None


def add_windows(db,maximum=MAX_BYTES,overlap=OVERLAP_BYTES):
    started=time.perf_counter()
    db.executescript('''
      CREATE TABLE evidence_bodies(body_id INTEGER PRIMARY KEY REFERENCES text_pool(id),
        body_bytes INTEGER NOT NULL,window_count INTEGER NOT NULL,fallback_reason TEXT);
      CREATE TABLE evidence_windows(id INTEGER PRIMARY KEY,body_id INTEGER NOT NULL REFERENCES text_pool(id),
        ordinal INTEGER NOT NULL,start_byte INTEGER NOT NULL,byte_count INTEGER NOT NULL,
        UNIQUE(body_id,ordinal));
      CREATE VIEW evidence_fields AS SELECT w.id,
        CAST(substr(CAST(t.value AS BLOB),w.start_byte+1,w.byte_count) AS TEXT) AS body
        FROM evidence_windows w JOIN text_pool t ON t.id=w.body_id;
      CREATE VIRTUAL TABLE evidence_fts USING fts5(body,content='evidence_fields',content_rowid='id',
        detail=full,columnsize=1,tokenize='unicode61 remove_diacritics 2');
    ''')
    count=payload=0;fallbacks=[]
    for body_id,value in db.execute('SELECT id,value FROM text_pool WHERE length(CAST(value AS BLOB))>? ORDER BY id',(maximum,)):
        parts,fallback=windows(value,maximum,overlap)
        db.execute('INSERT INTO evidence_bodies VALUES (?,?,?,?)',(body_id,len(value.encode()),len(parts),fallback))
        if fallback:
            fallbacks.append({'body_id':body_id,'reason':fallback});continue
        for ordinal,(start,size,text) in enumerate(parts):
            wid=db.execute('INSERT INTO evidence_windows(body_id,ordinal,start_byte,byte_count) VALUES (?,?,?,?)',
                           (body_id,ordinal,start,size)).lastrowid
            db.execute('INSERT INTO evidence_fts(rowid,body) VALUES (?,?)',(wid,text))
            count+=1;payload+=size
    db.execute("INSERT INTO evidence_fts(evidence_fts) VALUES ('optimize')")
    db.execute("INSERT INTO evidence_fts(evidence_fts,rank) VALUES ('integrity-check',1)")
    db.commit()
    return {'maximum_window_bytes':maximum,'maximum_overlap_bytes':overlap,'windows':count,
            'indexed_window_bytes_with_overlap':payload,'oversized_lexical_fallbacks':fallbacks,
            'large_distinct_bodies':db.execute('SELECT count(*) FROM evidence_bodies').fetchone()[0],
            'large_distinct_body_bytes':db.execute('SELECT coalesce(sum(body_bytes),0) FROM evidence_bodies').fetchone()[0],
            'build_seconds':time.perf_counter()-started}


def window_query(term,body_id):
    match=literal_query(term).replace(' AND ',' OR ')
    sql='''WITH hits AS MATERIALIZED (
      SELECT w.id,w.ordinal,bm25(evidence_fts) AS score FROM evidence_fts
        JOIN evidence_windows w ON w.id=evidence_fts.rowid
        WHERE evidence_fts MATCH ? AND w.body_id=?
    ), chosen AS (SELECT id FROM hits ORDER BY score,ordinal,id LIMIT 1)
    SELECT w.body_id,w.start_byte,w.byte_count,
      snippet(evidence_fts,0,char(57344),char(57345),'…',24) AS excerpt
      FROM evidence_fts JOIN evidence_windows w ON w.id=evidence_fts.rowid
      WHERE evidence_fts MATCH ? AND evidence_fts.rowid IN (SELECT id FROM chosen)'''
    return {'sql':sql,'bind':[match,body_id,match]}


def full_query(table,term,row_id):
    return {'sql':f"SELECT snippet({table},4,char(57344),char(57345),'…',24) AS excerpt FROM {table} WHERE {table} MATCH ? AND rowid=?",
            'bind':[literal_query(term),row_id]}


def execute(db,q):
    return [dict(r) for r in db.execute(q['sql'],q['bind'])]


def measure(db,q,repetitions=3):
    rows=execute(db,q)
    times=[]
    for _ in range(repetitions):
        started=time.perf_counter();assert execute(db,q)==rows
        times.append((time.perf_counter()-started)*1000)
    return rows,statistics.median(times)


def verify_excerpt(db,row):
    assert '\ue000' in row['excerpt'] and '\ue001' in row['excerpt']
    source=db.execute('SELECT value FROM text_pool WHERE id=?',(row['body_id'],)).fetchone()[0].encode('utf-8')
    window=source[row['start_byte']:row['start_byte']+row['byte_count']].decode('utf-8')
    plain=row['excerpt'].replace('\ue000','').replace('\ue001','').removeprefix('…').removesuffix('…')
    assert plain in window,(plain,window)
    return {'source_exact_window':True,'highlighted_source_excerpt':True,'window_start_byte':row['start_byte'],
            'window_bytes':row['byte_count'],'excerpt':row['excerpt']}


def compress(path):
    target=path.with_suffix(path.suffix+'.gz')
    with path.open('rb') as src,target.open('wb') as raw,gzip.GzipFile(fileobj=raw,mode='wb',filename='',mtime=0,compresslevel=9) as dst:
        shutil.copyfileobj(src,dst,1024*1024)
    return {'bytes':target.stat().st_size,'sha256':hashlib.file_digest(target.open('rb'),'sha256').hexdigest()}


def real_cases(source,candidate,cases_path):
    before=sqlite3.connect(f'file:{source.absolute()}?mode=ro',uri=True);before.row_factory=sqlite3.Row
    after=sqlite3.connect(f'file:{candidate.absolute()}?mode=ro',uri=True);after.row_factory=sqlite3.Row
    selected=json.loads(cases_path.read_text())[-2:]
    reports=[];exports=[]
    for case in selected:
        target=case['expected_rows'][0]
        passage=case['kind']=='passages'
        table,mapping=('passage_fts','passage_bodies') if passage else ('search_fts','document_bodies')
        record_id=target['id'] if passage else before.execute('SELECT id FROM search_records WHERE document_id=?',(target['match_document_id'],)).fetchone()[0]
        body_id=after.execute(f'SELECT body_id FROM {mapping} WHERE record_id=?',(record_id,)).fetchone()[0]
        all_fields=', '.join(f'{table}.{field}' for field in ('title','authors','abstract','labels','body','git'))
        identity={'sql':f'SELECT rowid,bm25({table},4.0,2.0,1.0,2.0,1.0,0.5) AS score,{all_fields} FROM {table} WHERE {table} MATCH ? AND rowid=?',
                  'bind':[literal_query(case['term']),record_id]}
        assert execute(before,identity)==execute(after,identity)
        # Original page output, including exact citation context, is unchanged.
        assert execute(before,case['optimized_page'])==execute(after,case['optimized_page'])
        full=full_query(table,case['term'],record_id);chunk=window_query(case['term'],body_id)
        full_rows,full_ms=measure(after,full)
        chunk_rows,chunk_ms=measure(after,chunk)
        assert len(chunk_rows)==1
        proof=verify_excerpt(after,chunk_rows[0])
        report={'kind':case['kind'],'term':case['term'],'document':case['document'],'record_id':record_id,'body_id':body_id,
                'body_bytes':after.execute('SELECT body_bytes FROM evidence_bodies WHERE body_id=?',(body_id,)).fetchone()[0],
                'original_membership_score_and_page_identity_unchanged':True,'full_body_snippet_median_ms':full_ms,
                'window_evidence_median_ms':chunk_ms,'timing_repetitions':3,**proof}
        reports.append(report)
        exports.append({'label':'large '+case['kind']+' evidence','kind':case['kind'],'term':case['term'],
                        'body_id':body_id,'record_id':record_id,'full_evidence':full,'window_evidence':chunk,
                        'expected_window_rows':chunk_rows,'original_page':case['optimized_page'],
                        'expected_original_page':case['expected_rows']})
    return reports,exports


def synthetic_cases():
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.executescript('''CREATE TABLE text_pool(id INTEGER PRIMARY KEY,value TEXT);
      CREATE VIRTUAL TABLE original USING fts5(title,authors,abstract,labels,body,git,
        tokenize='unicode61 remove_diacritics 2',detail=full,columnsize=1);''')
    fixtures=[('terms in different windows','alpha '+('ordinary '*2200)+'omega','alpha omega'),
              ('deep exact source hit',('padding '*6000)+'uniquedeepneedle '+('padding '*1000),'uniquedeepneedle'),
              ('Unicode source spelling',('context '*2200)+'Café '+('context '*1000),'cafe'),
              ('oversized lexical unit',('x'*20000)+' endneedle','endneedle')]
    for body_id,(_,body,_) in enumerate(fixtures,1):
        db.execute('INSERT INTO text_pool VALUES (?,?)',(body_id,body))
        db.execute('INSERT INTO original(rowid,title,authors,abstract,labels,body,git) VALUES (?,?,?,?,?,?,?)',
                   (body_id,'Synthetic case','Alice','','',body,'synthetic revision anchor'))
    details=add_windows(db)
    results=[]
    for body_id,(name,body,term) in enumerate(fixtures,1):
        matched=db.execute('SELECT rowid FROM original WHERE original MATCH ? AND rowid=?',(literal_query(term),body_id)).fetchall()
        assert len(matched)==1
        q=window_query(term,body_id);rows=execute(db,q)
        if name=='oversized lexical unit':
            assert not rows
            reason=db.execute('SELECT fallback_reason FROM evidence_bodies WHERE body_id=?',(body_id,)).fetchone()[0]
            assert reason
            original=execute(db,full_query('original',term,body_id))
            assert '\ue000endneedle\ue001' in original[0]['excerpt']
            results.append({'name':name,'full_occurrence_match':True,'explicit_original_snippet_fallback':True,'reason':reason})
        else:
            assert len(rows)==1
            proof=verify_excerpt(db,rows[0])
            if name=='terms in different windows':
                assert db.execute('SELECT count(*) FROM evidence_fts JOIN evidence_windows w ON w.id=evidence_fts.rowid WHERE evidence_fts MATCH ? AND w.body_id=?',
                                  (literal_query(term),body_id)).fetchone()[0]==0
            if name=='deep exact source hit':
                assert rows[0]['start_byte']>MAX_BYTES
            results.append({'name':name,'full_occurrence_match':True,**proof})
    # One term can belong to edition metadata while another belongs to the body.
    assert db.execute("SELECT count(*) FROM original WHERE original MATCH 'alpha AND Alice' AND rowid=1").fetchone()[0]==1
    mixed=execute(db,window_query('alpha Alice',1));assert mixed
    results.append({'name':'mixed author and body terms','full_occurrence_match':True,**verify_excerpt(db,mixed[0])})
    return {'build':details,'cases':results}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=Path('results/storage/latest/compact.sqlite'))
    parser.add_argument('--output',type=Path,default=Path('results/storage/latest/evidence.sqlite'))
    parser.add_argument('--cases',type=Path,default=Path('results/storage/latest/query-cases.json'))
    parser.add_argument('--report',type=Path,default=Path('results/storage/latest/evidence-windows.json'))
    parser.add_argument('--source-description',default='Native-heading exploratory corpus; benchmark edition and citation context modeled')
    args=parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    started=time.perf_counter()
    src=sqlite3.connect(f'file:{args.source.absolute()}?mode=ro',uri=True)
    dst=sqlite3.connect(args.output)
    src.backup(dst);src.close()
    build=add_windows(dst)
    dst.execute('VACUUM')
    assert dst.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    dst.close()
    output_gzip=compress(args.output)
    source_gzip_path=args.source.with_suffix(args.source.suffix+'.gz')
    source_gzip=source_gzip_path.stat().st_size if source_gzip_path.exists() else compress(args.source)['bytes']
    real,exports=real_cases(args.source,args.output,args.cases)
    report={'python':sys.version.split()[0],'sqlite':sqlite3.sqlite_version,
            'semantics':'Primary occurrence membership, ranking, field values and citation context unchanged; excerpt selection intentionally differs',
            'source':str(args.source),'source_description':args.source_description,'candidate':str(args.output),'build':build,
            'storage':{'source_raw_bytes':args.source.stat().st_size,'candidate_raw_bytes':args.output.stat().st_size,
                       'incremental_raw_bytes':args.output.stat().st_size-args.source.stat().st_size,
                       'source_gzip9_bytes':source_gzip,'candidate_gzip9_bytes':output_gzip['bytes'],
                       'incremental_gzip9_bytes':output_gzip['bytes']-source_gzip,'candidate_gzip9_sha256':output_gzip['sha256']},
            'real_cases':real,'synthetic':synthetic_cases(),'elapsed_seconds':time.perf_counter()-started,
            'limitations':['Single current corpus and native engine; qualify browser separately.',
                           'Safe ASCII boundary policy may fall back for long unbroken Unicode or ASCII text.',
                           'Original full-body FTS still determines all terms, rank, field matching and occurrence identity.',
                           'Window OR evidence proves at least one highlighted query term; it does not claim every AND term occurs in one window.']}
    args.report.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    args.report.with_name('evidence-browser-cases.json').write_text(json.dumps(exports,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'report':str(args.report),'build':build,'storage':report['storage'],
                      'timings':[{k:r[k] for k in ('kind','body_bytes','full_body_snippet_median_ms','window_evidence_median_ms')} for r in real]}))


if __name__=='__main__':
    main()
