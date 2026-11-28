"""Finite installed-package search qualification; modeled history is not publication authority."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import random
import resource
import sqlite3
import subprocess
import tempfile
import time

from a_guavajam_static_website_generator import navigation, search_build
from a_guavajam_static_website_generator.navigation_manifest import write_document, write_json
from a_guavajam_static_website_generator.renderer import pandoc
from a_guavajam_static_website_generator.search_artifact import emit_download
from a_guavajam_static_website_generator.search_index import emit_browser_assets

# This is a finite qualification envelope, not a universal publication SLA.
PROFILES = {
    'smoke': {'documents': 4, 'sections': 4, 'section_bytes': 2048, 'points': [1, 5]},
    'history-50': {'documents': 64, 'sections': 8, 'section_bytes': 32768, 'points': [1, 5, 50]},
}
BUDGETS = {
    'build_seconds_per_point': 1800, 'browser_process_tree_bytes': 2560 * 1024**2,
    'build_process_bytes': 1024 * 1024**2,
    'cold_init_ms': 60000, 'query_ms': 180000, 'delivery_bytes_per_point': 2 * 1024**3,
}
QUERIES = ['Scalebodyselector', 'Rareselector', 'Café', 'Nearleft Nearright', 'Missingselector']


def text_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def model_document(number, version, profile, directory):
    """Parse rich GFM once per distinct document, retaining ordered source blocks."""
    cache = directory / (str(number) + '-' + str(version) + '.json')
    if cache.exists():
        return json.loads(cache.read_text())
    rng = random.Random(number * 100000 + version)
    vocabulary = ['lexeme' + str(index).zfill(4) for index in range(4096)]
    body = []
    for section in range(profile['sections']):
        size = profile['section_bytes'] * (4 if number == 0 and version >= 40 else 1)
        words = ' '.join(rng.choices(vocabulary, k=size // 11))
        body += ['<a id="section-' + str(section) + '"></a>',
                 '## Evidence ' + str(section), '',
                 'Scalebodyselector Café Nearleft ' + words + ' Nearright.', '',
                 '- A **strong** observation and an *emphasized* record.',
                 '- A second item with `literal code`.', '',
                 '| Measurement | Value |', '| --- | --- |', '| Distance | 17 |', '',
                 '> An independently delimited quotation.', '',
                 '```text', 'literal example without generated navigation', '```', '']
        if number == 0 and section == 0:
            body += ['Rareselector Editionneedle' + str(version) + '.', '']
    source = '\n'.join(body)
    ast = json.loads(pandoc(source.encode()))
    document = {**navigation.extract(ast), 'title': 'Supporting evidence ' + str(number),
                'language': 'en', 'abstract': 'A source-derived summary for document ' + str(number),
                'source_sha256': text_hash(source),
                'view': 'index.html' if number == 0 else 'supporting/note-' + str(number) + '.html',
                'metadata': [['Group', ['01', 'Café']], ['Empty', []], ['Literal', '']]}
    cache.write_text(json.dumps(document, ensure_ascii=False))
    return document


def append_edition(root, position, versions, profile, cache):
    documents = profile['documents']
    # Sparse churn, a dense middle, then one disproportionately large source.
    changed = (range(max(1, documents // 16)) if position < 25 else
               range(documents // 2) if position < 40 else [0])
    for number in changed:
        versions[number] = position
    revision = hashlib.sha1(('modeled-edition-' + str(position)).encode()).hexdigest()
    canonical = {'hash_algorithm': 'sha1', 'revision': revision, 'manuscript': 'content/manuscript.md'}
    facts = {'title': 'Scale history', 'author': 'Example Author', 'language': 'en',
             'rights': 'CC-BY-4.0', 'abstract': 'A bibliographic scale observation.',
             'keywords': ['Scale'], 'subjects': [{'subject': 'Evidence'}]}
    view = {'canonical': canonical, 'preview': False, 'facts': facts, 'documents': {}, 'files': {}}
    descriptors = {}
    read = root / 'editions' / revision / 'read'
    read.mkdir(parents=True)
    for number in range(documents):
        source = ('content/manuscript.md' if number == 0 else 'content/supporting/note-' + str(number) + '.md')
        document = model_document(number, versions[number], profile, cache)
        details = {key: document[key] for key in ['title', 'language', 'abstract', 'source_sha256', 'view', 'metadata']}
        details['anchors'] = [item['anchor'] for item in document['anchors']]
        view['documents'][source] = details
        descriptor = write_document(read, canonical, False, source, document)
        descriptors[source] = descriptor
        view['files'][descriptor['url']] = descriptor['sha256']
    write_json(read / 'NAV.json', {'schema': 'guavajam.navigation.v1', 'layout': 'documents',
                                 'canonical': canonical, 'preview': False, 'documents': descriptors})
    view['files']['NAV.json'] = hashlib.sha256((read / 'NAV.json').read_bytes()).hexdigest()
    write_json(read / 'VIEW.json', view)
    write_json(read.parent / 'BIBLIOGRAPHY.json', {'schema': 'guavajam.search-bibliography.v1',
               'revision': revision, 'preview': False, 'facts': facts})
    return {'revision': revision, 'tree': hashlib.sha1(('tree-' + str(position)).encode()).hexdigest(),
            'published': '2026-' + str(position // 28 + 1).zfill(2) + '-' + str(position % 28 + 1).zfill(2),
            'message': 'Model edition ' + str(position) + ' with full commit-message evidence.'}


def oracle(database):
    results = []
    with closing(sqlite3.connect(database)) as db:
        for term in QUERIES:
            query = ' AND '.join('"' + word + '"*' for word in term.split())
            from_sql = (' FROM passage_fts JOIN passages p ON p.id=passage_fts.rowid '
                        'JOIN documents d ON d.id=p.document_id JOIN editions e ON e.id=p.edition_id '
                        'WHERE passage_fts MATCH ?')
            sql = 'SELECT p.id,bm25(passage_fts,4,2,1,2,1,0.5) AS score' + from_sql
            sql += ' ORDER BY score,e.position DESC,d.path,p.position,p.id'
            digest, first, count = hashlib.sha256(), [], 0
            started = time.perf_counter()
            for identifier, score in db.execute(sql, (query,)):
                digest.update(json.dumps([identifier, score], separators=(',', ':')).encode() + b'\n')
                if count < 40:
                    first.append({'id': identifier, 'score': score})
                count += 1
            assert db.execute('SELECT count(*)' + from_sql, (query,)).fetchone()[0] == count
            results.append({'term': term, 'total': count, 'first': first, 'full_rank_sha256': digest.hexdigest(),
                            'native_full_rank_ms': (time.perf_counter() - started) * 1000})
        counts = {name: db.execute('SELECT count(*) FROM ' + name).fetchone()[0] for name in
                  ['editions', 'documents', 'passage_occurrences', 'source_texts', 'source_contents']}
    return results, counts


def tree_rss(pid):
    """Sample aggregate RSS, independently of WASM and explicit JS caches."""
    rows = subprocess.run(['ps', '-eo', 'pid=,ppid=,rss='], check=True, capture_output=True, text=True).stdout
    records = [tuple(map(int, row.split())) for row in rows.splitlines()]
    descendants = {pid}
    while True:
        found = {child for child, parent, _ in records if parent in descendants}
        if found <= descendants:
            break
        descendants |= found
    return sum(rss * 1024 for child, _, rss in records if child in descendants)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=PROFILES, default='history-50')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--require-installed', action='store_true')
    args = parser.parse_args()
    if args.require_installed and '/src/' in search_build.__file__:
        raise RuntimeError('Scale qualification must use an isolated installed distribution')
    profile = PROFILES[args.profile]
    report = {'profile': args.profile, 'workload': profile, 'budgets': BUDGETS,
              'authority': 'Modeled histories from production GFM extraction, not signed publication acceptance.',
              'device_limit': 'Desktop Chromium/Firefox; mobile-shaped throttled Chromium is a simulation, not iOS.',
              'points': [], 'passed': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    def save():
        args.output.write_text(json.dumps(report, indent=2) + '\n')
    save()
    repo = Path(__file__).resolve().parents[3]
    os.environ.setdefault('GUAVAJAM_SQLITE_WASM_DIR', str(repo / 'node_modules/@sqlite.org/sqlite-wasm/dist'))
    with tempfile.TemporaryDirectory(prefix='guavajam-scale-') as temporary:
        root = Path(temporary) / 'site'
        root.mkdir()
        cache = Path(temporary) / 'source-cache'
        cache.mkdir()
        versions, editions = [0] * profile['documents'], []
        for position in range(max(profile['points'])):
            editions.append(append_edition(root, position, versions, profile, cache))
            count = position + 1
            if count not in profile['points']:
                continue
            write_json(root / 'CATALOG.json', {'tip': editions[-1]['revision'], 'editions': editions})
            point = root / 'points' / str(count)
            started = time.perf_counter()
            database = search_build.build(root, [{'path': '', 'repository': 'https://example.org/scale.git',
                 'tip': editions[-1]['revision']}], filename='points/' + str(count) + '/history.sqlite', scope='history')
            seconds = time.perf_counter() - started
            descriptor = emit_download(database)
            oracles, counts = oracle(database)
            files = [path for path in point.rglob('*') if path.is_file()]
            delivery = sum(path.stat().st_size for path in files if path.suffix != '.sqlite')
            measured = {'editions': count, 'build_seconds': seconds, 'counts': counts,
                        'database_bytes': database.stat().st_size, 'encoding': descriptor['encoding'],
                        'delivery_bytes': delivery, 'hosted_index_bytes_including_native_sqlite': delivery + database.stat().st_size,
                        'build_process_peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
                        'build_child_peak_rss_bytes': resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * 1024,
                        'oracles': oracles}
            report['points'].append(measured)
            save()
            assert seconds <= BUDGETS['build_seconds_per_point'], measured
            assert measured['build_process_peak_rss_bytes'] <= BUDGETS['build_process_bytes'], measured
            assert delivery <= BUDGETS['delivery_bytes_per_point'], measured
            assert counts['editions'] == count and counts['documents'] == count * profile['documents']
            assert counts['passage_occurrences'] == count * profile['documents'] * profile['sections']
            if args.profile == 'history-50' and count == 50:
                assert descriptor['encoding'] == 'sqlite-chunks', 'Large history must exercise the production oversize selection'
            emit_browser_assets(point)
            (point / 'test.html').write_text('<!doctype html><title>Search scale qualification</title>')
            fixture = point / 'fixture.json'
            fixture.write_text(json.dumps({'root': str(point), 'descriptor': descriptor, 'oracles': oracles,
                                            'budgets': BUDGETS}))
            browser_report = point / 'browser-report.json'
            with args.output.with_suffix('.browser.log').open('w') as log:
                child = subprocess.Popen(['node', str(Path(__file__).with_name('test_search_scale_browser.mjs')),
                                          str(fixture), str(browser_report)], stdout=log, stderr=subprocess.STDOUT)
                peak = 0
                while child.poll() is None:
                    peak = max(peak, tree_rss(child.pid))
                    time.sleep(.2)
                code = child.returncode
            measured['browser_process_tree_peak_rss_bytes'] = peak
            measured['browser'] = json.loads(browser_report.read_text()) if browser_report.exists() else {'passed': False}
            save()
            assert code == 0, 'Browser qualification failed; inspect its private log'
            assert peak <= BUDGETS['browser_process_tree_bytes'], measured
        report['passed'] = True
        save()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
