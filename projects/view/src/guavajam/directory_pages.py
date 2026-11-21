"""Bounded, indexable HTML directories enhanced by the existing work SQLite worker.

Static pages are rendered from verified catalog/NAV records, never from a
client-side metadata index. Searching/sorting uses the same opt-in SQLite
runtime and query code as the work browser. No JavaScript is required to reach
any document or previous edition through page links.
"""
from __future__ import annotations

import hashlib
from html import escape
import json
from pathlib import Path

from guavajam_core.bibliography import author_text, ordered_authors
from guavajam_core.catalog import encoded

from .navigation import document_directory
from .search_artifact import download_attributes, emit_download
from .site import citation_control, detail_link, page

PAGE_SIZE = 20


def _slices(items):
    items = list(items)
    return [items[start:start + PAGE_SIZE] for start in range(0, len(items), PAGE_SIZE)] or [[]]


def _static_pager(number, count, *, first_href, page_prefix):
    """Ordinary href pagination for HTTP crawlers and browsers without JS."""
    if count < 2:
        return ''

    def link(page_number, label, *, relation=''):
        href = first_href if page_number == 1 else page_prefix + str(page_number) + '.html'
        return ('<a href="' + escape(href, quote=True) + '"' +
                (' rel="' + relation + '"' if relation else '') + '>' + escape(str(label)) + '</a>')

    links = []
    if number > 1:
        links.append(link(number - 1, 'Previous', relation='prev'))
    if number > 3:
        links.append(link(1, '1'))
        if number > 4:
            links.append('<span aria-hidden="true">…</span>')
    for candidate in range(max(1, number - 2), min(count, number + 2) + 1):
        if candidate == number:
            links.append('<span aria-current="page">' + str(number) + '</span>')
        else:
            links.append(link(candidate, candidate))
    if number < count - 2:
        if number < count - 3:
            links.append('<span aria-hidden="true">…</span>')
        links.append(link(count, count))
    if number < count:
        links.append(link(number + 1, 'Next', relation='next'))
    return ('<nav class="guavajam-pagination guavajam-static-pages" aria-label="Directory pages">'
            '<span class="guavajam-meta">Page ' + str(number) + ' of ' + str(count) + '</span>' +
            ''.join(links) + '</nav>')


def _sqlite_controls(*, kind, edition, root, runtime, download):
    """Markup binds to search.js/search-worker.js, without another query layer.

    Hidden until the JavaScript controller has loaded: a no-JS directory shows
    only working static links, not nonfunctional form controls. The worker's
    database and wasm are fetched after search/sort interaction, not on load.
    """
    history = kind == 'editions'
    label = 'Search previous editions and commit messages' if history else 'Search documents and contents'
    sorts = (('<option value="publication">Publication order (newest)</option>'
              '<option value="oldest">Publication order (oldest)</option>'
              '<option value="date-newest">Commit date (newest)</option>'
              '<option value="date-oldest">Commit date (oldest)</option>'
              '<option value="relevance">Relevance</option>') if history else
             ('<option value="publication">Source order</option>'
              '<option value="title">Title (A–Z)</option>'
              '<option value="relevance">Relevance</option>'))
    return ('<section class="guavajam-directory-enhancement" id="guavajam-directory-enhancement" hidden>'
            '<form id="guavajam-search" role="search" data-scope="work" '
            'data-directory="' + escape(kind, quote=True) + '" data-lazy="interaction" '
            'data-root="' + escape(root, quote=True) + '" ' +
            download_attributes(download, prefix=root) +
            ' data-worker="' + escape(root + runtime + 'search-worker.js', quote=True) + '">'
            '<div class="guavajam-query-field"><label for="guavajam-query">' + escape(label) + '</label>'
            '<input id="guavajam-query" name="q" type="search" maxlength="256" autocomplete="off"></div>'
            '<div class="guavajam-query-sort"><label for="guavajam-sort">Sort</label>'
            '<select id="guavajam-sort" name="sort">' + sorts + '</select></div>'
            '<button type="submit">Search</button>'
            '<input type="hidden" name="kind" value="' + escape(kind, quote=True) + '">'
            '<input type="hidden" name="edition" value="' + escape(edition, quote=True) + '">'
            '</form>'
            '<p class="guavajam-meta" id="guavajam-search-status" role="status" aria-live="polite">'
            'Search or change sort to load the SQLite index.</p>'
            '<ol id="guavajam-search-results" class="guavajam-editions" hidden></ol>'
            '<nav class="guavajam-pagination" aria-label="SQLite search result pages" hidden>'
            '<button type="button" id="guavajam-search-prev" disabled>Previous</button>'
            '<span id="guavajam-search-page"></span>'
            '<button type="button" id="guavajam-search-next" disabled>Next</button></nav>'
            '</section><script src="' + escape(root + runtime + 'search.js', quote=True) +
            '" defer></script>')


