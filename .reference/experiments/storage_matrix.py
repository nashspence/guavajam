#!/usr/bin/env python3
"""Research layouts using the complete native Riverhog contract inventory.

The baseline copies GuavaJam's SQL schema, not its signed-publication pipeline.
History is modeled deterministically, not claimed to be Riverhog release history.
All candidate FTS rows retain original occurrence IDs, exact six fields and
tokenizer/positions/docsize. Compatibility views preserve the current worker API.
"""
from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import json
import platform
import random
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time
from pathlib import Path

FIELDS = ('title', 'authors', 'abstract', 'labels', 'body', 'git')
J = lambda x: json.dumps(x, ensure_ascii=False, separators=(',', ':'))
H = lambda x: hashlib.sha256(x.encode()).hexdigest()
S = lambda x: hashlib.sha1(x.encode()).hexdigest()
FACETS = '''
CREATE TABLE facet_values(id INTEGER PRIMARY KEY,name TEXT COLLATE BINARY NOT NULL,
 value TEXT COLLATE BINARY NOT NULL,UNIQUE(name,value));
CREATE TABLE document_facets(document_id INTEGER NOT NULL REFERENCES documents(id),
 facet_id INTEGER NOT NULL REFERENCES facet_values(id),PRIMARY KEY(document_id,facet_id)) WITHOUT ROWID;
CREATE INDEX facets_by_value ON document_facets(facet_id,document_id);
'''


def schema(guavajam):
    path = guavajam / 'some-implementations/a-guavajam-static-website-generator/src/a_guavajam_static_website_generator/search_index.py'
    parts = []
    for node in ast.walk(ast.parse(path.read_text())):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'executescript' and isinstance(node.args[0], ast.Constant)):
            text = node.args[0].value
            if 'CREATE TABLE metadata' in text or 'CREATE TABLE passages (' in text:
                parts.append(text)
    assert len(parts) == 2
    parts.sort(key=lambda x: 'CREATE TABLE metadata' not in x)
    return '\n'.join(parts) + FACETS


def facts(edition, editions):
    revision, tree = S(f'modeled-edition-{edition}'), S(f'modeled-tree-{edition}')
    f = {'title': 'Riverhog native contract reference', 'language': 'en',
         'authors': [{'name': 'Riverhog contributors'}], 'rights': 'Apache-2.0',
         'abstract': 'Native source contract reference; modeled documentation history.',
         'keywords': ['contract', 'Café', 'interface'],
         'identifiers': {'experiment': f'edition-{edition}'}}
    published = f'2026-11-{(edition - 1) % 25 + 1:02d}'
    message = f'Modeled documentation update {edition}'
    before = ' '.join(['https://github.com/nashspence/riverhog', revision, tree, published, message]) + ' '
    after = ' ' + ' '.join([f'v{edition}', f['rights'], '',
                            json.dumps(f['identifiers'], ensure_ascii=False),
                            json.dumps(f['authors'], ensure_ascii=False)])
    return revision, tree, f, published, message, before, after


def source_rows(corpus, editions, change_rate):
    rng = random.Random(729)
    changed = {}
    for edition in range(1, editions + 1):
        if edition > 1:
            for doc in rng.sample(corpus, round(len(corpus) * change_rate)):
                changed[doc['id']] = edition
        for doc in corpus:
            navigation = doc.get('navigation')
            passages = ([{**{key:value for key,value in p.items() if key != 'text'},'body':p['text']} for p in navigation['anchors']] if navigation
                        else [dict(p) for p in doc['passages']])
            if doc['id'] in changed:
                passages[-1]['body'] += f' Modeled contract observation {changed[doc["id"]]}.'
                if navigation and passages[-1]['kind'] == 'passage':
                    passages[-1]['title'] = passages[-1]['body'][:160]
            yield edition, doc, passages


