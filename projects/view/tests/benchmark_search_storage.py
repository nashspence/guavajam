"""Compare SQLite layouts on identical baseline rows; timings are measurements.

Inputs are pre-optimization index.sqlite/work.sqlite artifacts (baseline commit
5b6e399). Its run_search_browser.py and run_work_search_browser.py --prepare
commands produce signed fixtures. --scale derives a seeded synthetic workload
from their schema; those rows are not claimed to be signed publications. Keep
generated databases/reports outside the repo.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import gzip
import hashlib
import io
import json
from pathlib import Path
import random
import shutil
import sqlite3
import statistics
import time

FIELDS = ('title', 'authors', 'abstract', 'labels', 'body', 'git')
WEIGHTS = '4.0,2.0,1.0,2.0,1.0,0.5'
VARIANTS = {
    'baseline': (False, False, 'full', 1),
    'pruned': (True, False, 'full', 1),
    'contentless_full': (True, True, 'full', 1),
    'external_column': (True, False, 'column', 1),
    'external_none': (True, False, 'none', 1),
    'contentless_column': (True, True, 'column', 1),
    'contentless_none': (True, True, 'none', 1),
    'contentless_no_sizes': (True, True, 'full', 0),
}
TERMS = [
    '"observ"*', '"evidence"*', '"field"*', '"cafe"*',
    '"cafe"* AND "bathymetric"*', '"palaeotidal"*', '"palaeoriver"*',
    '"glaciolacustrine"*', '"quotient"*', '"archiv"*',
    '"ecol"* AND "estuar"*', '"needle17"*', '"李"*',
    '"salinity"* AND "measurement"*', '"zzmissingtoken"*',
]


def compressed(raw, level):
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, filename='', mtime=0, mode='wb', compresslevel=level) as stream:
        stream.write(raw)
    return output.getvalue()


def layout(source, target, variant):
    """Rewrite storage only, retaining row IDs and unchanged search inputs."""
    prune, contentless, detail, columnsize = VARIANTS[variant]
    shutil.copyfile(source, target)
    with closing(sqlite3.connect(target)) as db:
        scope = db.execute("SELECT value FROM metadata WHERE key='scope'").fetchone()[0]
        db.execute('PRAGMA foreign_keys=OFF')
        if scope == 'collection' and prune:
            db.executescript('DROP TABLE passage_fts; DROP TABLE passage_records; DROP TABLE passages;')
        tables = [('search_fts', 'search_records')]
        if not (scope == 'collection' and prune):
            tables.append(('passage_fts', 'passage_records'))
        if contentless or detail != 'full' or columnsize != 1:
            for fts, records in tables:
                rows = db.execute('SELECT id,' + ','.join(FIELDS) + ' FROM ' + records + ' ORDER BY id').fetchall()
                db.execute('DROP TABLE ' + fts)
                content = "content=''" if contentless else "content='" + records + "',content_rowid='id'"
                db.execute('CREATE VIRTUAL TABLE ' + fts + ' USING fts5(' + ','.join(FIELDS) + ',' +
                           content + ",detail='" + detail + "',columnsize=" + str(columnsize) +
                           ",tokenize='unicode61 remove_diacritics 2')")
                db.executemany('INSERT INTO ' + fts + '(rowid,' + ','.join(FIELDS) + ') VALUES (?,?,?,?,?,?,?)', rows)
                db.execute('INSERT INTO ' + fts + '(' + fts + ") VALUES ('optimize')")
                if contentless:
                    # Presentation reads these mappings, never the duplicate
                    # plaintext. Full positions and doc sizes remain in FTS5.
                    db.executescript('CREATE TABLE thin_' + records + ''' (
                        id INTEGER PRIMARY KEY, work_id INTEGER NOT NULL REFERENCES works(id),
                        edition_id INTEGER REFERENCES editions(id), document_id INTEGER REFERENCES documents(id),
                        url TEXT NOT NULL);
                        INSERT INTO thin_''' + records + '(id,work_id,edition_id,document_id,url) '
                        'SELECT id,work_id,edition_id,document_id,url FROM ' + records + ';'
                        'DROP TABLE ' + records + '; ALTER TABLE thin_' + records + ' RENAME TO ' + records + ';')
                    if records == 'search_records':
                        db.execute('CREATE INDEX search_by_work ON search_records(work_id)')
            if contentless and any(name == 'passage_fts' for name, _ in tables):
                db.execute('ALTER TABLE passages DROP COLUMN text')
        db.commit()
        db.execute('VACUUM')
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()


def query(fts, term, *, limit=None):
    records = 'search_records' if fts == 'search_fts' else 'passage_records'
    join = ' FROM ' + fts + ' JOIN ' + records + ' s ON s.id=' + fts + '.rowid WHERE ' + fts + ' MATCH ?'
    sql = 'SELECT s.id,s.url,bm25(' + fts + ',' + WEIGHTS + ') AS score' + join + ' ORDER BY score,s.id'
    if limit:
        sql += ' LIMIT ' + str(limit)
    return sql, 'SELECT COUNT(*)' + join


def readings(path, *, repeats=7):
    """Compare full matching rows and scores; time the paged query plus count."""
    output = {}
    with closing(sqlite3.connect(path)) as db:
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for fts in ('search_fts', 'passage_fts'):
            if fts not in names:
                continue
            for term in TERMS:
                full, count = query(fts, term)
                sql, _ = query(fts, term, limit=20)
                rows = db.execute(full, (term,)).fetchall()
                times = []
                for repeat in range(repeats + 2):
                    started = time.perf_counter()
                    db.execute(count, (term,)).fetchone()
                    db.execute(sql, (term,)).fetchall()
                    if repeat >= 2:
                        times.append((time.perf_counter() - started) * 1000)
                output[fts + ':' + term] = {'rows': rows, 'sql': sql, 'countSql': count, 'params': [term],
                    'median_ms': statistics.median(times), 'p95_ms': sorted(times)[min(len(times)-1, int(.95*len(times)))],
                    'table': fts}
    return output


def same_results(baseline, candidate):
    failures = []
    for key, found in candidate.items():
        expected = baseline[key]['rows']
        actual = found['rows']
        if actual != expected:
            failures.append(key)
    return failures


def benchmark(source, directory, *, variants=tuple(VARIANTS), repeats=7):
    directory.mkdir(parents=True, exist_ok=True)
    baseline = readings(source, repeats=1)
    report, cases = [], []
    with closing(sqlite3.connect(source)) as db:
        scope = db.execute("SELECT value FROM metadata WHERE key='scope'").fetchone()[0]
        counts = {table: db.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                  for table in ('works', 'editions', 'documents', 'passages')}
    for variant in variants:
        target = directory / (variant + '.sqlite')
        started = time.perf_counter()
        layout(source, target, variant)
        build_ms = (time.perf_counter() - started) * 1000
        measured = readings(target, repeats=repeats)
        failures = same_results(baseline, measured)
        raw = target.read_bytes()
        gzip_results = {}
        for level in (1, 6, 9):
            times = []
            for repeat in range(3):
                started = time.perf_counter()
                data = compressed(raw, level)
                times.append((time.perf_counter() - started) * 1000)
            gzip_results[str(level)] = {'bytes': len(data), 'median_ms': statistics.median(times)}
            if level == 9:
                target.with_suffix('.sqlite.gz').write_bytes(data)
        with closing(sqlite3.connect(target)) as db:
            try:
                sizes = dict(db.execute('SELECT name,SUM(pgsize) FROM dbstat GROUP BY name ORDER BY name'))
            except sqlite3.OperationalError:
                sizes = {}
        entry = {'variant': variant, 'scope': scope, 'raw_bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                 'build_ms': build_ms, 'gzip': gzip_results, 'ranking_equivalent': not failures,
                 'failed_queries': failures, 'counts': counts, 'table_bytes': sizes,
                 'queries': {key: {k: v for k, v in value.items() if k not in {'rows', 'sql', 'countSql', 'params'}} |
                             {'matches': len(value['rows'])} for key, value in measured.items()}}
        report.append(entry)
        browser_queries = []
        for key, result in measured.items():
            expected = baseline[key]['rows']
            browser_queries.append({'name': key, 'table': result['table'], 'sql': result['sql'],
                                    'params': result['params'], 'countSql': result['countSql'],
                                    'countExpected': len(expected), 'expected':
                                    [dict(zip(('id', 'url', 'score'), row)) for row in expected[:20]]})
        cases.append({'fixture': directory.name, 'name': variant, 'scope': scope, 'path': str(target.absolute()),
                      'gzipPath': str(target.with_suffix('.sqlite.gz').absolute()), 'rawBytes': len(raw),
                      'rankingEquivalent': not failures, 'queries': browser_queries})
        print(json.dumps({'fixture': directory.name, 'variant': variant, 'bytes': len(raw),
                          'gzip9': gzip_results['9']['bytes'], 'parity': not failures}), flush=True)
    return {'fixture': directory.name, 'source': str(source), 'sqlite_version': sqlite3.sqlite_version,
            'cases': report}, cases


def synthetic(template, target, *, works=48, editions=5, documents=5, sections=16):
    """Seeded prose, varied lengths, shared history, identifiers and Unicode.

    This is a storage-scale fixture using the actual baseline schema, not a
    publisher fixture. Existing signed browser fixtures supply source evidence.
    """
    with closing(sqlite3.connect(template)) as original, closing(sqlite3.connect(target)) as db:
        objects = original.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY rowid").fetchall()
        for kind, name, sql in objects:
            if name.startswith(('search_fts_', 'passage_fts_')):
                continue
            db.execute(sql)
        words = ('ecology coastal estuary hydrology field observation evidence measurement salinity climate '
                 'sediment archive biology river water ocean habitat forest plankton microbial isotope analysis '
                 'survey season depth instrument calibration sample replicate temperature oxygen nitrogen '
                 'carbon geology morphology transport velocity spatial temporal uncertainty distribution '
                 'model comparison experiment inference station lagoon peat coral shelf boundary pressure '
                 'carbonate adaptation diversity abundance isotope migration chemistry atmosphere groundwater '
                 'glacial marsh estuarine benthic pelagic method result discussion appendix transcript '
                 'publication source record author language classification synthesis interpretation').split()
        db.executemany('INSERT INTO metadata VALUES (?,?)', [('schema', 'guavajam.search.v1'), ('scope', 'collection')])
        for work in range(1, works + 1):
            title, author = f'Coastal archive {work:03}', f'Example Researcher {work}, 李'
            abstract = f'Field ecology evidence for station {work} and its estuary.'
            labels = 'Café studies field observations needle' + str(work)
            url, path = f'https://example.org/archive-{work:03}.git', f'sources/work-{work:03}'
            latest = hashlib.sha1(f'{work}:{editions}'.encode()).hexdigest()
            db.execute('INSERT INTO repositories VALUES (?,?,?,?)', (work, url, path, latest))
            db.execute('INSERT INTO works VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (work, work, title, author, abstract, 'en', '2026-11-05', latest, editions, labels))
            db.execute('INSERT INTO keywords VALUES (?,?)', (work, 'Café studies'))
            for edition in range(1, editions + 1):
                sha = hashlib.sha1(f'{work}:{edition}'.encode()).hexdigest()
                tree = hashlib.sha1((sha + ':tree').encode()).hexdigest()
                date, message = f'2026-11-{edition:02}', f'Observation {edition}: archive {work}'
                facts = json.dumps({'title': title, 'author': author, 'abstract': abstract, 'language': 'en',
                                    'rights': 'CC-BY-4.0', 'keywords': ['Café studies', 'needle' + str(work)]})
                edition_id = db.execute('INSERT INTO editions (work_id,position,revision,tree,published,title,message,facts_json) '
                                        'VALUES (?,?,?,?,?,?,?,?)', (work, edition-1, sha, tree, date, title, message, facts)).lastrowid
                db.execute('INSERT INTO edition_keywords VALUES (?,?)', (edition_id, 'Café studies'))
                for document in range(documents):
                    source = 'content/manuscript.md' if not document else f'content/supporting/notes-{document}.md'
                    document_title = title if not document else f'Field notes {document}'
                    destination = 'index.html' if not document else f'supporting/notes-{document}.html'
                    target_url = path + '/editions/' + sha + '/read/' + destination
                    pieces = []
                    for section in range(sections):
                        # Most text persists across editions, while varying
                        # section lengths and a small revision portion change.
                        rng = random.Random(8128 + work*10000 + document*100 + section)
                        count = 32 + rng.randrange(81)
                        tokens = [rng.choice(words) for _ in range(count)]
                        tokens.extend([f'sample{work:03}{document:02}{section:02}', 'revision' + str(edition)])
                        if section % 5 == edition % 5:
                            tokens.extend(['measurement'] * edition)
                        pieces.append(f'Section {section + 1}. ' + ' '.join(tokens) + '.')
                    body = document_title + ' ' + ' '.join(pieces)
                    document_id = db.execute('INSERT INTO documents (edition_id,work_id,title,path,url,language,source_sha256) '
                        'VALUES (?,?,?,?,?,?,?)', (edition_id, work, document_title, source, target_url, 'en',
                                                 hashlib.sha256(body.encode()).hexdigest())).lastrowid
                    provenance = ' '.join([url, sha, tree, date, message, source, 'CC-BY-4.0'])
                    record_title = title if not document else title + ' — ' + document_title
                    db.execute('INSERT INTO search_records '
                               '(work_id,edition_id,document_id,url,title,authors,abstract,labels,body,git) '
                               'VALUES (?,?,?,?,?,?,?,?,?,?)',
                               (work, edition_id, document_id, target_url, record_title, author, abstract, labels, body, provenance))
                    for section, text in enumerate(pieces):
                        anchor = 'section-' + str(section+1)
                        heading = 'Section ' + str(section+1)
                        passage_url = target_url + '#' + anchor
                        passage_id = db.execute('INSERT INTO passages '
                            '(edition_id,document_id,work_id,anchor,title,kind,level,position,parent,scope,text,url) '
                            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                            (edition_id, document_id, work, anchor, heading, 'section', 2, section, None,
                             'document', text, passage_url)).lastrowid
                        db.execute('INSERT INTO passage_records VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                                   (passage_id, work, edition_id, document_id, passage_url, record_title + ' — ' + heading,
                                    author, abstract, labels, text, provenance + ' ' + anchor))
        for fts in ('search_fts', 'passage_fts'):
            db.execute('INSERT INTO ' + fts + '(' + fts + ") VALUES ('rebuild')")
            db.execute('INSERT INTO ' + fts + '(' + fts + ") VALUES ('optimize')")
        db.commit()
        db.execute('VACUUM')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', action='append', required=True, metavar='NAME=PATH')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--scale', type=int, default=0, help='Add a synthetic collection with this many works')
    parser.add_argument('--repeats', type=int, default=7)
    parser.add_argument('--variants', default=','.join(VARIANTS))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    databases = [(name, Path(path)) for name, path in (value.split('=', 1) for value in args.database)]
    if args.scale:
        target = args.output / 'synthetic-baseline.sqlite'
        synthetic(databases[0][1], target, works=args.scale)
        databases.append(('synthetic-' + str(args.scale), target))
    reports, cases = [], []
    for name, source in databases:
        report, browser = benchmark(source, args.output / name, variants=args.variants.split(','), repeats=args.repeats)
        reports.append(report)
        cases.extend(browser)
        (args.output / 'storage-report.json').write_text(json.dumps(reports, indent=2) + '\n')
        (args.output / 'browser-manifest.json').write_text(json.dumps({'cases': cases}, indent=2) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
