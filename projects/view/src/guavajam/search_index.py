"""Disposable aggregate search derived only from independently verified views.

The SQLite artifact is an application index, not a publication or an authority
record. Subject declarations follow Core's DataCite Subject application profile;
exact declarations identify facets without inferring synonyms or hierarchies.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
from html import escape
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from urllib.parse import quote

from guavajam_core.bibliography import ordered_authors
from guavajam_core.catalog import CatalogError, encoded, MAX_BYTES as MAX_CATALOG_BYTES
from guavajam_core.identifiers import SLUG

from .search_artifact import download_attributes, emit_download

MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_INDEXED_BYTES = 256 * 1024 * 1024
MAX_DATABASE_BYTES = 256 * 1024 * 1024
SCHEMA = 'guavajam.search.v1'


def _owned(root, relative):
    """Reject escapes and symlinks even though all inputs are generator-owned."""
    if not isinstance(relative, str) or not relative or '\\' in relative:
        raise CatalogError('search: invalid generated path')
    part = Path(relative)
    path = root / part
    if (part.is_absolute() or any(p in {'.', '..'} for p in relative.split('/'))
            or path.resolve() != path.absolute() or not path.is_relative_to(root)):
        raise CatalogError('search: generated path must stay inside its owned directory')
    return path


def _json(root, relative, *, maximum=MAX_DOCUMENT_BYTES):
    path = _owned(root, relative)
    if not path.is_file() or path.stat().st_size > maximum:
        raise CatalogError('search: missing or oversized generated metadata')
    return json.loads(path.read_text('utf-8'))


def _navigation(read, view):
    """Consume the same bounded source-derived inventory as static navigation.

    A render's inventory hashes the derivative; it does not replace signature
    verification of the source, which has already happened before site building.
    """
    path = _owned(read, 'NAV.json')
    if not path.is_file() or path.stat().st_size > MAX_INDEXED_BYTES:
        raise CatalogError('search: missing or oversized navigation inventory')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != view.get('files', {}).get('NAV.json'):
        raise CatalogError('search: navigation inventory hash mismatch')
    navigation = json.loads(raw)
    if (navigation.get('schema') != 'guavajam.navigation.v1'
            or navigation.get('canonical') != view['canonical']
            or navigation.get('preview') is not False
            or set(navigation.get('documents', {})) != set(view['documents'])):
        raise CatalogError('search: navigation source identity mismatch')
    for source, document in navigation['documents'].items():
        details = view['documents'][source]
        if any(document.get(key) != details.get(key)
               for key in ('title', 'language', 'view', 'source_sha256')):
            raise CatalogError('search: navigation document identity mismatch')
        if (not isinstance(document.get('text'), str)
                or len(document['text'].encode('utf-8')) > MAX_DOCUMENT_BYTES):
            raise CatalogError('search: document text exceeds indexing bounds')
        anchors = document.get('anchors')
        if not isinstance(anchors, list):
            raise CatalogError('search: missing ordered navigation anchors')
        seen = {}
        for position, anchor in enumerate(anchors):
            identifier = anchor.get('anchor')
            if (not isinstance(identifier, str)
                    or not SLUG.fullmatch(identifier)
                    or identifier in seen or anchor.get('position') != position
                    or anchor.get('kind') not in {'section', 'passage'}
                    or not isinstance(anchor.get('title'), str)
                    or not isinstance(anchor.get('text'), str)
                    or len(anchor['text'].encode('utf-8')) > MAX_DOCUMENT_BYTES
                    or type(anchor.get('level')) is not int
                    or anchor['level'] not in range(7)
                    or not isinstance(anchor.get('scope'), str)
                    or not (anchor['scope'] == 'document'
                            or (anchor['scope'].startswith('quotation:')
                                and SLUG.fullmatch(anchor['scope'].split(':', 1)[1])))):
                raise CatalogError('search: invalid ordered navigation anchor')
            parent = anchor.get('parent')
            if parent is not None and (parent not in seen or seen[parent]['scope'] != anchor['scope']):
                raise CatalogError('search: navigation parent crosses source outline')
            seen[identifier] = anchor
        if sorted(seen) != sorted(details.get('anchors', [])):
            raise CatalogError('search: navigation anchors differ from verified view')
    return navigation, len(raw)


def _labels(facts):
    return ' '.join([*facts.get('keywords', []),
                     *(value for subject in facts.get('subjects', []) for value in subject.values())])


def subject_key(subject):
    """No label-only joins, authority resolution or implicit equivalence."""
    return hashlib.sha256(encoded(subject)).hexdigest()


def subject_label(subject):
    context = [subject.get(key) for key in ('subject_scheme', 'classification_code', 'language')]
    context.extend(dict.fromkeys(subject.get(key) for key in ('scheme_uri', 'value_uri')))
    return ' · '.join([subject['subject'], *(part for part in context if part)])


def _authors(facts):
    return ', '.join(item['name'] for item in ordered_authors(facts))


def create_index(site, entries, *, filename='index.sqlite', scope='collection'):
    """Write index.sqlite beside index.html; entries remain repository/path/tip.

    Latest means last in verified first-parent publication order, never greatest
    claimed date. Dates remain signed publisher claims. Every edition's original
    metadata and Git facts are retained, while work facets describe its latest
    edition. Each search row combines one document's text and its edition facts.
    """
    site = Path(site).absolute()
    if scope not in {'collection', 'work'}:
        raise CatalogError('search: invalid index scope')
    entries = list(entries)
    if scope == 'work' and (len(entries) != 1 or entries[0]['path'] != ''):
        raise CatalogError('search: a work index requires exactly its own repository')
    if filename not in {'index.sqlite', 'work.sqlite'}:
        raise CatalogError('search: invalid database filename')
    database = _owned(site, filename)
    if database.exists() or database.is_symlink():
        raise CatalogError('search: destination already exists')
    total = 0
    conn = sqlite3.connect(database)
    try:
        conn.executescript('''
            PRAGMA page_size=4096;
            PRAGMA journal_mode=DELETE;
            PRAGMA foreign_keys=ON;
            PRAGMA user_version=1;
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE repositories (
                id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE,
                path TEXT NOT NULL UNIQUE, tip TEXT NOT NULL
            );
            CREATE TABLE works (
                id INTEGER PRIMARY KEY, repository_id INTEGER NOT NULL UNIQUE REFERENCES repositories(id),
                title TEXT NOT NULL, authors TEXT NOT NULL, abstract TEXT NOT NULL,
                language TEXT NOT NULL, latest_published TEXT NOT NULL,
                latest_revision TEXT NOT NULL, edition_count INTEGER NOT NULL, labels TEXT NOT NULL
            );
            CREATE TABLE editions (
                id INTEGER PRIMARY KEY, work_id INTEGER NOT NULL REFERENCES works(id),
                position INTEGER NOT NULL, revision TEXT NOT NULL, tree TEXT NOT NULL,
                published TEXT NOT NULL, title TEXT NOT NULL, message TEXT NOT NULL,
                facts_json TEXT NOT NULL, UNIQUE(work_id, revision)
            );
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY, edition_id INTEGER NOT NULL REFERENCES editions(id),
                work_id INTEGER NOT NULL REFERENCES works(id), title TEXT NOT NULL,
                path TEXT NOT NULL, url TEXT NOT NULL, language TEXT NOT NULL, source_sha256 TEXT NOT NULL
            );
            CREATE TABLE keywords (work_id INTEGER NOT NULL REFERENCES works(id), name TEXT NOT NULL,
                PRIMARY KEY(work_id, name));
            CREATE TABLE subjects (work_id INTEGER NOT NULL REFERENCES works(id), key TEXT NOT NULL,
                label TEXT NOT NULL, PRIMARY KEY(work_id, key));
            CREATE TABLE edition_keywords (edition_id INTEGER NOT NULL REFERENCES editions(id), name TEXT NOT NULL,
                PRIMARY KEY(edition_id, name));
            CREATE TABLE edition_subjects (
                edition_id INTEGER NOT NULL REFERENCES editions(id), key TEXT NOT NULL,
                subject TEXT NOT NULL, subject_scheme TEXT, scheme_uri TEXT, value_uri TEXT,
                classification_code TEXT, language TEXT, PRIMARY KEY(edition_id, key)
            );
            CREATE TABLE search_records (
                id INTEGER PRIMARY KEY, work_id INTEGER NOT NULL REFERENCES works(id),
                edition_id INTEGER REFERENCES editions(id), document_id INTEGER REFERENCES documents(id),
                url TEXT NOT NULL
            );
            CREATE INDEX editions_by_work ON editions(work_id, position);
            CREATE INDEX documents_by_work ON documents(work_id);
            CREATE INDEX works_by_date ON works(latest_published DESC);
            CREATE INDEX works_by_title ON works(title COLLATE NOCASE);
            CREATE INDEX keywords_by_name ON keywords(name, work_id);
            CREATE INDEX subjects_by_key ON subjects(key, work_id);
            CREATE INDEX search_by_work ON search_records(work_id);
            CREATE INDEX documents_by_edition ON documents(edition_id, path);
            CREATE VIRTUAL TABLE search_fts USING fts5(title, authors, abstract, labels, body, git,
                content='', detail=full, columnsize=1, tokenize='unicode61 remove_diacritics 2');
        ''')
        # Collection search routes to documents. Only a work's directory uses
        # anchor rows. Both keep FTS positions and token counts for unchanged
        # weighted BM25 ranking, without another copy of the source plaintext.
        if scope == 'work':
            conn.executescript('''
                CREATE TABLE passages (
                    id INTEGER PRIMARY KEY, edition_id INTEGER NOT NULL REFERENCES editions(id),
                    document_id INTEGER NOT NULL REFERENCES documents(id),
                    work_id INTEGER NOT NULL REFERENCES works(id), anchor TEXT NOT NULL,
                    title TEXT NOT NULL, kind TEXT NOT NULL, level INTEGER NOT NULL,
                    position INTEGER NOT NULL, parent TEXT, scope TEXT NOT NULL,
                    url TEXT NOT NULL, UNIQUE(document_id, anchor)
                );
                CREATE TABLE passage_records (
                    id INTEGER PRIMARY KEY REFERENCES passages(id), work_id INTEGER NOT NULL REFERENCES works(id),
                    edition_id INTEGER NOT NULL REFERENCES editions(id), document_id INTEGER NOT NULL REFERENCES documents(id),
                    url TEXT NOT NULL
                );
                CREATE INDEX passages_by_document ON passages(document_id, position);
                CREATE INDEX passages_by_edition ON passages(edition_id);
                CREATE VIRTUAL TABLE passage_fts USING fts5(title, authors, abstract, labels, body, git,
                    content='', detail=full, columnsize=1, tokenize='unicode61 remove_diacritics 2');
            ''')
        with conn:
            conn.execute('INSERT INTO metadata VALUES (?,?)', ('schema', SCHEMA))
            conn.execute('INSERT INTO metadata VALUES (?,?)', ('scope', scope))
            for number, entry in enumerate(entries, start=1):
                root = site if scope == 'work' else _owned(site, entry['path'])
                catalog = _json(root, 'CATALOG.json', maximum=MAX_CATALOG_BYTES)
                manifest = (_json(root, 'SITE.json') if scope == 'collection'
                            else {'tip': catalog['tip'], 'title': 'Unpublished work'})
                if catalog['tip'] != entry['tip'] or manifest['tip'] != entry['tip']:
                    raise CatalogError('search: generated source verification mismatch')
                editions = catalog['editions']
                conn.execute('INSERT INTO repositories VALUES (?,?,?,?)',
                             (number, entry['repository'], entry['path'], entry['tip']))
                # Populate the work after its verified edition rows have been read.
                conn.execute('INSERT INTO works VALUES (?,?,?,?,?,?,?,?,?,?)',
                             (number, number, manifest['title'], '', '', '', '', '', len(editions), ''))
                recent_facts = {}
                for position, edition in enumerate(editions):
                    sha = edition['revision']
                    if not re.fullmatch(r'[a-f0-9]{40}', sha):
                        raise CatalogError('search: invalid edition revision')
                    read = _owned(root, 'editions/' + sha + '/read')
                    view = _json(read, 'VIEW.json')
                    if view['preview'] is not False or view['canonical']['revision'] != sha:
                        raise CatalogError('search: non-public or mismatched verified view')
                    navigation, size = _navigation(read, view)
                    total += size
                    if total > MAX_INDEXED_BYTES:
                        raise CatalogError('search: navigation inventories exceed indexing bounds')
                    facts = view['facts']
                    recent_facts = facts
                    authors, labels = _authors(facts), _labels(facts)
                    cursor = conn.execute('INSERT INTO editions '
                                          '(work_id,position,revision,tree,published,title,message,facts_json) '
                                          'VALUES (?,?,?,?,?,?,?,?)',
                                          (number, position, sha, edition['tree'], edition['published'],
                                           facts['title'], edition['message'], encoded(facts).decode('utf-8')))
                    edition_id = cursor.lastrowid
                    for keyword in facts.get('keywords', []):
                        conn.execute('INSERT OR IGNORE INTO edition_keywords VALUES (?,?)', (edition_id, keyword))
                    for subject in facts.get('subjects', []):
                        conn.execute('INSERT OR IGNORE INTO edition_subjects VALUES (?,?,?,?,?,?,?,?)',
                                     (edition_id, subject_key(subject), subject['subject'],
                                      *(subject.get(key) for key in ('subject_scheme', 'scheme_uri', 'value_uri',
                                                                    'classification_code', 'language'))))
                    for source_path, detail in sorted(view['documents'].items()):
                        view_path = detail['view']
                        if not view_path.endswith('.html'):
                            raise CatalogError('search: invalid verified document view')
                        html = _owned(read, view_path)
                        if not html.is_file() or html.stat().st_size > MAX_DOCUMENT_BYTES:
                            raise CatalogError('search: verified reading text exceeds indexing bounds')
                        document = navigation['documents'][source_path]
                        prefix = entry['path'] + '/' if entry['path'] else ''
                        url = quote(prefix + 'editions/' + sha + '/read/' + view_path, safe='/')
                        cursor = conn.execute('INSERT INTO documents '
                                              '(edition_id,work_id,title,path,url,language,source_sha256) '
                                              'VALUES (?,?,?,?,?,?,?)',
                                              (edition_id, number, detail['title'], source_path, url,
                                               detail['language'], detail['source_sha256']))
                        document_id = cursor.lastrowid
                        title = facts['title']
                        if detail['title'] != title:
                            title += ' — ' + detail['title']
                        # Preserve bibliographic facts structurally in facts_json;
                        # make identifiers and signed provenance searchable too.
                        provenance = ' '.join([entry['repository'], sha, edition['tree'],
                                               edition['published'], edition['message'], source_path,
                                               facts.get('rights', ''), facts.get('schema_type', ''),
                                               json.dumps(facts.get('identifiers', {}), ensure_ascii=False),
                                               json.dumps(ordered_authors(facts), ensure_ascii=False)])
                        cursor = conn.execute('INSERT INTO search_records '
                                              '(work_id,edition_id,document_id,url) VALUES (?,?,?,?)',
                                              (number, edition_id, document_id, url))
                        conn.execute('INSERT INTO search_fts(rowid,title,authors,abstract,labels,body,git) '
                                     'VALUES (?,?,?,?,?,?,?)',
                                     (cursor.lastrowid, title, authors, facts.get('abstract', ''),
                                      labels, document['text'], provenance))
                        if scope != 'work':
                            continue
                        for anchor in document['anchors']:
                            target = url + '#' + quote(anchor['anchor'], safe='')
                            cursor = conn.execute('INSERT INTO passages '
                                '(edition_id,document_id,work_id,anchor,title,kind,level,position,parent,scope,url) '
                                'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                                (edition_id, document_id, number, anchor['anchor'], anchor['title'],
                                 anchor['kind'], anchor['level'], anchor['position'], anchor['parent'],
                                 anchor['scope'], target))
                            passage_id = cursor.lastrowid
                            conn.execute('INSERT INTO passage_records VALUES (?,?,?,?,?)',
                                         (passage_id, number, edition_id, document_id, target))
                            conn.execute('INSERT INTO passage_fts(rowid,title,authors,abstract,labels,body,git) '
                                'VALUES (?,?,?,?,?,?,?)',
                                (passage_id, title + ' — ' + anchor['title'], authors, facts.get('abstract', ''),
                                 labels, anchor['text'], provenance + ' ' + anchor['anchor']))
                if editions:
                    latest = editions[-1]
                    conn.execute('UPDATE works SET title=?,authors=?,abstract=?,language=?,latest_published=?, '
                                 'latest_revision=?,labels=? WHERE id=?',
                                 (recent_facts['title'], _authors(recent_facts), recent_facts.get('abstract', ''),
                                  recent_facts['language'], latest['published'], latest['revision'],
                                  _labels(recent_facts), number))
                    for keyword in recent_facts.get('keywords', []):
                        conn.execute('INSERT OR IGNORE INTO keywords VALUES (?,?)', (number, keyword))
                    for subject in recent_facts.get('subjects', []):
                        conn.execute('INSERT OR IGNORE INTO subjects VALUES (?,?,?)',
                                     (number, subject_key(subject), subject_label(subject)))
                else:
                    cursor = conn.execute('INSERT INTO search_records (work_id,url) VALUES (?,?)',
                                          (number, (entry['path'] + '/' if entry['path'] else '') + 'index.html'))
                    conn.execute('INSERT INTO search_fts(rowid,title,authors,abstract,labels,body,git) '
                                 'VALUES (?,?,?,?,?,?,?)',
                                 (cursor.lastrowid, manifest['title'], '', '', '', '',
                                  entry['repository'] + ' ' + entry['tip']))
            conn.execute("INSERT INTO search_fts(search_fts) VALUES ('optimize')")
            if scope == 'work':
                conn.execute("INSERT INTO passage_fts(passage_fts) VALUES ('optimize')")
        conn.execute('VACUUM')
        if (conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok'
                or conn.execute('PRAGMA foreign_key_check').fetchall()):
            raise CatalogError('search: database integrity check failed')
        if database.stat().st_size > MAX_DATABASE_BYTES:
            raise CatalogError('search: database exceeds the 256 MiB whole-download limit')
        conn.close()
        emit_download(database)
    except Exception:
        conn.close()
        database.unlink(missing_ok=True)
        raise
    else:
        return database


def _runtime_files():
    assets = Path(__file__).parent / 'assets'
    specified = os.environ.get('GUAVAJAM_SQLITE_WASM_DIR')
    source = Path(specified) if specified else assets
    module_name = 'index.mjs' if specified else 'sqlite3.mjs'
    files = {'sqlite3.mjs': source / module_name, 'sqlite3.wasm': source / 'sqlite3.wasm',
             **{name: assets / name for name in
                ('search.js', 'search-worker.js', 'search-download.js', 'sqlite3-LICENSE.txt')}}
    if not all(path.is_file() for path in files.values()):
        if specified or os.environ.get('GUAVAJAM_REQUIRE_SEARCH') == '1':
            raise CatalogError('search: missing pinned SQLite WASM runtime assets')
        return {}
    return files


def runtime_identity():
    """Invalidate aggregate generations when an external runtime is added/changed."""
    return {'sqlite': sqlite3.sqlite_version,
            'assets': {name: hashlib.sha256(path.read_bytes()).hexdigest()
                       for name, path in _runtime_files().items()}}


def emit_browser_assets(site):
    """Self-host the pinned runtime; a plain Python install keeps the static list.

    Docker bundles the runtime. Pages and source builds may supply an npm dist/
    using GUAVAJAM_SQLITE_WASM_DIR. GUAVAJAM_REQUIRE_SEARCH=1 makes absence fatal.
    """
    files = _runtime_files()
    for name, path in files.items():
        shutil.copyfile(path, Path(site) / name)
    return bool(files)


def homepage(site, *, interactive):
    """Every work stays reachable before JavaScript and after search failure."""
    from .site import page
    with closing(sqlite3.connect(Path(site) / 'index.sqlite')) as conn:
        rows = conn.execute('''
            SELECT w.id, w.title, w.authors, w.abstract, w.latest_published, w.edition_count, r.path, r.url
            FROM works w JOIN repositories r ON r.id=w.repository_id
            ORDER BY w.latest_published DESC, w.title COLLATE NOCASE, w.id
        ''').fetchall()
        entries = []
        for identifier, title, authors, abstract, date, count, path, source in rows:
            keywords = [row[0] for row in conn.execute('SELECT name FROM keywords WHERE work_id=? ORDER BY name', (identifier,))]
            subjects = [row[0] for row in conn.execute('SELECT label FROM subjects WHERE work_id=? ORDER BY label', (identifier,))]
            entries.append('<li><article><h3><a href="' + escape(path + '/index.html', quote=True) + '">' +
                           escape(title) + '</a></h3>' +
                           ('<p>' + escape(authors) + '</p>' if authors else '') +
                           ('<p>' + escape(abstract) + '</p>' if abstract else '') +
                           ('<p>Keywords: ' + escape(', '.join(keywords)) + '</p>' if keywords else '') +
                           ('<p>Subjects: ' + escape('; '.join(subjects)) + '</p>' if subjects else '') +
                           '<p>' + escape(date or 'Unpublished') + ' · ' + str(count) +
                           ' edition(s) · <a href="' + escape(source, quote=True) +
                           '">Source repository</a></p></article></li>')
    body = '<p>Discover independently verified works, editions and their source documents.</p>'
    if interactive:
        download = emit_download(Path(site) / 'index.sqlite')
        body += ('<section class="guavajam-index-search" aria-labelledby="guavajam-search-heading">'
                 '<h2 id="guavajam-search-heading">Search publications</h2>'
                 '<p id="guavajam-search-scope">Search covers all published editions. Keyword and subject '
                 'filters describe each work’s latest edition. Dates are publisher-supplied Git dates.</p>'
                 '<p>Enable search to download this collection’s local index (' +
                 str((download['transfer_size'] + 1023) // 1024) +
                 ' KiB compressed) and search engine. All works remain listed below.</p>'
                 '<button type="button" id="guavajam-search-enable">Enable search</button>'
                 '<form id="guavajam-search" role="search" aria-describedby="guavajam-search-scope" ' +
                 download_attributes(download) + '>'
                 '<div><label for="guavajam-query">Text search</label>'
                 '<input id="guavajam-query" name="q" type="search" maxlength="256" autocomplete="off"></div>'
                 '<div><label for="guavajam-keyword">Keyword</label>'
                 '<select id="guavajam-keyword" name="keyword"><option value="">All keywords</option></select></div>'
                 '<div><label for="guavajam-subject">Subject</label>'
                 '<select id="guavajam-subject" name="subject"><option value="">All subjects</option></select></div>'
                 '<div><label for="guavajam-sort">Sort</label><select id="guavajam-sort" name="sort">'
                 '<option value="recent">Most recent</option><option value="relevance">Relevance</option>'
                 '<option value="title">Title</option><option value="oldest">Oldest</option>'
                 '<option value="editions">Most editions</option></select></div>'
                 '<button type="submit">Search</button></form>'
                 '<p id="guavajam-search-status" role="status" aria-live="polite">Search has not been enabled.</p>'
                 '<ol id="guavajam-search-results" class="guavajam-works" hidden></ol>'
                 '<nav aria-label="Search result pages">'
                 '<button type="button" id="guavajam-search-prev" disabled>Previous</button>'
                 '<span id="guavajam-search-page"></span>'
                 '<button type="button" id="guavajam-search-next" disabled>Next</button>'
                 '</nav></section>')
    body += ('<section id="guavajam-static-section"><h2>All works</h2>'
             '<ol id="guavajam-static-list" class="guavajam-works">' + ''.join(entries) + '</ol></section>')
    if interactive:
        body += '<script src="search.js" defer></script>'
    return page('Publications', body, search=interactive)
