"""Occurrence postings with shared source blocks and document citation context."""
from __future__ import annotations

import hashlib
import json
import re

from guavajam.catalog import CatalogError, encoded
from guavajam.bibliography import ordered_authors, author_text
from .citation import citation_presentation
from .citation_routes import citation_fragment, passage_json
from .pagination import PAGE_SIZE

WINDOW_CHARS = 1024

SCHEMA = """
    CREATE TABLE source_texts (id INTEGER PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL, value TEXT NOT NULL);
    CREATE TABLE source_contents (id INTEGER PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL);
    CREATE TABLE source_content_map (source_sha256 TEXT PRIMARY KEY, content_id INTEGER REFERENCES source_contents(id));
    CREATE TABLE source_blocks (
        content_id INTEGER REFERENCES source_contents(id), position INTEGER, scope TEXT,
        text_id INTEGER REFERENCES source_texts(id), PRIMARY KEY(content_id,position)
    );
    CREATE INDEX blocks_by_text ON source_blocks(text_id,content_id);
    CREATE TABLE evidence_windows (
        id INTEGER PRIMARY KEY, text_id INTEGER REFERENCES source_texts(id),
        position INTEGER, end INTEGER, text TEXT NOT NULL, UNIQUE(text_id,position)
    );
    CREATE VIRTUAL TABLE window_fts USING fts5(text,content='evidence_windows',
        content_rowid='id',detail=full,columnsize=1,tokenize='unicode61 remove_diacritics 2');
    CREATE TABLE edition_contexts (
        edition_id INTEGER PRIMARY KEY REFERENCES editions(id),
        title TEXT, authors TEXT, abstract TEXT, labels TEXT, git_before TEXT, git_after TEXT
    );
    CREATE TABLE citation_frames (id INTEGER PRIMARY KEY, sha256 TEXT UNIQUE, description TEXT, source TEXT, label TEXT);
    CREATE TABLE document_contents (
        document_id INTEGER PRIMARY KEY REFERENCES documents(id),
        content_id INTEGER REFERENCES source_contents(id),
        citation_frame_id INTEGER REFERENCES citation_frames(id),
        citation_key TEXT, json_prefix TEXT
    );
    CREATE TABLE passage_shapes (
        id INTEGER PRIMARY KEY, sha256 TEXT UNIQUE, anchor TEXT, title TEXT,
        kind TEXT, level INTEGER, position INTEGER, parent TEXT, scope TEXT,
        block_start INTEGER, block_end INTEGER
    );
    CREATE TABLE passage_occurrences (
        id INTEGER PRIMARY KEY, edition_id INTEGER REFERENCES editions(id),
        document_id INTEGER REFERENCES documents(id), work_id INTEGER REFERENCES works(id),
        shape_id INTEGER REFERENCES passage_shapes(id), citation_position INTEGER,
        UNIQUE(document_id,shape_id)
    );
    CREATE INDEX occurrences_by_document ON passage_occurrences(document_id,id);
    CREATE INDEX occurrences_by_edition ON passage_occurrences(edition_id,id);
    CREATE VIEW passages AS SELECT
        p.id,p.edition_id,p.document_id,p.work_id,s.anchor,s.title,s.kind,s.level,s.position,s.parent,s.scope,
        d.url || '#' || s.anchor AS url,
        'editions/' || e.revision || '/read/' ||
          CASE WHEN p.citation_position<{page_size} THEN 'passages.html'
          ELSE 'passages/page/' || (p.citation_position/{page_size}+1) || '.html' END ||
          '#' || dc.citation_key || s.anchor AS citation_url,
        json_object('description',json(c.description),
          'source',json_insert(json(c.source),'$[#]',json_array('text',' · '),'$[#]',json_array('code','#' || s.anchor)),
          'href','editions/' || e.revision || '/read/' || dc.json_prefix || s.anchor || '.json',
          'label',c.label) AS citation_json
        FROM passage_occurrences p
        JOIN passage_shapes s ON s.id=p.shape_id JOIN documents d ON d.id=p.document_id
        JOIN editions e ON e.id=p.edition_id JOIN document_contents dc ON dc.document_id=d.id
        JOIN citation_frames c ON c.id=dc.citation_frame_id;
    CREATE VIEW passage_content AS SELECT
        p.id, CASE WHEN d.title!=e.title THEN e.title || ' — ' || d.title ELSE e.title END || ' — ' || s.title AS title,
        e.authors,e.abstract,e.labels,
        COALESCE((SELECT group_concat(value,' ') FROM (
            SELECT t.value FROM source_blocks b JOIN source_texts t ON t.id=b.text_id
            WHERE b.content_id=dc.content_id AND b.scope=s.scope
              AND b.position>=s.block_start AND b.position<s.block_end AND t.value!=''
            ORDER BY b.position)), '') AS body,
        e.git_before || ' ' || d.path || ' ' || e.git_after || ' ' || s.anchor AS git
        FROM passage_occurrences p JOIN passage_shapes s ON s.id=p.shape_id
        JOIN documents d ON d.id=p.document_id JOIN document_contents dc ON dc.document_id=d.id
        JOIN edition_contexts e ON e.edition_id=p.edition_id;
    CREATE VIEW passage_windows AS SELECT p.id AS passage_id,w.id AS window_id,
        b.position AS block_position,w.position AS window_position
        FROM passage_occurrences p JOIN passage_shapes s ON s.id=p.shape_id
        JOIN document_contents d ON d.document_id=p.document_id
        JOIN source_blocks b ON b.content_id=d.content_id AND b.scope=s.scope
          AND b.position>=s.block_start AND b.position<s.block_end
        JOIN evidence_windows w ON w.text_id=b.text_id;
""".format(page_size=PAGE_SIZE)