def _history_row(edition, *, links_prefix):
    revision, label, date, message = edition
    subject, _, details = message.partition('\n')
    html = ('<li><h2><a href="' + escape(links_prefix + revision + '/read/index.html', quote=True) +
            '">' + escape(label) + '</a>' +
            detail_link(links_prefix + revision + '/index.html', label + ' record') +
            '</h2><p class="guavajam-meta"><time datetime="' + escape(date, quote=True) +
            '">' + escape(date) + '</time> · <code title="' + escape(revision, quote=True) +
            '">' + escape(revision[:12]) + '</code></p>' +
            '<p class="guavajam-message">' + escape(subject) + '</p>')
    if details.strip():
        html += ('<details class="guavajam-message-detail"><summary>Full commit message</summary>'
                 '<p class="guavajam-message">' + escape(details) + '</p></details>')
    return html + '</li>'


def _write_history(output, entries, title, *, collection, runtime, download):
    chunks = _slices(reversed(entries[:-1]))  # latest edition is on the work cover, not here
    directory = output / 'editions'
    for number, chunk in enumerate(chunks, 1):
        first = number == 1
        root = '../' if first else '../../'
        destination = directory / 'index.html' if first else directory / 'page' / f'{number}.html'
        destination.parent.mkdir(parents=True, exist_ok=True)
        crumbs = (((root + '../../index.html', 'GuavaJam'),) if collection else ()) + ((root + 'index.html', title),)
        body = '<p>Earlier verified editions, in publication order.</p>'
        if download is not None:
            body += _sqlite_controls(kind='editions', edition='previous', root=root,
                                     runtime=runtime, download=download)
        body += ('<section id="guavajam-static-section" class="guavajam-directory">'
                 '<ol class="guavajam-editions">' +
                 ''.join(_history_row(item, links_prefix='' if first else '../') for item in chunk) +
                 '</ol>')
        if not entries[:-1]:
            body += '<p>No previous editions.</p>'
        body += _static_pager(number, len(chunks), first_href='index.html' if first else '../index.html',
                              page_prefix='page/' if first else '')
        body += '</section>'
        destination.write_text(page('Previous editions' if first else f'Previous editions — page {number}',
                                    body, search=download is not None, ancestors=crumbs,
                                    breadcrumb_current='Previous editions'), encoding='utf-8')


def _write_documents(output, entries, *, collection, runtime, download):
    for position, (revision, _, date, _) in enumerate(entries, 1):
        read = output / 'editions' / revision / 'read'
        view_path = read / 'VIEW.json'
        view = json.loads(view_path.read_text('utf-8'))
        navigation = json.loads((read / 'NAV.json').read_text('utf-8'))
        chunks = _slices(navigation['documents'].items())
        citation = json.loads((read.parent / 'CITATION.json').read_text('utf-8'))
        title = view['facts']['title']
        authors = author_text(ordered_authors(view['facts']), identifiers=False)
        for number, chunk in enumerate(chunks, 1):
            first = number == 1
            read_root = '' if first else '../../'
            root = read_root + '../../../'
            target = read / 'documents.html' if first else read / 'documents' / 'page' / f'{number}.html'
            target.parent.mkdir(parents=True, exist_ok=True)
            crumbs = (((root + '../../index.html', 'GuavaJam'),) if collection else ()) + (
                (root + 'index.html', title), (read_root + '../index.html', f'Edition {position}'))
            citation_href = read_root + '../CITATION.json'
            body = '<article class="guavajam-edition"><p>Manuscript and supporting documents in this edition.</p>'
            if download is not None:
                body += _sqlite_controls(kind='documents', edition=revision, root=root,
                                         runtime=runtime, download=download)
            body += ('<section id="guavajam-static-section" class="guavajam-directory">' +
                     document_directory({'documents': dict(chunk)}, prefix=read_root,
                                        include_sections=False))
            body += _static_pager(number, len(chunks),
                                  first_href='documents.html' if first else '../../documents.html',
                                  page_prefix='documents/page/' if first else '')
            body += '</section></article>'
            html = page('Documents' if first else f'Documents — page {number}', body,
                        search=download is not None, ancestors=crumbs, breadcrumb_current='Documents',
                        citation_record=citation, citation_href=citation_href,
                        citation_title=title, citation_authors=authors,
                        citation_edition=position, citation_published=date)
            target.write_text(html, encoding='utf-8')
            relative = target.relative_to(read).as_posix()
            view['files'][relative] = hashlib.sha256(html.encode('utf-8')).hexdigest()
        view_path.write_bytes(encoded(view))


def build_directories(output, editions, *, collection, search_assets, database):
    """Generate every bounded HTML page after the verified SQLite index exists."""
    output = Path(output)
    interactive = database is not None and (bool(search_assets) or (output / 'search.js').is_file())
    download = emit_download(database) if interactive else None
    # A collection shares its single runtime; standalone works self-host it.
    runtime = '../../' if search_assets else ''
    if editions:
        title = json.loads((output / 'editions' / editions[-1][0] / 'read' / 'VIEW.json').read_text())['facts']['title']
    else:
        title = 'Unpublished work'
    _write_history(output, editions, title, collection=collection, runtime=runtime, download=download)
    _write_documents(output, editions, collection=collection, runtime=runtime, download=download)
