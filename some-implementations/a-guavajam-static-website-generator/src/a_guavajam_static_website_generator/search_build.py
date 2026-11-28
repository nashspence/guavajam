"""Build one physical search corpus from verified generator inputs.

Search derivatives own no publication authority. Collection and Versions never
read source bodies; edition scopes never ingest another edition's occurrences.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote

from guavajam.bibliography import ordered_authors, author_text
from guavajam.catalog import CatalogError, encoded, MAX_BYTES as MAX_CATALOG_BYTES

from .citation import citation_presentation
from .citation_routes import citation_href, passage_json, source_offsets
from .search_artifact import emit_download
from . import passage_storage

SCOPES = frozenset({'collection', 'versions', 'documents', 'passages', 'history'})
SCHEMA = 'guavajam.search.v1'

COMMON = """
    PRAGMA page_size=4096;
    PRAGMA journal_mode=DELETE;
    PRAGMA foreign_keys=ON;
    PRAGMA temp_store=FILE;
    PRAGMA cache_size=-8192;
    PRAGMA user_version=1;
    CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE repositories (
        id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE,
        path TEXT NOT NULL UNIQUE, tip TEXT NOT NULL
    );
    CREATE TABLE works (
        id INTEGER PRIMARY KEY, repository_id INTEGER NOT NULL UNIQUE REFERENCES repositories(id),
        title TEXT NOT NULL, authors TEXT NOT NULL, abstract TEXT NOT NULL,
        language TEXT NOT NULL, latest_published TEXT NOT NULL, latest_revision TEXT NOT NULL,
        edition_count INTEGER NOT NULL, labels TEXT NOT NULL, facts_json TEXT NOT NULL
    );
    CREATE TABLE keywords (
        work_id INTEGER NOT NULL REFERENCES works(id), name TEXT NOT NULL,
        PRIMARY KEY(work_id, name)
    );
    CREATE TABLE subjects (
        work_id INTEGER NOT NULL REFERENCES works(id), key TEXT NOT NULL,
        name TEXT NOT NULL, label TEXT NOT NULL, PRIMARY KEY(work_id, key)
    );
    CREATE INDEX works_by_date ON works(latest_published, id);
    CREATE INDEX works_by_title ON works(title COLLATE NOCASE, id);
    CREATE INDEX keywords_by_name ON keywords(name, work_id);
    CREATE INDEX subjects_by_key ON subjects(key, work_id);
"""
EDITIONS = """
    CREATE TABLE editions (
        id INTEGER PRIMARY KEY, work_id INTEGER NOT NULL REFERENCES works(id),
        position INTEGER NOT NULL, revision TEXT NOT NULL, tree TEXT NOT NULL,
        published TEXT NOT NULL, name TEXT NOT NULL, title TEXT NOT NULL,
        message TEXT NOT NULL, facts_json TEXT NOT NULL, UNIQUE(work_id, revision)
    );
    CREATE INDEX editions_by_work ON editions(work_id, position);
    CREATE INDEX editions_by_date ON editions(published, position);
"""
DOCUMENTS = """
    CREATE TABLE documents (
        id INTEGER PRIMARY KEY, edition_id INTEGER NOT NULL REFERENCES editions(id),
        work_id INTEGER NOT NULL REFERENCES works(id), title TEXT NOT NULL,
        path TEXT NOT NULL, url TEXT NOT NULL, language TEXT NOT NULL,
        source_sha256 TEXT NOT NULL, abstract TEXT NOT NULL, fields_json TEXT NOT NULL,
        UNIQUE(edition_id, path)
    );
    CREATE INDEX documents_by_edition ON documents(edition_id, path);
    CREATE TABLE document_fields (
        document_id INTEGER NOT NULL REFERENCES documents(id), key TEXT NOT NULL,
        kind TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY(document_id, key)
    );
    CREATE TABLE document_facets (
        document_id INTEGER NOT NULL REFERENCES documents(id), key TEXT NOT NULL,
        value TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY(document_id, key, value)
    );
    CREATE INDEX facets_by_value ON document_facets(key, value, document_id);
