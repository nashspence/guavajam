"""Disposable aggregate search derived only from independently verified views.

The SQLite artifact is an application index, not a publication or an authority
record. Subject declarations follow GuavaJam's DataCite Subject application profile;
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

from guavajam.bibliography import ordered_authors, author_text
from guavajam.catalog import CatalogError, encoded, MAX_BYTES as MAX_CATALOG_BYTES
from guavajam.identifiers import SLUG

from .citation_routes import citation_href, passage_json, source_offsets
from .citation import citation_presentation
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
               for key in ('title', 'language', 'view', 'source_sha256', 'abstract')):
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
                committer_name TEXT NOT NULL, committer_email TEXT NOT NULL,
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
                path TEXT NOT NULL, url TEXT NOT NULL, language TEXT NOT NULL, source_sha256 TEXT NOT NULL,
                abstract TEXT NOT NULL
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
                detail=full, columnsize=1, tokenize='unicode61 remove_diacritics 2');
        ''')
        # Keep each document's searchable fields in its FTS row. Every text
        # result must carry query-positioned evidence from the exact winning
        # record, including historical metadata and deep source-body matches.
        # No second copy of those fields belongs in the relational mapping.
        if scope == 'work':
            conn.executescript('''
                CREATE VIRTUAL TABLE document_fts USING fts5(title, abstract,
                    content=documents, content_rowid=id, detail=full, columnsize=1,
                    tokenize='unicode61 remove_diacritics 2');
                CREATE TABLE passages (
                    id INTEGER PRIMARY KEY, edition_id INTEGER NOT NULL REFERENCES editions(id),
                    document_id INTEGER NOT NULL REFERENCES documents(id),
                    work_id INTEGER NOT NULL REFERENCES works(id), anchor TEXT NOT NULL,
                    title TEXT NOT NULL, kind TEXT NOT NULL, level INTEGER NOT NULL,
                    position INTEGER NOT NULL, parent TEXT, scope TEXT NOT NULL,
                    url TEXT NOT NULL, citation_url TEXT NOT NULL, citation_json TEXT NOT NULL,
                    UNIQUE(document_id, anchor)
                );
                CREATE TABLE passage_records (
                    id INTEGER PRIMARY KEY REFERENCES passages(id), work_id INTEGER NOT NULL REFERENCES works(id),
                    edition_id INTEGER NOT NULL REFERENCES editions(id), document_id INTEGER NOT NULL REFERENCES documents(id),
                    url TEXT NOT NULL
                );
                CREATE INDEX passages_by_document ON passages(document_id, position);
                CREATE INDEX passages_by_edition ON passages(edition_id);
                CREATE VIRTUAL TABLE passage_fts USING fts5(title, authors, abstract, labels, body, git,
                    detail=full, columnsize=1, tokenize='unicode61 remove_diacritics 2');
            ''')
        with conn:
            conn.execute('INSERT INTO metadata VALUES (?,?)', ('schema', SCHEMA))
            conn.execute('INSERT INTO metadata VALUES (?,?)', ('scope', scope))
            for number, entry in enumerate(entries, start=1):
                root = site if scope == 'work' else _owned(site, entry['path'])
                catalog = _json(root, 'CATALOG.json', maximum=MAX_CATALOG_BYTES)
                # File inventories can contain one citation download per source
                # anchor. Bound them by the shared indexing budget, not the
                # smaller per-document source-text limit.
                manifest = (_json(root, 'SITE.json', maximum=MAX_INDEXED_BYTES) if scope == 'collection'
                            else {'tip': catalog['tip'], 'title': 'Unpublished work'})
                if scope == 'collection':
                    total += (root / 'SITE.json').stat().st_size
                    if total > MAX_INDEXED_BYTES:
                        raise CatalogError('search: generated inventories exceed indexing bounds')
                if catalog['tip'] != entry['tip'] or manifest['tip'] != entry['tip']:
                    raise CatalogError('search: generated source verification mismatch')
                editions = catalog['editions']
                conn.execute('INSERT INTO repositories VALUES (?,?,?,?)',
                             (number, entry['repository'], entry['path'], entry['tip']))
                # Populate the work after its verified edition rows have been read.
                conn.execute('INSERT INTO works VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                             (number, number, manifest['title'], '', '', '', '', '', '', '', len(editions), ''))
                recent_facts = {}
                recent_committer = {'name': '', 'email': ''}
                for position, edition in enumerate(editions):
                    sha = edition['revision']
                    if not re.fullmatch(r'[a-f0-9]{40}', sha):
                        raise CatalogError('search: invalid edition revision')
                    read = _owned(root, 'editions/' + sha + '/read')
                    view = _json(read, 'VIEW.json', maximum=MAX_INDEXED_BYTES)
                    if view['preview'] is not False or view['canonical']['revision'] != sha:
                        raise CatalogError('search: non-public or mismatched verified view')
                    navigation, size = _navigation(read, view)
                    passage_starts = source_offsets(navigation)
                    total += size + (read / 'VIEW.json').stat().st_size
                    if total > MAX_INDEXED_BYTES:
                        raise CatalogError('search: generated inventories exceed indexing bounds')
                    facts = view['facts']
                    recent_facts = facts
                    committer_file = _owned(root, 'editions/' + sha + '/COMMITTER.json')
                    if committer_file.is_file():
                        recent_committer = _json(root, 'editions/' + sha + '/COMMITTER.json', maximum=4096)
                        if (not isinstance(recent_committer, dict)
                                or set(recent_committer) != {'name', 'email'}
                                or not all(isinstance(value, str) and len(value) <= 512
                                           for value in recent_committer.values())):
                            raise CatalogError('search: invalid generated committer display metadata')
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
                                              '(edition_id,work_id,title,path,url,language,source_sha256,abstract) '
                                              'VALUES (?,?,?,?,?,?,?,?)',
                                              (edition_id, number, detail['title'], source_path, url,
                                               detail['language'], detail['source_sha256'], detail.get('abstract', '')))
                        document_id = cursor.lastrowid
                        if scope == 'work' and source_path != 'content/manuscript.md':
                            conn.execute('INSERT INTO document_fts(rowid,title,abstract) VALUES (?,?,?)',
                                         (document_id, detail['title'], detail.get('abstract', '')))
                        title = facts['title']
                        if detail['title'] != title:
                            title += ' — ' + detail['title']
                        # Preserve bibliographic facts structurally in facts_json;
                        # make identifiers and signed provenance searchable too.
                        provenance = ' '.join([entry['repository'], sha, edition['tree'],
                                               edition['published'], edition['message'], source_path,
                                               'v' + str(position + 1),
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
                            cited = quote(prefix + 'editions/' + sha + '/read/' + citation_href(
                                source_path, passage_starts[source_path] + anchor['position'],
                                anchor['anchor']), safe='/#:')
                            identity = {'revision': sha, 'manuscript': 'content/manuscript.md',
                                        'document': source_path, 'anchor': anchor['anchor']}
                            presentation = citation_presentation(identity,
                                quote(prefix + 'editions/' + sha + '/read/' + passage_json(view_path, anchor['anchor']), safe='/'),
                                title=facts['title'], authors=author_text(ordered_authors(facts)), published=edition['published'],
                                edition_number=position + 1,
                                document_title=detail['title'] if source_path != 'content/manuscript.md' else '')
                            cursor = conn.execute('INSERT INTO passages '
                                '(edition_id,document_id,work_id,anchor,title,kind,level,position,parent,scope,url,citation_url,citation_json) '
                                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                (edition_id, document_id, number, anchor['anchor'], anchor['title'],
                                 anchor['kind'], anchor['level'], anchor['position'], anchor['parent'],
                                 anchor['scope'], target, cited, json.dumps(presentation, ensure_ascii=False)))
                            passage_id = cursor.lastrowid
                            conn.execute('INSERT INTO passage_records VALUES (?,?,?,?,?)',
                                         (passage_id, number, edition_id, document_id, target))
                            conn.execute('INSERT INTO passage_fts(rowid,title,authors,abstract,labels,body,git) '
                                'VALUES (?,?,?,?,?,?,?)',
                                (passage_id, title + ' — ' + anchor['title'], authors, facts.get('abstract', ''),
                                 labels, anchor['text'], provenance + ' ' + anchor['anchor']))
                if editions:
                    latest = editions[-1]
                    conn.execute('UPDATE works SET title=?,authors=?,abstract=?,committer_name=?,committer_email=?, '
                                 'language=?,latest_published=?,latest_revision=?,labels=? WHERE id=?',
                                 (recent_facts['title'], _authors(recent_facts), recent_facts.get('abstract', ''),
                                  recent_committer['name'], recent_committer['email'], recent_facts['language'],
                                  latest['published'], latest['revision'], _labels(recent_facts), number))
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


def _collection_subject_names(conn):
    """Display the bare subject name unless multiple distinct latest subjects share it."""
    rows = conn.execute('''
        SELECT DISTINCT es.key, es.subject
        FROM edition_subjects es JOIN editions e ON e.id=es.edition_id
        JOIN works w ON w.id=e.work_id AND w.latest_revision=e.revision
    ''').fetchall()
    variants = {}
    for key, name in rows:
        variants.setdefault(name, set()).add(key)
    return {key: (name if len(variants[name]) == 1 else None) for key, name in rows}


def _facets(keywords, subjects):
    """Expose verified metadata; the existing SQLite options enable filtering."""
    if not keywords and not subjects:
        return ''

    def facet(kind, key, label):
        name = 'Tag' if kind == 'keyword' else 'Subject'
        return ('<span class="guavajam-facet guavajam-' + ('tag' if kind == 'keyword' else 'subject') +
                '" data-facet="' + kind + '" data-value="' + escape(key, quote=True) +
                '"><span class="guavajam-facet-type">' + name + '</span> ' +
                escape(label) + '</span>')

    chips = ([facet('keyword', item, item) for item in keywords] +
             [facet('subject', key, label) for key, label in subjects])
    return ('<p class="guavajam-facets guavajam-scrollline" tabindex="0" aria-label="Subjects and tags">' +
            ''.join(chips) + '</p>')


def homepage(site, *, interactive, site_origin=None):
    """Emit a bounded static works directory, enhanced by the existing SQLite UI.

    Each index page contains at most PAGE_SIZE works. The other pages are real
    HTML documents linked with ordinary hrefs, not JS-dependent slices of a
    large hidden list. Querying/searching/sorting in the browser still uses
    the same collection SQLite worker and only loads on interaction.
    """
    from .directory_pages import PAGE_SIZE, _static_pager, search_summary, search_submit
    from .site import detail_link, page
    from .icons import icon

    site = Path(site)
    first_html = None
    with closing(sqlite3.connect(site / 'index.sqlite')) as conn:
        count = conn.execute('SELECT count(*) FROM works').fetchone()[0]
        pages = max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE)
        cursor = conn.execute('''
            SELECT w.id,w.title,w.authors,w.abstract,w.latest_published,w.latest_revision,
              w.committer_name,w.committer_email,r.path,r.url
            FROM works w JOIN repositories r ON w.repository_id=r.id
            ORDER BY w.latest_published DESC,w.title COLLATE NOCASE,w.id
        ''')
        download = emit_download(site / 'index.sqlite') if interactive else None
        subject_names = _collection_subject_names(conn)
        for number in range(1, pages + 1):
            base = '' if number == 1 else '../'
            records = cursor.fetchmany(PAGE_SIZE)
            ids = [row[0] for row in records]
            tags, subjects = {key: [] for key in ids}, {key: [] for key in ids}
            if ids:
                placeholders = ','.join('?' for _ in ids)
                for work_id, keyword in conn.execute(
                        'SELECT work_id, name FROM keywords WHERE work_id IN (' + placeholders +
                        ') ORDER BY name COLLATE NOCASE, name', ids):
                    tags[work_id].append(keyword)
                for work_id, key, label in conn.execute(
                        'SELECT work_id, key, label FROM subjects WHERE work_id IN (' + placeholders +
                        ') ORDER BY label COLLATE NOCASE, key', ids):
                    subjects[work_id].append((key, subject_names.get(key) or label))
            entries = []
            for work_id, title, authors, abstract, day, revision, _committer_name, _committer_email, path, source in records:
                work = base + path + '/'
                target = (work + 'editions/' + revision + '/read/index.html') if revision else work + 'index.html'
                byline = escape(authors) if authors else ''
                if day:
                    byline += (' · ' if byline else '') + '<time datetime="' + escape(day, quote=True) + '">' + escape(day) + '</time>'
                entries.append('<li><article><h3><a href="' + escape(target, quote=True) + '">' +
                               escape(title) + '</a>' + detail_link(work + 'index.html', title + ' work page') +
                               '</h3>' +
                               ('<p class="guavajam-byline guavajam-scrollline" tabindex="0" aria-label="Work authors and date">' + byline + '</p>' if byline else '') +
                               _facets(tags[work_id], subjects[work_id]) +
                               ('<p class="guavajam-abstract">' + escape(abstract) + '</p>' if abstract else '') +
                               '</article></li>')
            body = ''
            if interactive:
                body += (
                    '<details class="guavajam-index-search" id="guavajam-index-search" hidden>' +
                    search_summary(download) +
                    '<form id="guavajam-search" role="search" '
                    'data-scope="collection" data-root="' + escape(base, quote=True) + '" '
                    'data-worker="' + escape(base + 'search-worker.js', quote=True) + '" ' +
                    download_attributes(download, prefix=base) + '>'
                    '<div class="guavajam-query-field"><label class="guavajam-visually-hidden" for="guavajam-query">Search works</label>'
                    '<input id="guavajam-query" name="q" type="search" maxlength="256" autocomplete="off"></div>'
                    + search_submit() +
                    '<details class="guavajam-search-filters"><summary>' + icon('sliders-horizontal') + 'Filters and sort</summary>'
                    '<div class="guavajam-filter-fields">'
                    '<div><label for="guavajam-keyword">Keyword</label>'
                    '<select id="guavajam-keyword" name="keyword"><option value="">All keywords</option></select></div>'
                    '<div><label for="guavajam-subject">Subject</label>'
                    '<select id="guavajam-subject" name="subject"><option value="">All subjects</option></select></div>'
                    '<div><label for="guavajam-sort">Sort</label><select id="guavajam-sort" name="sort">'
                    '<option value="recent">Most recent</option><option value="relevance">Relevance</option>'
                    '<option value="title">Title</option><option value="oldest">Oldest</option>'
                    '<option value="editions">Most editions</option></select></div></div></details>'
                    '</form></details>')
            body += '<section class="guavajam-listing" aria-labelledby="guavajam-works-heading">'
            body += '<h2 class="guavajam-list-heading" id="guavajam-works-heading" data-static-heading="Works">Works</h2>'
            if interactive:
                body += (
                    '<p class="guavajam-meta" id="guavajam-search-status" role="status" aria-live="polite">'
                    'Showing static page ' + str(number) + ' of ' + str(pages) + '.</p>'
                    '<ol id="guavajam-search-results" class="guavajam-works guavajam-record-list" hidden></ol>')
            body += ('<div id="guavajam-static-section"><ol id="guavajam-static-list" '
                     'class="guavajam-works guavajam-record-list">' + ''.join(entries) + '</ol>')
            body += _static_pager(number, pages,
                                  first_href='index.html' if number == 1 else '../index.html',
                                  page_prefix='page/' if number == 1 else '')
            body += '</div>'
            if interactive:
                body += (
                    '<nav class="guavajam-pagination" aria-label="Search result pages" hidden>'
                    '<button type="button" id="guavajam-search-prev" disabled>Previous</button>'
                    '<span id="guavajam-search-page"></span>'
                    '<button type="button" id="guavajam-search-next" disabled>Next</button>'
                    '</nav>')
            body += '</section>'
            if interactive:
                body += '<script src="' + escape(base + 'search.js', quote=True) + '" defer></script>'
            # A collection spans Git repositories and cannot have one canonical
            # publication-commit citation.
            html = page('GuavaJam' if number == 1 else f'GuavaJam — page {number}',
                        body, search=interactive, breadcrumb_current='GuavaJam', site_origin=site_origin)
            if number == 1:
                first_html = html
            else:
                target = site / 'page' / f'{number}.html'
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(html, encoding='utf-8')
    return first_html