def add_edition(connection, identifier, entry, edition, position, facts):
    from .search_index import _authors, _labels
    before = ' '.join([entry['repository'], edition['revision'], edition['tree'],
                       edition['published'], edition['message']])
    after = ' '.join(['v' + str(position + 1), facts.get('rights', ''), facts.get('schema_type', ''),
                     json.dumps(facts.get('identifiers', {}), ensure_ascii=False),
                     json.dumps(ordered_authors(facts), ensure_ascii=False)])
    connection.execute('INSERT INTO edition_contexts VALUES (?,?,?,?,?,?,?)',
        (identifier, facts['title'], _authors(facts), facts.get('abstract', ''), _labels(facts), before, after))


def windows(text):
    """Whole whitespace-delimited units cannot invent prefixes inside tokens.

    Oversized units use native exact-field evidence as a bounded per-input
    fallback. They never create truncated tokens in this auxiliary index.
    """
    start, end = None, None
    for match in re.finditer(r'\S+', text):
        if start is not None and match.end() - start > WINDOW_CHARS:
            yield start, end, text[start:end]
            start = end = None
        if match.end() - match.start() > WINDOW_CHARS:
            continue
        if start is None:
            start = match.start()
        end = match.end()
    if start is not None:
        yield start, end, text[start:end]


def _text(connection, value):
    digest = hashlib.sha256(value.encode('utf-8')).hexdigest()
    row = connection.execute('SELECT id FROM source_texts WHERE sha256=?', (digest,)).fetchone()
    if row:
        return row[0], digest
    identifier = connection.execute('INSERT INTO source_texts VALUES (NULL,?,?)', (digest, value)).lastrowid
    for start, end, excerpt in windows(value):
        window = connection.execute('INSERT INTO evidence_windows VALUES (NULL,?,?,?,?)',
                                    (identifier, start, end, excerpt)).lastrowid
        connection.execute('INSERT INTO window_fts(rowid,text) VALUES (?,?)', (window, excerpt))
    return identifier, digest