"""
@lru_cache(maxsize=1)
def generation_identity():
    import guavajam
    identity = {'sqlite': sqlite3.sqlite_version, 'schema': SCHEMA}
    for name, root in [('guavajam', Path(guavajam.__file__).parent), ('generator', Path(__file__).parent)]:
        identity[name] = {
            path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob('*'))
            if path.is_file() and path.suffix in {'.py', '.js', '.mjs', '.css', '.json'}
        }
    return hashlib.sha256(encoded(identity)).hexdigest()


def _fts(connection, name, fields, *, content=None):
    external = (",content='" + content + "',content_rowid='id'") if content else ''
    connection.execute('CREATE VIRTUAL TABLE ' + name + ' USING fts5(' + ','.join(fields) +
                       external + ",detail=full,columnsize=1,tokenize='unicode61 remove_diacritics 2')")


FTS_FIELDS = {
    'work_fts': ['title', 'authors', 'abstract', 'labels'],
    'version_fts': ['name', 'title', 'message'],
    'document_fts': ['title', 'abstract'],
    'passage_fts': ['title', 'authors', 'abstract', 'labels', 'body', 'git'],
}


def _insert(connection, table, values):
    columns = ('(rowid,' + ','.join(FTS_FIELDS[table]) + ')') if table in FTS_FIELDS else ''
    return connection.execute('INSERT INTO ' + table + columns + ' VALUES (' +
                              ','.join('?' for _ in values) + ')', values).lastrowid


def _facts(root, edition):
    from .search_index import _json
    sha = edition['revision']
    if not re.fullmatch(r'[a-f0-9]{40}', sha):
        raise CatalogError('search: invalid edition revision')
    bibliography = _json(root, 'editions/' + sha + '/BIBLIOGRAPHY.json', maximum=1024 * 1024)
    if (bibliography.get('schema') != 'guavajam.search-bibliography.v1'
            or bibliography.get('revision') != sha or bibliography.get('preview') is not False
            or not isinstance(bibliography.get('facts'), dict)):
        raise CatalogError('search: bibliography source identity mismatch')
    return bibliography['facts']


def _schema(connection, scope):
    connection.executescript(COMMON)
    if scope == 'collection':
        _fts(connection, 'work_fts', ['title', 'authors', 'abstract', 'labels'], content='works')
    else:
        connection.executescript(EDITIONS)
        if scope == 'versions':
            _fts(connection, 'version_fts', ['name', 'title', 'message'], content='editions')
        else:
            connection.executescript(DOCUMENTS)
            if scope == 'documents':
                _fts(connection, 'document_fts', ['title', 'abstract'], content='documents')
            else:
                from .passage_storage import SCHEMA as PASSAGE_SCHEMA
                connection.executescript(PASSAGE_SCHEMA)
                _fts(connection, 'passage_fts', ['title', 'authors', 'abstract', 'labels', 'body', 'git'], content='passage_content')


def _work(connection, number, entry, facts, edition, count, title):
    from .search_index import _authors, _labels, subject_key, subject_label
    _insert(connection, 'repositories', (number, entry['repository'], entry['path'], entry['tip']))
    _insert(connection, 'works', (
        number, number, facts.get('title', title), _authors(facts), facts.get('abstract', ''),
        facts.get('language', ''), edition['published'] if edition else '',
        edition['revision'] if edition else '', count, _labels(facts), encoded(facts).decode('utf-8')))
    for keyword in dict.fromkeys(facts.get('keywords', [])):
        _insert(connection, 'keywords', (number, keyword))
    subjects = {subject_key(value): value for value in facts.get('subjects', [])}
    for key, subject in subjects.items():
        _insert(connection, 'subjects', (number, key, subject['subject'], subject_label(subject)))


def _document(connection, edition_id, work_id, source, detail, prefix, sha):
    fields = detail.get('metadata', [])
    identifier = _insert(connection, 'documents', (
        None, edition_id, work_id, detail['title'], source,
        quote(prefix + 'editions/' + sha + '/read/' + detail['view'], safe='/'),
        detail['language'], detail['source_sha256'], detail.get('abstract', ''),
        encoded(fields).decode('utf-8')))
    for ordinal, (key, value) in enumerate(fields):
        is_list = isinstance(value, list)
        _insert(connection, 'document_fields', (identifier, key, 'list' if is_list else 'text', ordinal))
        for position, literal in enumerate(value if is_list else [value]):
            connection.execute('INSERT OR IGNORE INTO document_facets VALUES (?,?,?,?)',
                               (identifier, key, literal, position))
    return identifier


def build(site, entries, *, filename, scope, revision=None):
    from .search_index import _json, _owned, _navigation, _authors, _labels
    site = Path(site).absolute()
    entries = list(entries)
    if scope not in SCOPES:
        raise CatalogError('search: invalid index scope')
    if scope != 'collection' and (len(entries) != 1 or entries[0]['path'] != ''):
        raise CatalogError('search: a work scope requires exactly its own repository')
    if scope in {'documents', 'passages'}:
        if not isinstance(revision, str) or not re.fullmatch(r'[a-f0-9]{40}', revision):
            raise CatalogError('search: an edition scope requires an exact revision')
    elif revision is not None:
        raise CatalogError('search: this scope does not select an edition')
    database = _owned(site, filename)
    if database.name not in {'index.sqlite', 'versions.sqlite', 'documents.sqlite',
                             'passages.sqlite', 'history.sqlite'}:
        raise CatalogError('search: invalid database filename')
    if database.exists() or database.is_symlink():
        raise CatalogError('search: destination already exists')
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    table = {'collection': 'work_fts', 'versions': 'version_fts',
             'documents': 'document_fts', 'passages': 'passage_fts', 'history': 'passage_fts'}[scope]
    try:
        _schema(connection, scope)
        with connection:
            generation = generation_identity()
            for key, value in [('schema', SCHEMA), ('scope', scope), ('generation', generation),
                               ('sqlite', sqlite3.sqlite_version), ('revision', revision or '')]:
                _insert(connection, 'metadata', (key, value))
            for number, entry in enumerate(entries, 1):
                root = site if scope != 'collection' else _owned(site, entry['path'])
                catalog = _json(root, 'CATALOG.json', maximum=MAX_CATALOG_BYTES)
                if catalog['tip'] != entry['tip']:
                    raise CatalogError('search: generated source verification mismatch')
                editions = catalog['editions']
                selected = ([(position, item) for position, item in enumerate(editions)
                             if item['revision'] == revision] if revision else
                            list(enumerate(editions[-1:], max(0, len(editions) - 1))) if scope == 'collection'
                            else list(enumerate(editions)))
                if revision and len(selected) != 1:
                    raise CatalogError('search: selected edition is not accepted')
                latest = selected[-1][1] if selected else None
                latest_facts = _facts(root, latest) if latest else {}
                # Edition-local descriptors do not change when later editions append.
                count = selected[-1][0] + 1 if revision else len(editions)
                _work(connection, number, {**entry, 'tip': revision or entry['tip']},
                      latest_facts, latest, count, 'Unpublished work')
                if scope == 'collection':
                    _insert(connection, 'work_fts', (
                        number, latest_facts.get('title', 'Unpublished work'), _authors(latest_facts),
                        latest_facts.get('abstract', ''), _labels(latest_facts)))
                    continue
                for position, edition in selected:
                    sha = edition['revision']
                    facts = _facts(root, edition)
                    edition_id = _insert(connection, 'editions', (
                        None, number, position, sha, edition['tree'], edition['published'],
                        'v' + str(position + 1), facts['title'], edition['message'],
                        encoded(facts).decode('utf-8')))
                    if scope == 'versions':
                        _insert(connection, 'version_fts', (
                            edition_id, 'v' + str(position + 1), facts['title'], edition['message']))
                        continue
                    if scope in {'passages', 'history'}:
                        passage_storage.add_edition(connection, edition_id, entry, edition, position, facts)
                    read = _owned(root, 'editions/' + sha + '/read')
                    view = _json(read, 'VIEW.json', maximum=64 * 1024 * 1024)
                    if (view.get('preview') is not False or view['canonical']['revision'] != sha
                            or view['facts'] != facts):
                        raise CatalogError('search: non-public or mismatched verified view')
                    navigation, _ = _navigation(read, view)
                    starts = source_offsets(navigation)
                    prefix = entry['path'] + '/' if entry['path'] else ''
                    for source in sorted(navigation['documents']):
                        detail = navigation['documents'][source]
                        if scope == 'documents' and source == 'content/manuscript.md':
                            continue
                        document_id = _document(connection, edition_id, number, source, detail, prefix, sha)
                        if scope == 'documents':
                            _insert(connection, 'document_fts', (
                                document_id, detail['title'], detail.get('abstract', '')))
                            continue
                        passage_storage.add_document(connection, document_id, source, detail, edition, position, facts)
                        for anchor in detail['anchors']:
                            passage_storage.add_passage(connection, edition_id, document_id, number,
                                anchor, starts[source] + anchor['position'])
            connection.execute('INSERT INTO ' + table + '(' + table + ") VALUES ('optimize')")
            if scope in {'passages', 'history'}:
                passage_storage.qualify(connection)
            if scope in {'collection', 'versions', 'documents'}:
                connection.execute('INSERT INTO ' + table + '(' + table + ",rank) VALUES ('integrity-check',1)")
        connection.execute('VACUUM')
        if (connection.execute('PRAGMA integrity_check').fetchone() != ('ok',)
                or connection.execute('PRAGMA foreign_key_check').fetchall()):
            raise CatalogError('search: database integrity check failed')
        connection.close()
        emit_download(database)
        return database
    except Exception:
        connection.close()
        database.unlink(missing_ok=True)
        raise