def baseline(args, corpus):
    sys.path[:0] = [str(args.guavajam / 'src'), str(args.guavajam / 'some-implementations/a-guavajam-static-website-generator/src')]
    from a_guavajam_static_website_generator.citation import citation_presentation
    from a_guavajam_static_website_generator.navigation import source_text
    from guavajam.supporting_metadata import document_frontmatter, metadata_block
    from guavajam.supporting_gfm import MAX_RECORD_BYTES
    from guavajam_corpus_adapter import literal
    path = args.output / 'baseline.sqlite'
    path.unlink(missing_ok=True)
    db = sqlite3.connect(path)
    db.executescript(schema(args.guavajam))
    db.execute('PRAGMA synchronous=OFF')
    db.execute('INSERT INTO metadata VALUES(?,?)', ('schema', 'guavajam.search.v1'))
    db.execute('INSERT INTO metadata VALUES(?,?)', ('scope', 'work'))
    exact_navigation = 'navigation' in corpus[0]
    db.execute('INSERT INTO metadata VALUES(?,?)', ('experiment-body-layout',
        'title-space-passages' if exact_navigation else 'double-lf-passages'))
    latest = facts(args.editions, args.editions)
    db.execute('INSERT INTO repositories VALUES(1,?,?,?)', ('https://github.com/nashspence/riverhog', '', latest[0]))
    db.execute('INSERT INTO works VALUES(1,1,?,?,?,?,?,?,?,?,?,?)',
               (latest[2]['title'], 'Riverhog contributors', latest[2]['abstract'], 'Experiment', '',
                'en', latest[3], latest[0], args.editions, 'contract Café interface'))
    # The same fields later reconstruct the exact production-format FTS text.
    db.execute('''CREATE TABLE edition_search_fields(edition_id INTEGER PRIMARY KEY REFERENCES editions(id),
        authors TEXT,abstract TEXT,labels TEXT,git_before_path TEXT,git_after_path TEXT)''')
    facets = {}
    active_edition = None
    for edition, doc, passages in source_rows(corpus, args.editions, args.change_rate):
        if active_edition != edition:
            revision, tree, f, day, message, before, after = facts(edition, args.editions)
            db.execute('INSERT INTO editions VALUES(?,1,?,?,?,?,?,?,?)',
                       (edition, edition - 1, revision, tree, day, f['title'], message, J(f)))
            db.execute('INSERT INTO edition_search_fields VALUES(?,?,?,?,?,?)',
                       (edition, 'Riverhog contributors', f['abstract'], 'contract Café interface', before, after))
            for keyword in f['keywords']:
                db.execute('INSERT INTO edition_keywords VALUES(?,?)', (edition, keyword))
                if edition == args.editions:
                    db.execute('INSERT INTO keywords VALUES(1,?)', (keyword,))
            active_edition = edition
        metadata = dict(doc['metadata']) if exact_navigation else {**doc['metadata'], 'Label': ['all', 'Café' if int(H(doc['id'])[0],16) % 2 else 'Other']}
        if args.metadata_mode == 'edition':
            metadata['Example version'] = f'v{edition}'
        # Supporting metadata is also visible/searchable source text today.
        # Model that introduction as its own explicit passage, so changing a
        # release facet does not incorrectly leave the whole document equal.
        if exact_navigation:
            metadata_body = source_text(metadata_block(list(metadata.items())))
            assert passages[0]['anchor'] == 'metadata'
            passages[0] = {**passages[0], 'body':metadata_body, 'title':metadata_body[:160]}
            body = ' '.join([doc['title'], *(p['body'] for p in passages)])
            if edition == 1:
                assert body == doc['navigation']['text']
                assert all(p['body'] == a['text'] and p['title'] == a['title']
                           for p,a in zip(passages,doc['navigation']['anchors'],strict=True))
        else:
            metadata_body = ' '.join(key + ': ' + ('; '.join(value) if isinstance(value,list) else value)
                                     for key,value in metadata.items())
            passages = [{'anchor':'metadata','title':'Document metadata','body':metadata_body}, *passages]
            body = '\n\n'.join(p['body'] for p in passages)
        path_string = doc['path']
        url = f'editions/{revision}/read/supporting/{Path(path_string).stem}.html'
        abstract = doc.get('abstract', f"{doc['metadata']['Interface']} · {doc['metadata']['Authority']}")
        if exact_navigation:
            # This experiment accepts the explicitly anchored flat-paragraph
            # adapter, not arbitrary overlapping heading/quotation NAV records.
            assert all(p['kind'] == 'passage' and p['level'] == 0
                       and p['parent'] is None and p['scope'] == 'document'
                       for p in passages)
            source = document_frontmatter(doc['title'], abstract=abstract,
                                          metadata=list(metadata.items()))
            source += '<a name="metadata"></a>\n\n'
            for passage in passages[1:]:
                source += (f'<a name="{passage["anchor"]}"></a>\n\n'
                           + literal(passage['body']) + '\n\n')
            source_bytes = (source.rstrip() + '\n').encode('utf-8')
            assert len(source_bytes) <= MAX_RECORD_BYTES
            source_hash = hashlib.sha256(source_bytes).hexdigest()
            if edition == 1:
                # The complete first edition is byte-identical to the fixture
                # already accepted by the production supporting-GFM loader.
                assert source_hash == doc['source_markdown_sha256'], doc['id']
                assert len(source_bytes) == doc['source_markdown_bytes'], doc['id']
        else:
            # Retained only for the explicitly labeled earlier heading proxy.
            source_hash = H(J([doc['title'], abstract, metadata, passages]))
        d = db.execute('''INSERT INTO documents(edition_id,work_id,title,path,url,language,source_sha256,abstract)
                          VALUES(?,1,?,?,?,?,?,?)''',
                       (edition, doc['title'], path_string, url, 'en', source_hash, abstract)).lastrowid
        db.execute('INSERT INTO document_fts(rowid,title,abstract) VALUES(?,?,?)', (d, doc['title'], abstract))
        r = db.execute('INSERT INTO search_records(work_id,edition_id,document_id,url) VALUES(1,?,?,?)',
                       (edition, d, url)).lastrowid
        title = f['title'] + (' — ' + doc['title'] if doc['title'] != f['title'] else '')
        provenance = before + path_string + after
        db.execute('INSERT INTO search_fts(rowid,title,authors,abstract,labels,body,git) VALUES(?,?,?,?,?,?,?)',
                   (r, title, 'Riverhog contributors', f['abstract'], 'contract Café interface', body, provenance))
        for pos, passage in enumerate(passages):
            anchor = passage['anchor']
            target = url + '#' + anchor
            citation_url = url.removesuffix('.html') + '-' + anchor + '.citation.html'
            identity_href = url.removesuffix('.html') + '-' + anchor + '.citation.json'
            presentation = citation_presentation({'revision': revision, 'manuscript': 'content/manuscript.md',
                'document': path_string, 'anchor': anchor}, identity_href, title=f['title'],
                authors='Riverhog contributors', published=day, edition_number=edition, document_title=doc['title'])
            p = db.execute('''INSERT INTO passages(edition_id,document_id,work_id,anchor,title,kind,level,
                position,parent,scope,url,citation_url,citation_json) VALUES(?,?,1,?,?,?,?,?,?,?,?,?,?)''',
                (edition, d, anchor, passage['title'], passage.get('kind','section'),passage.get('level',2),pos,
                 passage.get('parent'),passage.get('scope','document'),target,citation_url,json.dumps(presentation, ensure_ascii=False))).lastrowid
            db.execute('INSERT INTO passage_records VALUES(?,1,?,?,?)', (p, edition, d, target))
            db.execute('INSERT INTO passage_fts(rowid,title,authors,abstract,labels,body,git) VALUES(?,?,?,?,?,?,?)',
                       (p, title + ' — ' + passage['title'], 'Riverhog contributors', f['abstract'],
                        'contract Café interface', passage['body'], provenance + ' ' + anchor))
        for key, value in metadata.items():
            for val in value if isinstance(value, list) else [value]:
                pair = (key, val)
                if pair not in facets:
                    facets[pair] = db.execute('INSERT INTO facet_values(name,value) VALUES(?,?)', pair).lastrowid
                db.execute('INSERT INTO document_facets VALUES(?,?)', (d, facets[pair]))
    finish(db)
    return path


