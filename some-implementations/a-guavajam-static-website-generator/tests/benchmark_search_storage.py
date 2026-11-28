"""Measure current physical scopes and an equivalent materialized FTS alternative."""
from __future__ import annotations

import argparse
from contextlib import closing
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import statistics
import time

CORPORA = {
    'collection': ('work_fts', 'works', ('title','authors','abstract','labels'), '4,2,1,2'),
    'versions': ('version_fts', 'editions', ('name','title','message'), '4,2,1'),
    'documents': ('document_fts', 'documents', ('title','abstract'), '4,1'),
    'passages': ('passage_fts', 'passage_content', ('title','authors','abstract','labels','body','git'), '4,2,1,2,1,0.5'),
    'history': ('passage_fts', 'passage_content', ('title','authors','abstract','labels','body','git'), '4,2,1,2,1,0.5'),
}
VARIANTS = ('production', 'materialized')
TERMS = ('"current"*', '"supporting"*', '"observ"*', '"evidence"*', '"field"*', '"cafe"*',
         '"cafe"* AND "bathymetric"*', '"palaeotidal"*', '"palaeoriver"*',
         '"quotient"*', '"ecol"* AND "estuar"*', '"李"*', '"zzmissingtoken"*')


def identity(connection):
    metadata = dict(connection.execute('SELECT key,value FROM metadata'))
    if metadata.get('schema') != 'guavajam.search.v1' or metadata.get('scope') not in CORPORA:
        raise ValueError('A current physically scoped GuavaJam search database is required')
    return metadata['scope'], CORPORA[metadata['scope']]


def layout(source, target, variant):
    if variant not in VARIANTS:
        raise ValueError('Unknown storage variant')
    with closing(sqlite3.connect(source)) as original, closing(sqlite3.connect(target)) as db:
        original.backup(db)
        _, (table, content, fields, _) = identity(db)
        if variant == 'materialized':
            # Preserve every row, field, token position and corpus statistic.
            # Only the FTS text storage changes; display/citation sources stay.
            name = 'measured_fts'
            db.execute('CREATE VIRTUAL TABLE ' + name + ' USING fts5(' + ','.join(fields) +
                       ",detail=full,columnsize=1,tokenize='unicode61 remove_diacritics 2')")
            db.execute('INSERT INTO ' + name + '(rowid,' + ','.join(fields) + ') SELECT id,' +
                       ','.join(fields) + ' FROM ' + content + ' ORDER BY id')
            db.execute('DROP TABLE ' + table)
            db.execute('ALTER TABLE ' + name + ' RENAME TO ' + table)
            db.execute('INSERT INTO ' + table + '(' + table + ") VALUES ('optimize')")
        db.commit()
        db.execute('VACUUM')
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()


