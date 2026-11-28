"""Bounded immutable prefix discovery, independent of the search database."""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from guavajam.catalog import CatalogError, encoded

SCHEMA = 'guavajam.search-facets.v1'
PAGE_SIZE = 32
MAX_OPTION_BYTES = 512 * 1024
MAX_NODE_BYTES = 1024 * 1024
MAX_DEPTH = 16


def _write(directory, stem, value):
    data = encoded(value)
    if len(data) > MAX_NODE_BYTES:
        raise CatalogError('search facets: option node exceeds the 1-MiB operational budget')
    digest = hashlib.sha256(data).hexdigest()
    name = stem + '.facets.' + digest + '.json'
    path = directory / name
    if path.is_symlink() or path.exists() and path.read_bytes() != data:
        raise CatalogError('search facets: immutable node differs')
    path.write_bytes(data)
    return {'url': name, 'sha256': digest, 'size': len(data)}


def _tree(connection, directory, stem, identity, records):
    """Keep only one option page and one parent page in Python memory."""
    connection.execute('CREATE TEMP TABLE IF NOT EXISTS option_nodes '
                       '(level INTEGER, ordinal INTEGER, first TEXT, last TEXT, '
                       'node TEXT, PRIMARY KEY(level,ordinal))')
    connection.execute('DELETE FROM option_nodes')
    import json
    page, ordinal = [], 0

    def leaf():
        nonlocal page, ordinal
        descriptor = {**_write(directory, stem, {**identity, 'items': page}), 'count': len(page)}
        connection.execute('INSERT INTO option_nodes VALUES (?,?,?,?,?)',
                           (0, ordinal, page[0]['order'] if page else '',
                            page[-1]['order'] if page else '', json.dumps(descriptor)))
        ordinal += 1
        page = []

    for record in records:
        record = {**record, 'order': record['name'].lower()}
        if len(encoded(record)) > MAX_OPTION_BYTES:
            raise CatalogError('search facets: one option exceeds the 512-KiB operational budget')
        if page and (len(page) == PAGE_SIZE or len(encoded([*page, record])) > MAX_OPTION_BYTES):
            leaf()
        page.append(record)
    if page or not ordinal:
        leaf()
    level = 0
    while ordinal > 1:
        level += 1
        if level >= MAX_DEPTH:
            raise CatalogError('search facets: option tree exceeds the 16-level operational budget')
        rows = connection.execute(
            'SELECT first,last,node FROM option_nodes WHERE level=? ORDER BY ordinal', (level - 1,))
        page, ordinal = [], 0

        def parent():
            nonlocal page, ordinal
            descriptor = {**_write(directory, stem, {**identity, 'children': page}), 'count': sum(child['count'] for child in page)}
            connection.execute('INSERT INTO option_nodes VALUES (?,?,?,?,?)',
                               (level, ordinal, page[0]['first'], page[-1]['last'], json.dumps(descriptor)))
            ordinal += 1
            page = []

        for first, last, node in rows:
            child = {'first': first, 'last': last, **json.loads(node)}
            if page and (len(page) == PAGE_SIZE or len(encoded([*page, child])) > MAX_OPTION_BYTES):
                parent()
            page.append(child)
        if page:
            parent()
        connection.execute('DELETE FROM option_nodes WHERE level=?', (level - 1,))
    return json.loads(connection.execute('SELECT node FROM option_nodes WHERE level=?', (level,)).fetchone()[0])


def emit_options(database):
    """Totals count the occurrences in this physical corpus, never another scope."""
    database = Path(database)
    with sqlite3.connect(database) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='metadata'").fetchone():
            return None
        meta = dict(connection.execute('SELECT key,value FROM metadata'))
        scope = meta.get('scope')
        if scope not in {'collection', 'documents', 'passages', 'history'}:
            return None
        identity = {'schema': SCHEMA, 'generation': meta['generation'],
                    'scope': scope, 'revision': meta.get('revision', '')}
        connection.create_function('option_order', 1, str.lower, deterministic=True)
        connection.execute('PRAGMA temp_store=FILE')
        connection.execute('PRAGMA cache_size=-2048')
        root = {**identity}
        if scope == 'collection':
            root['keyword'] = _tree(connection, database.parent, database.stem, identity,
                ({'key': key, 'name': key, 'total': count} for key, count in connection.execute(
                    'SELECT name,count(*) FROM keywords GROUP BY name ORDER BY option_order(name),name')))
            root['subject'] = _tree(connection, database.parent, database.stem, identity,
                ({'key': key, 'name': name, 'total': count} for key, name, count in connection.execute(
                    'SELECT s.key,CASE WHEN (SELECT count(DISTINCT key) FROM subjects t WHERE t.name=s.name)>1 '
                    'THEN s.label ELSE s.name END AS display,count(*) FROM subjects s '
                    'GROUP BY s.key,display ORDER BY option_order(display),display,s.key')))
        else:
            # A file-backed temporary table stores key-root descriptors while
            # each values tree is built. Neither distinct values nor keys are
            # accumulated as an unbounded Python mapping.
            connection.execute('CREATE TEMP TABLE option_keys (key TEXT PRIMARY KEY, total INTEGER, node TEXT)')
            import json
            passage = scope in {'passages', 'history'}
            join = ' JOIN passages p ON p.document_id=f.document_id' if passage else ''
            for key, count in connection.execute(
                    'SELECT f.key,count(DISTINCT ' + ('p.id' if passage else 'f.document_id') +
                    ') FROM document_facets f' + join +
                    ' GROUP BY f.key ORDER BY option_order(f.key),f.key'):
                descriptor = _tree(connection, database.parent, database.stem, identity,
                    ({'key': value, 'name': value, 'total': total} for value, total in connection.execute(
                        'SELECT f.value,count(*) FROM document_facets f' + join +
                        ' WHERE f.key=? GROUP BY f.value ORDER BY option_order(f.value),f.value', (key,))))
                connection.execute('INSERT INTO option_keys VALUES (?,?,?)', (key, count, json.dumps(descriptor)))
            root['metadata'] = _tree(connection, database.parent, database.stem, identity,
                ({'key': key, 'name': key, 'total': count, 'values': json.loads(node)}
                 for key, count, node in connection.execute(
                     'SELECT key,total,node FROM option_keys ORDER BY option_order(key),key')))
        return _write(database.parent, database.stem, root)