def finish(db):
    for table in ('search_fts', 'passage_fts', 'document_fts'):
        db.execute(f"INSERT INTO {table}({table}) VALUES('optimize')")
    db.commit()
    db.execute('VACUUM')
    assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert not db.execute('PRAGMA foreign_key_check').fetchall()
    db.close()


def external(source, output, *, citation_compact=False, segmented=False):
    # Offline conversion is only the experiment. A production builder would
    # write these records directly, without constructing a second full DB.
    output.unlink(missing_ok=True)
    src = sqlite3.connect(source)
    db = sqlite3.connect(output)
    src.backup(db)
    db.execute('PRAGMA foreign_keys=OFF')
    db.execute('PRAGMA synchronous=OFF')
    db.executescript('''
      DROP TABLE search_fts;
      DROP TABLE passage_fts;
      CREATE TABLE text_pool(id INTEGER PRIMARY KEY,sha256 TEXT UNIQUE NOT NULL,value TEXT NOT NULL);
      CREATE TABLE document_bodies(record_id INTEGER PRIMARY KEY,body_id INTEGER NOT NULL REFERENCES text_pool(id));
      CREATE TABLE passage_bodies(record_id INTEGER PRIMARY KEY,body_id INTEGER NOT NULL REFERENCES text_pool(id));
    ''')
    seen = {}
    for table, mapping in [('search_fts', 'document_bodies'), ('passage_fts', 'passage_bodies')]:
        for rowid, body in src.execute(f'SELECT rowid,body FROM {table} ORDER BY rowid'):
            key = H(body)
            if key not in seen:
                seen[key] = db.execute('INSERT INTO text_pool(sha256,value) VALUES(?,?)', (key, body)).lastrowid
            else:
                assert db.execute('SELECT value FROM text_pool WHERE id=?', (seen[key],)).fetchone()[0] == body
            db.execute(f'INSERT INTO {mapping} VALUES(?,?)', (rowid, seen[key]))
    if citation_compact:
        # Factor citation presentation without altering any parsed JSON value.
        db.execute('''CREATE TABLE document_citation_parts(document_id INTEGER PRIMARY KEY,
          description TEXT,source_prefix TEXT,href_prefix TEXT,href_suffix TEXT,label TEXT)''')
        db.execute('''CREATE TABLE passage_shape(id INTEGER PRIMARY KEY,anchor TEXT,title TEXT,kind TEXT,
          level INTEGER,position INTEGER,parent TEXT,scope TEXT)''')
        db.execute('''CREATE TABLE passage_occurrence(id INTEGER PRIMARY KEY,document_id INTEGER NOT NULL,
          shape_id INTEGER NOT NULL,citation_url TEXT NOT NULL)''')
        shapes = {}
        cited = set()
        for row in src.execute('SELECT id,document_id,anchor,title,kind,level,position,parent,scope,citation_url,citation_json FROM passages ORDER BY id'):
            pid, did, anchor, title, kind, level, pos, parent, scope, cite_url, cite_json = row
            shape = (anchor, title, kind, level, pos, parent, scope)
            if shape not in shapes:
                shapes[shape] = db.execute('INSERT INTO passage_shape(anchor,title,kind,level,position,parent,scope) VALUES(?,?,?,?,?,?,?)', shape).lastrowid
            citation = json.loads(cite_json)
            assert citation['source'][-2:] == [['text', ' · '], ['code', '#' + anchor]]
            pre, sep, post = citation['href'].rpartition(anchor)
            assert sep == anchor
            parts = (J(citation['description']), J(citation['source'][:-2]), pre, post, citation['label'])
            if did not in cited:
                db.execute('INSERT INTO document_citation_parts VALUES(?,?,?,?,?,?)', (did, *parts))
                cited.add(did)
            else:
                assert db.execute('SELECT description,source_prefix,href_prefix,href_suffix,label FROM document_citation_parts WHERE document_id=?', (did,)).fetchone() == parts
            db.execute('INSERT INTO passage_occurrence VALUES(?,?,?,?)', (pid, did, shapes[shape], cite_url))
        db.execute('DROP TABLE passages')
        db.execute('CREATE INDEX passage_occurrence_document ON passage_occurrence(document_id,shape_id)')
        db.executescript('''CREATE VIEW passages AS
          SELECT p.id,d.edition_id,d.id AS document_id,d.work_id,s.anchor,s.title,s.kind,s.level,
            s.position,s.parent,s.scope,d.url||'#'||s.anchor AS url,p.citation_url,
            json_object('description',json(c.description),
              'source',json_insert(c.source_prefix,'$[#]',json_array('text',' · '),'$[#]',json_array('code','#'||s.anchor)),
              'href',c.href_prefix||s.anchor||c.href_suffix,'label',c.label) AS citation_json
          FROM passage_occurrence p JOIN documents d ON d.id=p.document_id
          JOIN passage_shape s ON s.id=p.shape_id JOIN document_citation_parts c ON c.document_id=d.id;
        ''')
    common = "CASE WHEN d.title=e.title THEN e.title ELSE e.title||' — '||d.title END"
    document_body = 't.value'
    document_body_join = 'JOIN document_bodies b ON b.record_id=r.id JOIN text_pool t ON t.id=b.body_id'
    if segmented:
        # This fixture's exact document text is the ordered passage text joined
        # by two LF bytes. Check that equation, rather than assume it, before
        # deleting the separately stored complete document value. A production
        # adapter must retain arbitrary interstitial text/overlapping sections.
        document_body = """(SELECT group_concat(value,char(10)||char(10)) FROM
            (SELECT t.value FROM passages p JOIN passage_bodies b ON b.record_id=p.id
             JOIN text_pool t ON t.id=b.body_id WHERE p.document_id=d.id ORDER BY p.position))"""
        model = db.execute("SELECT value FROM metadata WHERE key='experiment-body-layout'").fetchone()[0]
        if model == 'title-space-passages':
            document_body = "d.title||' '||" + document_body.replace('char(10)||char(10)', "' '")
        document_body_join = ''
        db.execute('DROP TABLE document_bodies')
        db.execute('DELETE FROM text_pool WHERE id NOT IN (SELECT body_id FROM passage_bodies)')
    db.executescript(f'''
      CREATE VIEW search_fields AS SELECT r.id,{common} AS title,f.authors,f.abstract,f.labels,{document_body} AS body,
        f.git_before_path||d.path||f.git_after_path AS git FROM search_records r
        JOIN documents d ON d.id=r.document_id JOIN editions e ON e.id=d.edition_id
        JOIN edition_search_fields f ON f.edition_id=e.id {document_body_join};
      CREATE VIEW passage_fields AS SELECT p.id,{common}||' — '||p.title AS title,f.authors,f.abstract,f.labels,
        t.value AS body,f.git_before_path||d.path||f.git_after_path||' '||p.anchor AS git
        FROM passages p JOIN documents d ON d.id=p.document_id JOIN editions e ON e.id=d.edition_id
        JOIN edition_search_fields f ON f.edition_id=e.id JOIN passage_bodies b ON b.record_id=p.id
        JOIN text_pool t ON t.id=b.body_id;
    ''')
    for table, view in [('search_fts','search_fields'),('passage_fts','passage_fields')]:
        cols = ','.join(FIELDS)
        db.execute(f"CREATE VIRTUAL TABLE {table} USING fts5({cols},content='{view}',content_rowid='id',detail=full,columnsize=1,tokenize='unicode61 remove_diacritics 2')")
        count = 0
        for left, right in zip(src.execute(f'SELECT rowid,{cols} FROM {table} ORDER BY rowid'),
                               db.execute(f'SELECT id,{cols} FROM {view} ORDER BY id'), strict=True):
            assert left == right, (table, left[0])
            db.execute(f'INSERT INTO {table}(rowid,{cols}) VALUES(?,?,?,?,?,?,?)', right)
            count += 1
        print(table, 'exact field comparisons', count, flush=True)
    if citation_compact:
        for left, right in zip(src.execute('SELECT * FROM passages ORDER BY id'), db.execute('SELECT * FROM passages ORDER BY id'), strict=True):
            assert left[:-1] == right[:-1] and json.loads(left[-1]) == json.loads(right[-1])
    src.close()
    # SQLite foreign_key_check cannot target a view; the compatibility view's
    # IDs were checked exhaustively above. Production schema needs explicit FKs.
    for table in ('search_fts', 'passage_fts'):
        db.execute(f"INSERT INTO {table}({table},rank) VALUES('integrity-check',1)")
    if citation_compact:
        db.execute('DROP TABLE passage_records')
        db.execute('CREATE VIEW passage_records AS SELECT id,work_id,edition_id,document_id,url FROM passages')
    finish(db)
    return output


