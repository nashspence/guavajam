"""Disposable aggregate search derived only from independently verified views.

The SQLite artifact is an application index, not a publication or an authority
record. Subject declarations follow Core's DataCite Subject application profile;
exact declarations identify facets without inferring synonyms or hierarchies.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
from html import escape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from urllib.parse import quote

from guavajam_core.bibliography import ordered_authors
from guavajam_core.catalog import CatalogError, encoded

MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_INDEXED_BYTES = 256 * 1024 * 1024
MAX_DATABASE_BYTES = 256 * 1024 * 1024
SCHEMA = 'guavajam.search.v1'


class ReadingText(HTMLParser):
    """Keep reading text and word boundaries, excluding generated page chrome."""

    VOID = frozenset({'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                      'link', 'meta', 'param', 'source', 'track', 'wbr'})
    BLOCK = frozenset({'article', 'p', 'div', 'section', 'aside', 'header', 'footer',
                       'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'ul', 'ol',
                       'table', 'tr', 'td', 'th', 'pre', 'blockquote', 'br', 'hr',
                       'figure', 'figcaption', 'dt', 'dd'})
    IGNORED = frozenset({'style', 'script', 'svg', 'noscript'})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        active = bool(self.stack) or tag == 'article'
        if not active:
            return
        skipped = ((self.stack[-1][1] if self.stack else False) or tag in self.IGNORED
                   or 'hidden' in attrs or attrs.get('aria-hidden') == 'true'
                   or 'guavajam-source-identity' in attrs.get('class', '').split())
        if tag in self.BLOCK and not skipped:
            self.chunks.append(' ')
        if tag not in self.VOID:
            self.stack.append((tag, skipped))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID or not self.stack:
            return
        if tag in self.BLOCK and not self.stack[-1][1]:
            self.chunks.append(' ')
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if self.stack and not self.stack[-1][1]:
            self.chunks.append(data)

    def text(self):
        return ' '.join(''.join(self.chunks).split())


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


def _json(root, relative):
    path = _owned(root, relative)
    if not path.is_file() or path.stat().st_size > MAX_DOCUMENT_BYTES:
        raise CatalogError('search: missing or oversized generated metadata')
    return json.loads(path.read_text('utf-8'))


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


def create_index(site, entries):
    """Write index.sqlite beside index.html; entries remain repository/path/tip.

    Latest means last in verified first-parent publication order, never greatest
    claimed date. Dates remain signed publisher claims. Every edition's original
    metadata and Git facts are retained, while work facets describe its latest
    edition. Each search row combines one document's text and its edition facts.
    """
    site = Path(site).absolute()
    database = site / 'index.sqlite'
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
                url TEXT NOT NULL, title TEXT NOT NULL, authors TEXT NOT NULL, abstract TEXT NOT NULL,
                labels TEXT NOT NULL, body TEXT NOT NULL, git TEXT NOT NULL
            );
            CREATE INDEX editions_by_work ON editions(work_id, position);
            CREATE INDEX documents_by_work ON documents(work_id);
            CREATE INDEX works_by_date ON works(latest_published DESC);
            CREATE INDEX works_by_title ON works(title COLLATE NOCASE);
            CREATE INDEX keywords_by_name ON keywords(name, work_id);
            CREATE INDEX subjects_by_key ON subjects(key, work_id);
            CREATE INDEX search_by_work ON search_records(work_id);
            CREATE VIRTUAL TABLE search_fts USING fts5(title, authors, abstract, labels, body, git,
                content='search_records', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
        ''')
        with conn:
            conn.execute('INSERT INTO metadata VALUES (?,?)', ('schema', SCHEMA))
            for number, entry in enumerate(entries, start=1):
                root = _owned(site, entry['path'])
                catalog = _json(root, 'CATALOG.json')
                manifest = _json(root, 'SITE.json')
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
                        size = html.stat().st_size
                        total += size
                        if size > MAX_DOCUMENT_BYTES or total > MAX_INDEXED_BYTES:
                            raise CatalogError('search: verified reading text exceeds indexing bounds')
                        parser = ReadingText()
                        parser.feed(html.read_text('utf-8'))
                        parser.close()
                        url = quote(entry['path'] + '/editions/' + sha + '/read/' + view_path, safe='/')
                        cursor = conn.execute('INSERT INTO documents '
                                              '(edition_id,work_id,title,path,url,language,source_sha256) '
                                              'VALUES (?,?,?,?,?,?,?)',
                                              (edition_id, number, detail['title'], source_path, url,
                                               detail['language'], detail['source_sha256']))
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
                        conn.execute('INSERT INTO search_records '
                                     '(work_id,edition_id,document_id,url,title,authors,abstract,labels,body,git) '
                                     'VALUES (?,?,?,?,?,?,?,?,?,?)',
                                     (number, edition_id, cursor.lastrowid, url, title, authors,
                                      facts.get('abstract', ''), labels, parser.text(), provenance))
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
                    conn.execute('INSERT INTO search_records '
                                 '(work_id,url,title,authors,abstract,labels,body,git) VALUES (?,?,?,?,?,?,?,?)',
                                 (number, entry['path'] + '/index.html', manifest['title'], '', '', '', '',
                                  entry['repository'] + ' ' + entry['tip']))
            conn.execute("INSERT INTO search_fts(search_fts) VALUES ('rebuild')")
            conn.execute("INSERT INTO search_fts(search_fts) VALUES ('optimize')")
        conn.execute('VACUUM')
        if (conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok'
                or conn.execute('PRAGMA foreign_key_check').fetchall()):
            raise CatalogError('search: database integrity check failed')
        if database.stat().st_size > MAX_DATABASE_BYTES:
            raise CatalogError('search: database exceeds the 256 MiB whole-download limit')
    except Exception:
        conn.close()
        database.unlink(missing_ok=True)
        raise
    else:
        conn.close()
        return database


def _runtime_files():
    assets = Path(__file__).parent / 'assets'
    specified = os.environ.get('GUAVAJAM_SQLITE_WASM_DIR')
    source = Path(specified) if specified else assets
    module_name = 'index.mjs' if specified else 'sqlite3.mjs'
    files = {'sqlite3.mjs': source / module_name, 'sqlite3.wasm': source / 'sqlite3.wasm',
             **{name: assets / name for name in ('search.js', 'search-worker.js', 'sqlite3-LICENSE.txt')}}
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
        body += ('<section class="guavajam-index-search" aria-labelledby="guavajam-search-heading">'
                 '<h2 id="guavajam-search-heading">Search publications</h2>'
                 '<p id="guavajam-search-scope">Search covers all published editions. Keyword and subject '
                 'filters describe each work’s latest edition. Dates are publisher-supplied Git dates.</p>'
                 '<form id="guavajam-search" role="search" aria-describedby="guavajam-search-scope">'
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
                 '<p id="guavajam-search-status" role="status" aria-live="polite">Loading local search…</p>'
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