def _content(connection, document):
    row = connection.execute('SELECT content_id FROM source_content_map WHERE source_sha256=?',
                             (document['source_sha256'],)).fetchone()
    if row:
        return row[0]
    rows, digest, previous = [], hashlib.sha256(), -1
    for block in document['blocks']:
        position, scope, text = block.get('position'), block.get('scope'), block.get('text')
        if (type(position) is not int or position <= previous or not isinstance(scope, str)
                or not isinstance(text, str) or len(text.encode('utf-8')) > 8 * 1024 * 1024):
            raise CatalogError('search: invalid ordered source block')
        previous = position
        identifier, text_digest = _text(connection, text)
        digest.update(encoded([position, scope, text_digest]))
        rows.append((position, scope, identifier))
    key = digest.hexdigest()
    row = connection.execute('SELECT id FROM source_contents WHERE sha256=?', (key,)).fetchone()
    if row:
        identifier = row[0]
    else:
        identifier = connection.execute('INSERT INTO source_contents VALUES (NULL,?)', (key,)).lastrowid
        connection.executemany('INSERT INTO source_blocks VALUES (?,?,?,?)',
                               ((identifier, *row) for row in rows))
    connection.execute('INSERT INTO source_content_map VALUES (?,?)', (document['source_sha256'], identifier))
    return identifier


def add_document(connection, identifier, source, document, edition, position, facts):
    content_id = _content(connection, document)
    record = {'revision': edition['revision'], 'manuscript': 'content/manuscript.md', 'document': source}
    presentation = citation_presentation(record, '', title=facts['title'],
        authors=author_text(ordered_authors(facts)), published=edition['published'], edition_number=position + 1,
        document_title=document['title'] if source != 'content/manuscript.md' else '')
    key = hashlib.sha256(encoded(presentation)).hexdigest()
    row = connection.execute('SELECT id FROM citation_frames WHERE sha256=?', (key,)).fetchone()
    frame = row[0] if row else connection.execute('INSERT INTO citation_frames VALUES (NULL,?,?,?,?)',
        (key, json.dumps(presentation['description'], ensure_ascii=False),
         json.dumps(presentation['source'], ensure_ascii=False), presentation['label'])).lastrowid
    prefix = passage_json(document['view'], 'placeholder').rsplit('/', 1)[0] + '/'
    connection.execute('INSERT INTO document_contents VALUES (?,?,?,?,?)',
                       (identifier, content_id, frame, citation_fragment(source, ''), prefix))


def add_passage(connection, edition_id, document_id, work_id, record, citation_position):
    fields = ('anchor','title','kind','level','position','parent','scope','block_start','block_end')
    shape = {key: record[key] for key in fields}
    if (type(shape['block_start']) is not int or type(shape['block_end']) is not int
            or shape['block_start'] < 0 or shape['block_end'] <= shape['block_start']):
        raise CatalogError('search: invalid passage source-block range')
    key = hashlib.sha256(encoded(shape)).hexdigest()
    row = connection.execute('SELECT id FROM passage_shapes WHERE sha256=?', (key,)).fetchone()
    shape_id = row[0] if row else connection.execute(
        'INSERT INTO passage_shapes VALUES (NULL,' + ','.join('?' for _ in range(10)) + ')',
        (key, *(shape[field] for field in fields))).lastrowid
    identifier = connection.execute('INSERT INTO passage_occurrences VALUES (NULL,?,?,?,?,?)',
        (edition_id, document_id, work_id, shape_id, citation_position)).lastrowid
    values = connection.execute('SELECT title,authors,abstract,labels,body,git FROM passage_content WHERE id=?',
                                (identifier,)).fetchone()
    if values[4] != record['text']:
        raise CatalogError('search: ordered source blocks do not reproduce exact passage text')
    connection.execute('INSERT INTO passage_fts(rowid,title,authors,abstract,labels,body,git) VALUES (?,?,?,?,?,?,?)',
                       (identifier, *values))


def qualify(connection):
    connection.execute("INSERT INTO window_fts(window_fts) VALUES ('optimize')")
    for table in ['passage_fts', 'window_fts']:
        connection.execute('INSERT INTO ' + table + '(' + table + ",rank) VALUES ('integrity-check',1)")