def describe(path):
    start = time.perf_counter()
    zipped = path.with_suffix('.sqlite.gz')
    with path.open('rb') as inp, zipped.open('wb') as out, gzip.GzipFile(fileobj=out, mode='wb', filename='', mtime=0, compresslevel=9) as dst:
        shutil.copyfileobj(inp, dst, 1 << 20)
    with sqlite3.connect(path) as db:
        tables = dict(db.execute('SELECT name,sum(pgsize) FROM dbstat GROUP BY name'))
        present = {row[0] for row in db.execute('SELECT name FROM sqlite_schema')}
        counts = {t: db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in ('editions','documents','passages') if t in present}
    return {'raw_bytes': path.stat().st_size, 'gzip9_bytes': zipped.stat().st_size,
            'raw_sha256': hashlib.file_digest(path.open('rb'), 'sha256').hexdigest(),
            'gzip9_sha256': hashlib.file_digest(zipped.open('rb'), 'sha256').hexdigest(),
            'gzip_seconds': time.perf_counter() - start, 'counts': counts, 'table_bytes': tables}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--guavajam', type=Path, required=True)
    p.add_argument('--corpus', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--editions', type=int, default=5)
    p.add_argument('--change-rate', type=float, default=.05)
    p.add_argument('--metadata-mode', choices=('stable','edition'), default='edition')
    p.add_argument('--limit', type=int)
    p.add_argument('--variants', default='baseline,external,compact')
    args = p.parse_args()
    args.guavajam = args.guavajam.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    corpus = [json.loads(s) for s in args.corpus.read_text().splitlines()]
    if args.limit:
        corpus = corpus[:args.limit]
    report = {'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version,
              'guavajam_commit': subprocess.check_output(['git','-C',str(args.guavajam),'rev-parse','HEAD'], text=True).strip(),
              'corpus_sha256': hashlib.file_digest(args.corpus.open('rb'),'sha256').hexdigest(),
              'documents_per_edition': len(corpus), 'editions': args.editions,
              'change_rate': args.change_rate, 'metadata_mode':args.metadata_mode,
              'navigation_source':'production supporting parser and navigation extractor' if 'navigation' in corpus[0] else 'native heading segmentation model',
              'source_identity': ('SHA-256 of serialized supporting GFM bytes; every initial digest and byte count agrees with the production-parsed fixture; later editions use the same literal-source serialization with modeled updates' if 'navigation' in corpus[0] else 'modeled content digest; not serialized supporting-source identity'),
              'metadata_text':'modeled visible metadata introduction included in document and passage text',
              'history': 'deterministic modeled updates; not real releases', 'variants': {}}
    if (args.output / 'report.json').exists():
        previous = json.loads((args.output / 'report.json').read_text())
        assert all(previous[k] == report[k] for k in ('corpus_sha256','editions','documents_per_edition','change_rate','metadata_mode'))
        report['variants'].update(previous['variants'])
    def save():
        (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    source = args.output / 'baseline.sqlite'
    for variant in args.variants.split(','):
        start = time.perf_counter()
        if variant == 'baseline':
            path = baseline(args, corpus)
        else:
            path = external(source, args.output / (variant + '.sqlite'), citation_compact=variant in {'compact','segmented'}, segmented=variant=='segmented')
        seconds = time.perf_counter() - start
        report['variants'][variant] = {**describe(path), 'construction_seconds': seconds}
        save()
        print(variant, {k:v for k,v in report['variants'][variant].items() if k != 'table_bytes'}, flush=True)


if __name__ == '__main__':
    main()
