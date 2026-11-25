"""Feature-preserving external-content FTS and counterexamples to lossy designs.

Synthetic correctness examples, not a Riverhog corpus or browser benchmark.
Run with Python 3.12+ and SQLite FTS5: python semantic-parity-experiment.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
import sys
from pathlib import Path

FIELDS = ('title', 'authors', 'abstract', 'labels', 'body', 'git')
TOKENIZER = "tokenize='unicode61 remove_diacritics 2', detail=full, columnsize=1"


def full_rows():
    # Source content is shared; every occurrence retains its own provenance.
    source = [
        ('Alpha interface', 'Retries transaction rollback; café café', 'Contract declaration'),
        ('Beta interface', 'Archive collection input sqlite table', 'Usage promise'),
        ('Gamma interface', 'Never promise that an experimental feature is stable', 'Limitations'),
        ('Alpha interface', 'Retries transaction rollback; café café', 'Contract declaration'),
    ]
    rows = []
    for edition in range(1, 4):
        for ordinal, (title, body, abstract) in enumerate(source):
            if edition == 3 and ordinal == 2:
                continue  # Document removal does not erase prior identity.
            source_path = f'content/supporting/{ordinal}.md'
            if edition == 2 and ordinal == 3:
                source_path = 'content/supporting/moved.md'
            row_id = len(rows) + 1
            fields = (
                'Example work — ' + title,
                'Alice Archivist' if edition == 1 else 'Beatriz Writer',
                f'Edition {edition} abstract',
                'stable DataCite' if edition < 3 else 'experimental DataCite',
                body,
                f'https://github.com/example/work {edition:040x} tree{edition} v{edition} {source_path}',
            )
            rows.append((row_id, edition, source_path, fields))
    return rows


def external_content_parity():
    db = sqlite3.connect(':memory:')
    cols = ','.join(FIELDS)
    db.executescript(f'''
        CREATE VIRTUAL TABLE baseline USING fts5({cols},{TOKENIZER});
        CREATE TABLE source (id INTEGER PRIMARY KEY, title TEXT, body TEXT, UNIQUE(title,body));
        CREATE TABLE edition (id INTEGER PRIMARY KEY, authors TEXT, abstract TEXT, labels TEXT);
        CREATE TABLE occurrence (id INTEGER PRIMARY KEY, edition_id INTEGER, source_id INTEGER,
            source_path TEXT, source_sha256 TEXT, git TEXT);
        CREATE TABLE facets (occurrence_id INTEGER, name TEXT COLLATE BINARY, value TEXT COLLATE BINARY,
            PRIMARY KEY(occurrence_id,name,value));
        CREATE INDEX facets_by_value ON facets(name,value,occurrence_id);
        CREATE VIEW external_fields AS
            SELECT o.id,s.title,e.authors,e.abstract,e.labels,s.body,o.git
            FROM occurrence o JOIN source s ON s.id=o.source_id JOIN edition e ON e.id=o.edition_id;
        CREATE VIRTUAL TABLE compact USING fts5({cols},content='external_fields',content_rowid='id',{TOKENIZER});
    ''')
    rows = full_rows()
    for row_id, edition, path, fields in rows:
        db.execute('INSERT INTO baseline VALUES (?,?,?,?,?,?)', fields)
        title, authors, abstract, labels, body, git = fields
        db.execute('INSERT OR IGNORE INTO source(title,body) VALUES (?,?)', (title, body))
        source_id = db.execute('SELECT id FROM source WHERE title=? AND body=?', (title, body)).fetchone()[0]
        db.execute('INSERT OR IGNORE INTO edition VALUES (?,?,?,?)', (edition, authors, abstract, labels))
        # Same extracted body can arise from distinct exact source bytes.
        raw_source = f'---\ntitle: {title}\nmetadata:\n  Release: v{edition}\n---\n\n{body}\n'
        source_hash = hashlib.sha256(raw_source.encode()).hexdigest()
        db.execute('INSERT INTO occurrence VALUES (?,?,?,?,?,?)', (row_id, edition, source_id, path, source_hash, git))
        for name, values in {'Release': [f'v{edition}'], 'Label': ['Café', 'all']}.items():
            for value in values:
                db.execute('INSERT INTO facets VALUES (?,?,?)', (row_id, name, value))
        db.execute('INSERT INTO compact(rowid,title,authors,abstract,labels,body,git) VALUES (?,?,?,?,?,?,?)', (row_id, *fields))
    queries = ['alpha', 'transaction', '"trans"* AND "ali"*', '"archive"* AND "beatriz"*',
               'authors : alice', 'body : cafe', '"v2"* AND "rollback"*', 'experimental',
               'git : moved', '"no such phrase"', '"transaction rollback"', '"DataCite"*',
               'abstract : "Edition 3"', '"stable"* AND "archive"*']
    comparisons = []
    def query(table, match):
        return db.execute(f'''SELECT rowid,bm25({table},4.0,2.0,1.0,2.0,1.0,0.5),
            {','.join(f"snippet({table},{i},'<m>','</m>','…',24)" for i in range(6))}
            FROM {table} WHERE {table} MATCH ? ORDER BY 2,1''', (match,)).fetchall()
    for match in queries:
        baseline, compact = query('baseline', match), query('compact', match)
        assert baseline == compact, (match, baseline, compact)
        comparisons.append({'query': match, 'hits': len(baseline), 'identities_rank_and_all_six_snippets_equal': True})
    count = db.execute('SELECT count(*) FROM source').fetchone()[0]
    mixed_query = '"transaction"* AND "alice"*'
    correct = db.execute('SELECT rowid FROM baseline WHERE baseline MATCH ?', (mixed_query,)).fetchall()
    body_only = db.execute('SELECT rowid FROM baseline WHERE baseline MATCH ?', ('body : (' + mixed_query + ')',)).fetchall()
    assert correct and not body_only
    filtered = db.execute('''SELECT o.id,o.edition_id,o.source_path,o.source_sha256 FROM compact
        JOIN occurrence o ON o.id=compact.rowid WHERE compact MATCH 'transaction'
        AND EXISTS (SELECT 1 FROM facets f WHERE f.occurrence_id=o.id AND f.name='Release' AND f.value='v2')
        ORDER BY o.id''').fetchall()
    assert [(r[0],r[1],r[2]) for r in filtered] == [(5,2,'content/supporting/0.md'),(8,2,'content/supporting/moved.md')]
    assert db.execute("SELECT count(*) FROM facets WHERE name='Label' AND value='Cafe'").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM facets WHERE name='Label' AND value='Café'").fetchone()[0] == len(rows)
    assert db.execute('SELECT count(DISTINCT source_sha256) FROM occurrence WHERE source_id=1').fetchone()[0] == 3
    # Reconstructing exact fields via a view retains cross-field AND behavior.
    return {'occurrences': len(rows), 'unique_source_rows': count, 'queries': comparisons,
            'metadata_only_change_and_moved_path': {'filtered_v2_transaction_occurrences': filtered,
                'source_hashes_remain_occurrence_specific': True, 'literal_facet_unicode_preserved': True},
            'mixed_field_counterexample': {'query': mixed_query, 'correct_occurrence_ids': [x[0] for x in correct],
                                           'body_only_occurrence_ids': [x[0] for x in body_only]}}


def ranked(documents, repetitions=None):
    db = sqlite3.connect(':memory:')
    db.execute("CREATE VIRTUAL TABLE ft USING fts5(key UNINDEXED, body, tokenize='unicode61 remove_diacritics 2')")
    for key, body in documents:
        for _ in range((repetitions or {}).get(key, 1)):
            db.execute('INSERT INTO ft(key,body) VALUES (?,?)', (key, body))
    matches = db.execute("SELECT key,bm25(ft) FROM ft WHERE ft MATCH '\"alpha\"* AND \"beta\"*' ORDER BY 2,key").fetchall()
    unique = {}
    for key, score in matches:
        unique.setdefault(key, score)
    db.close()
    return sorted(unique.items(), key=lambda item: (item[1], item[0]))


def rank_counterexamples():
    rng = random.Random(729)
    evidence = {}
    for trial in range(100):
        docs = [(str(i), ' '.join(rng.choices(['alpha', 'beta', 'other'], weights=[1,1,8], k=rng.randint(4,45))))
                for i in range(8)]
        reps = {str(i): rng.randint(1,20) for i in range(8)}
        full = ranked(docs, reps)
        dedup = ranked(docs)
        left, right = docs[:4], docs[4:]
        monolith = ranked(docs)
        sharded = sorted(ranked(left) + ranked(right), key=lambda item: (item[1], item[0]))
        if 'dedup' not in evidence and [x[0] for x in full] != [x[0] for x in dedup]:
            evidence['dedup'] = {'documents': docs, 'occurrences_per_document': reps,
                                 'occurrence_weighted_rank': full, 'unique_content_rank': dedup}
        if 'shards' not in evidence and [x[0] for x in monolith] != [x[0] for x in sharded]:
            evidence['shards'] = {'documents': docs, 'partition': {'left': [x[0] for x in left], 'right': [x[0] for x in right]},
                                  'global_rank': monolith, 'local_bm25_merged_rank': sharded}
        if len(evidence) == 2:
            break
    assert len(evidence) == 2
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(__file__).with_name('semantic-probes.json'))
    args = parser.parse_args()
    report = {'python': sys.version.split()[0], 'sqlite': sqlite3.sqlite_version,
              'kind': 'synthetic SQLite correctness experiment; not performance qualification',
              'external_content_parity': external_content_parity(), 'rank_counterexamples': rank_counterexamples()}
    output = args.output
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'output': str(output), 'query_parity_cases': len(report['external_content_parity']['queries']),
                      'dedup_rank_changed': True, 'partition_rank_changed': True}))


if __name__ == '__main__':
    main()