def readings(path, *, repeats=7):
    """Stream complete native-oracle fingerprints; retain at most one page."""
    results = {}
    with closing(sqlite3.connect(path)) as db:
        _, (table, _, _, weights) = identity(db)
        for term in TERMS:
            sql = 'SELECT rowid AS id,bm25(' + table + ',' + weights + ') AS score FROM ' + table
            sql += ' WHERE ' + table + ' MATCH ? ORDER BY score,id'
            count_sql = 'SELECT count(*) FROM ' + table + ' WHERE ' + table + ' MATCH ?'
            digest, first, total = hashlib.sha256(), [], 0
            for row in db.execute(sql, (term,)):
                digest.update(json.dumps(row,separators=(',',':')).encode() + b'\n')
                if len(first) < 20:
                    first.append(dict(zip(('id','score'),row)))
                total += 1
            times = []
            for repeat in range(repeats + 2):
                started = time.perf_counter()
                assert db.execute(count_sql,(term,)).fetchone()[0] == total
                db.execute(sql + ' LIMIT 20',(term,)).fetchall()
                if repeat >= 2:
                    times.append((time.perf_counter() - started) * 1000)
            results[term] = {'sha256':digest.hexdigest(),'matches':total,'expected':first,
                            'sql':sql + ' LIMIT 20','countSql':count_sql,'params':[term],'table':table,
                            'median_ms':statistics.median(times),
                            'p95_ms':sorted(times)[min(len(times)-1,int(.95*len(times)))]}
    return results


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def benchmark(source, directory, *, variants=VARIANTS, repeats=7):
    directory.mkdir(parents=True,exist_ok=True)
    expected = readings(source,repeats=1)
    with closing(sqlite3.connect(source)) as db:
        scope, _ = identity(db)
        names = {row[0] for row in db.execute('SELECT name FROM sqlite_schema')}
        counts = {name: db.execute('SELECT count(*) FROM '+name).fetchone()[0]
                  for name in ('works','editions','documents','passages') if name in names}
    report, cases = [], []
    for variant in variants:
        target = directory / (variant + '.sqlite')
        started = time.perf_counter()
        layout(source,target,variant)
        build_ms = (time.perf_counter() - started) * 1000
        found = readings(target,repeats=repeats)
        failures = [term for term in TERMS if any(found[term][key] != expected[term][key]
                    for key in ('sha256','matches'))]
        gzip_results = {}
        for level in (1,6,9):
            zipped = target.with_suffix('.sqlite.gz')
            started = time.perf_counter()
            with target.open('rb') as raw, zipped.open('wb') as output:
                with gzip.GzipFile(fileobj=output,filename='',mtime=0,mode='wb',compresslevel=level) as sink:
                    shutil.copyfileobj(raw,sink,length=1024**2)
            gzip_results[str(level)] = {'bytes':zipped.stat().st_size,
                                       'ms':(time.perf_counter()-started)*1000}
        report.append({'variant':variant,'scope':scope,'raw_bytes':target.stat().st_size,
                       'sha256':file_hash(target),'build_ms':build_ms,'gzip':gzip_results,
                       'ranking_equivalent':not failures,'failed_queries':failures,'counts':counts,
                       'queries':{term:{key:value for key,value in result.items()
                         if key in {'sha256','matches','median_ms','p95_ms'}} for term,result in found.items()}})
        cases.append({'fixture':directory.name,'name':variant,'scope':scope,'path':str(target.absolute()),
                      'gzipPath':str(zipped.absolute()),'rawBytes':target.stat().st_size,
                      'rankingEquivalent':not failures,'queries':[
                         {'name':term,'table':result['table'],'sql':result['sql'],'params':result['params'],
                          'countSql':result['countSql'],'countExpected':result['matches'],
                          'expected':result['expected']} for term,result in found.items()]})
    return {'fixture':directory.name,'sqlite_version':sqlite3.sqlite_version,'cases':report},cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',action='append',required=True,metavar='NAME=PATH')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--repeats',type=int,default=7)
    parser.add_argument('--variants',default=','.join(VARIANTS))
    args = parser.parse_args()
    if not 1 <= args.repeats <= 100:
        parser.error('repeats must be between 1 and 100')
    variants = args.variants.split(',')
    if not set(variants) <= set(VARIANTS):
        parser.error('unknown current storage variant')
    args.output.mkdir(parents=True,exist_ok=True)
    reports,cases = [],[]
    for value in args.database:
        name,path = value.split('=',1)
        if not name or Path(name).name != name or name in {'.','..'}:
            parser.error('fixture names must be single safe path components')
        report,browser = benchmark(Path(path),args.output/name,variants=variants,repeats=args.repeats)
        reports.append(report);cases.extend(browser)
        (args.output/'storage-report.json').write_text(json.dumps(reports,indent=2)+'\n')
        (args.output/'browser-manifest.json').write_text(json.dumps({'cases':cases},indent=2)+'\n')
    return int(any(not case['rankingEquivalent'] for case in cases))


if __name__ == '__main__':
    raise SystemExit(main())
