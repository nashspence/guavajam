"""Qualify SQLite transfer/caching, or benchmark an explicit storage manifest."""
import argparse
import gzip
import hashlib
import json
import random
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]


def small_database(path):
    """A deterministic transport workload; signed rendering has its own journeys."""
    rng = random.Random(271828)
    words = [''.join(rng.choice('abcdefghijklmnopqrstuvwxyz') for _ in range(9)) for _ in range(2000)]
    with sqlite3.connect(path) as db:
        db.executescript('''
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            INSERT INTO metadata VALUES('schema','guavajam.search.v1'),('scope','collection');
            CREATE TABLE search_records(id INTEGER PRIMARY KEY,url TEXT,title TEXT,authors TEXT,
                abstract TEXT,labels TEXT,body TEXT,git TEXT);
            CREATE VIRTUAL TABLE search_fts USING fts5(title,authors,abstract,labels,body,git,
                content='search_records',content_rowid='id',tokenize='unicode61 remove_diacritics 2');
        ''')
        for number in range(1, 129):
            body = ' '.join(rng.choices(words, k=768))
            body += ' coastal ecology' * (1 + number % 7)
            if number % 11 == 0:
                body += ' rare estuary observations'
            db.execute('INSERT INTO search_records VALUES(?,?,?,?,?,?,?,?)',
                       (number, f'works/{number}/read/index.html', f'Field study {number}',
                        'Example Author', 'Observed ecology', 'Café studies', body, 'revision ' + str(number)))
        db.execute("INSERT INTO search_fts(search_fts) VALUES('rebuild')")
        db.execute("INSERT INTO search_fts(search_fts) VALUES('optimize')")
        db.commit()
        db.execute('VACUUM')
        queries = []
        for term in ('"ecol"*', '"coast"* AND "estuar"*', '"cafe"*', '"nonexistenttoken"*'):
            sql = ('SELECT s.id,s.url,bm25(search_fts,4.,2.,1.,2.,1.,.5) AS score '
                   'FROM search_fts JOIN search_records s ON s.id=search_fts.rowid '
                   'WHERE search_fts MATCH ? ORDER BY score,s.id LIMIT 20')
            count_sql = 'SELECT count(*) FROM search_fts WHERE search_fts MATCH ?'
            db.row_factory = sqlite3.Row
            queries.append({'name': term, 'sql': sql, 'params': [term],
                            'expected': [dict(row) for row in db.execute(sql, (term,))],
                            'countSql': count_sql, 'countExpected': db.execute(count_sql, (term,)).fetchone()[0]})
    return {'fixture': 'seeded-transport', 'name': 'baseline', 'scope': 'collection',
            'path': str(path), 'rankingEquivalent': True, 'queries': queries}


def prepare(directory, *, storage_manifest=None, names=('baseline', 'contentless_full'),
            fixtures=(), repetitions=3, profiles=('local', 'controlled', 'controlled-runtime-gzip'), gzip_level=9):
    directory = Path(directory)
    directory.mkdir(parents=True)
    site = directory / 'site'
    site.mkdir()
    if storage_manifest:
        cases = json.loads(Path(storage_manifest).read_text())['cases']
        cases = [case for case in cases if case['name'] in names and case['rankingEquivalent']
                 and (not fixtures or case['fixture'] in fixtures)]
    else:
        cases = [small_database(directory / 'fixture.sqlite')]
    if not cases:
        raise ValueError('No ranking-equivalent benchmark cases selected')
    runtime = ROOT / 'node_modules/@sqlite.org/sqlite-wasm/dist'
    for name, source in {
        'sqlite3.mjs': runtime / 'index.mjs', 'sqlite3.wasm': runtime / 'sqlite3.wasm',
        'search-download.js': ROOT / 'some-implementations/a-guavajam-static-website-generator/src/a_guavajam_static_website_generator/assets/search-download.js',
        'search_download_worker.mjs': Path(__file__).with_name('search_download_worker.mjs'),
    }.items():
        shutil.copyfile(source, site / name)
    (site / 'index.html').write_text('<!doctype html><html lang="en"><title>SQLite transfer qualification</title>'
                                   '<h1>SQLite transfer qualification</h1></html>')
    output_cases = []
    for number, case in enumerate(cases):
        folder = site / 'data' / str(number)
        folder.mkdir(parents=True)
        raw = Path(case['path']).read_bytes()
        stem = 'work' if case['scope'] == 'work' else 'index'
        plain = folder / (stem + '.sqlite')
        plain.write_bytes(raw)
        compressed = gzip.compress(raw, compresslevel=gzip_level, mtime=0)
        digest = hashlib.sha256(compressed).hexdigest()
        zipped = folder / f'{stem}.{digest}.sqlite.gz'
        zipped.write_bytes(compressed)
        # A second valid generation makes cache invalidation observable.
        newer = folder / 'next.sqlite'
        newer.write_bytes(raw)
        with sqlite3.connect(newer) as db:
            db.execute("INSERT OR REPLACE INTO metadata VALUES('benchmark-generation','next')")
        next_raw = newer.read_bytes()
        next_gzip = gzip.compress(next_raw, compresslevel=gzip_level, mtime=0)
        newer.unlink()
        next_path = folder / f'{stem}.{hashlib.sha256(next_gzip).hexdigest()}.sqlite.gz'
        next_path.write_bytes(next_gzip)
        # Collection UI never queries passages. Keep query timing populations
        # identical when comparing its old combined DB with a pruned one.
        queries = [query for query in case['queries']
                   if case['scope'] != 'collection' or query.get('table') != 'passage_fts']
        output_cases.append({**{key: case[key] for key in ('fixture', 'name', 'scope')}, 'queries': queries,
                             'raw': plain.relative_to(site).as_posix(), 'gzip': zipped.relative_to(site).as_posix(),
                             'rawBytes': len(raw), 'gzipBytes': len(compressed),
                             'next': next_path.relative_to(site).as_posix(),
                             'nextRawBytes': len(next_raw), 'nextGzipBytes': len(next_gzip)})
    manifest = directory / 'search-download-browser.json'
    manifest.write_text(json.dumps({'root': str(site), 'cases': output_cases, 'repetitions': repetitions,
                                   'profiles': list(profiles), 'gzipLevel': gzip_level,
                                   'requireCacheReuse': storage_manifest is None}))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--storage-manifest', type=Path)
    parser.add_argument('--names', default='baseline,contentless_full')
    parser.add_argument('--fixtures', default='')
    parser.add_argument('--profiles', default='local,controlled,controlled-runtime-gzip')
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--gzip-level', type=int, choices=range(1, 10), default=9)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error('--repetitions must be positive')
    if not set(args.profiles.split(',')) <= {'local', 'controlled', 'controlled-runtime-gzip'}:
        parser.error('Unknown HTTP profile')
    settings = dict(storage_manifest=args.storage_manifest, names=args.names.split(','),
                    fixtures=list(filter(None, args.fixtures.split(','))), repetitions=args.repetitions,
                    profiles=args.profiles.split(','), gzip_level=args.gzip_level)
    if args.prepare:
        print(prepare(args.prepare, **settings))
        return 0
    with tempfile.TemporaryDirectory(prefix='guavajam-download-browser-') as temporary:
        manifest = prepare(Path(temporary) / 'fixture', **settings)
        output = args.output or Path(temporary) / 'evidence'
        return subprocess.run(['node', str(Path(__file__).with_name('test_search_download_browser.mjs')),
                               str(manifest), str(output)], check=False).returncode


if __name__ == '__main__':
    sys.exit(main())
