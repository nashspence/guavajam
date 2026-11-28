"""Disposable aggregate search derived only from independently verified views.

The SQLite artifact is an application index, not a publication or an authority
record. Subject declarations follow GuavaJam's DataCite Subject application profile;
exact declarations identify facets without inferring synonyms or hierarchies.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
from html import escape
import os
from pathlib import Path
import shutil
import sqlite3

from guavajam.bibliography import ordered_authors
from guavajam.catalog import CatalogError, encoded

from .search_artifact import download_attributes, emit_download
from .icons import icon

MAX_DOCUMENT_BYTES = 8 * 1024 * 1024


from .navigation_manifest import owned as _owned, read_json


def _json(root, relative, *, maximum=MAX_DOCUMENT_BYTES):
    return read_json(root, relative, maximum)


def _navigation(read, view):
    """Consume the same bounded source-derived inventory as static navigation.

    A render's inventory hashes the derivative; it does not replace signature
    verification of the source, which has already happened before site building.
    """
    from .navigation_manifest import load_navigation
    navigation = load_navigation(read, view)
    return navigation, (Path(read) / 'NAV.json').stat().st_size


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
    return ', '.join(item['name'] for item in ordered_authors(facts)) if facts else ''


def create_index(site, entries, *, filename='index.sqlite', scope='collection', revision=None):
    """Build only the selected logical corpus; no all-history loading shortcut."""
    from .search_build import build
    return build(site, entries, filename=filename, scope=scope, revision=revision)


def _runtime_files():
    assets = Path(__file__).parent / 'assets'
    specified = os.environ.get('GUAVAJAM_SQLITE_WASM_DIR')
    source = Path(specified) if specified else assets
    module_name = 'index.mjs' if specified else 'sqlite3.mjs'
    files = {'sqlite3.mjs': source / module_name, 'sqlite3.wasm': source / 'sqlite3.wasm',
             **{name: assets / name for name in
                ('search.js', 'search-worker.js', 'search-download.js', 'search-facets.js', 'search-hash.js', 'search-chunks.js', 'search-limits.js', 'search-state.js', 'fflate.mjs', 'fflate-LICENSE.txt', 'sqlite3-LICENSE.txt')}}
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
    rows = conn.execute('SELECT DISTINCT key, name FROM subjects').fetchall()
    variants = {}
    for key, name in rows:
        variants.setdefault(name, set()).add(key)
    return {key: (name if len(variants[name]) == 1 else None) for key, name in rows}


def _facets(keywords, subjects):
    """Use the shared categorical row presentation."""
    from .facets import facet, facet_line
    return facet_line([facet('keyword', item, item) for item in keywords] +
                      [facet('subject', key, label) for key, label in subjects], 'Subjects and tags')


def homepage(site, *, interactive, site_origin=None):
    """Emit a bounded static works directory, enhanced by the existing SQLite UI.

    Each index page contains at most PAGE_SIZE works. The other pages are real
    HTML documents linked with ordinary hrefs, not JS-dependent slices of a
    large hidden list. Querying/searching/sorting in the browser still uses
    the same collection SQLite worker and only loads on interaction.
    """
    from .directory_pages import (PAGE_SIZE, _static_pager, search_submit,
                                  search_listing_open, search_listing_close, disclosure_summary)
    from .site import detail_link, page

    site = Path(site)
    first_html = None
    with closing(sqlite3.connect(site / 'index.sqlite')) as conn:
        count = conn.execute('SELECT count(*) FROM works').fetchone()[0]
        pages = max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE)
        cursor = conn.execute('''
            SELECT w.id,w.title,w.authors,w.abstract,w.latest_published,w.latest_revision,
              r.path,r.url
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
            for work_id, title, authors, abstract, day, revision, path, source in records:
                work = base + path + '/'
                target = (work + 'editions/' + revision + '/read/index.html') if revision else work + 'index.html'
                byline = ('<time datetime="' + escape(day, quote=True) + '">' + escape(day) + '</time>') if day else ''
                if authors:
                    byline += (' · ' if byline else '') + escape(authors)
                byline = detail_link(work + 'index.html', title + ' work page') + (' ' + byline if byline else '')
                entries.append('<li><article><h3><a href="' + escape(target, quote=True) + '">' +
                               escape(title) + '</a>' +
                               '</h3>' +
                               ('<p class="guavajam-byline guavajam-scrollline" tabindex="0" aria-label="Work date and authors">' + byline + '</p>' if byline else '') +
                               _facets(tags[work_id], subjects[work_id]) +
                               ('<p class="guavajam-abstract">' + escape(abstract) + '</p>' if abstract else '') +
                               '</article></li>')
            controls = ''
            if interactive:
                controls = (
                    '<div class="guavajam-index-search" id="guavajam-index-search" hidden>' +
                    '<form id="guavajam-search" role="search" '
                    'data-scope="collection" data-root="' + escape(base, quote=True) + '" '
                    'data-worker="' + escape(base + 'search-worker.js', quote=True) + '" ' +
                    download_attributes(download, prefix=base) + '>'
                    '<div class="guavajam-query-field"><label class="guavajam-visually-hidden" for="guavajam-query">Search works</label>'
                    '<input id="guavajam-query" name="q" type="search" maxlength="256" autocomplete="off"></div>'
                    + search_submit() +
                    '<details class="guavajam-search-filters">' + disclosure_summary('Filters and sort') +
                    '<div class="guavajam-filter-fields">'
                    '<div><label for="guavajam-keyword">Keyword</label>'
                    '<select id="guavajam-keyword" name="keyword"><option value="">All keywords</option></select></div>'
                    '<div><label for="guavajam-subject">Subject</label>'
                    '<select id="guavajam-subject" name="subject"><option value="">All subjects</option></select></div>'
                    '<div class="guavajam-date-range"><div><label for="guavajam-date-from">' + icon('calendar-days') + ' Published from</label>'
                    '<input type="date" id="guavajam-date-from" name="from"></div>'
                    '<div><label for="guavajam-date-to">' + icon('calendar-days') + ' Published to</label>'
                    '<input type="date" id="guavajam-date-to" name="to"></div></div>'
                    '<div><label for="guavajam-sort">Sort</label><select id="guavajam-sort" name="sort">'
                    '<option value="recent">Most recent</option><option value="relevance">Relevance</option>'
                    '<option value="title">Title</option><option value="oldest">Oldest</option>'
                    '<option value="editions">Most editions</option></select></div></div></details>'
                    '</form></div>')
            body = search_listing_open('Works', count, controls=controls, result_class='guavajam-works',
                                       feed_href=base + 'feed.atom', feed_title='Subscribe to collection updates')
            body += ('<div id="guavajam-static-section"><ol id="guavajam-static-list" '
                     'class="guavajam-works guavajam-record-list">' + ''.join(entries) + '</ol>')
            body += _static_pager(number, pages,
                                  first_href='index.html' if number == 1 else '../index.html',
                                  page_prefix='page/' if number == 1 else '')
            body += '</div>' + search_listing_close(interactive=interactive)
            if interactive:
                body += '<script src="' + escape(base + 'search.js', quote=True) + '" defer></script>'
            # A collection spans Git repositories and cannot have one canonical
            # publication-commit citation.
            html = page('GuavaJam' if number == 1 else f'GuavaJam — page {number}',
                         body, search=interactive, breadcrumb_current='GuavaJam', site_origin=site_origin,
                         feed_href=base + 'feed.atom', feed_title='GuavaJam — Collection updates')
            if number == 1:
                first_html = html
            else:
                target = site / 'page' / f'{number}.html'
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(html, encoding='utf-8')
    return first_html
