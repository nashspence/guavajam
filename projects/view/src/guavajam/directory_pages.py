"""Bounded, indexable HTML directories enhanced by the existing work SQLite worker.

Static pages are rendered from verified catalog/NAV records, never from a
client-side metadata index. Searching/sorting uses the same opt-in SQLite
runtime and query code as the work browser. No JavaScript is required to reach
any document or edition through page links.
"""
from __future__ import annotations

import hashlib
from html import escape
import json
from pathlib import Path

from guavajam_core.bibliography import author_text, ordered_authors
from guavajam_core.catalog import encoded

from .navigation import document_directory
from .pagination import PAGE_SIZE, _slices, _static_pager
from .search_artifact import download_attributes, emit_download
from .site import page


def _sqlite_controls(*, kind, edition, root, runtime, download, document=''):
    """Use the collection's explicit, opt-in SQLite search disclosure.

    JS only reveals the controls. It must not fetch the worker/WASM/database
    until Enable search (or an explicit form submission). Static HTML is still
    complete, bounded and navigable without a script or a secondary index.
    """
    history = kind == 'editions'
    citations = kind == 'citations'
    heading = 'Find versions' if history else 'Find passages' if citations else 'Find documents'
    label = ('Search versions and commit messages' if history else
             'Search passages in this document' if citations else 'Search documents and contents')
    sorts = (('<option value="publication">Publication order (newest)</option>'
              '<option value="oldest">Publication order (oldest)</option>'
              '<option value="date-newest">Commit date (newest)</option>'
              '<option value="date-oldest">Commit date (oldest)</option>'
              '<option value="relevance">Relevance</option>') if history else
             ('<option value="publication">Source order</option>'
              '<option value="title">Title (A–Z)</option>'
              '<option value="relevance">Relevance</option>'))
    kib = (download['transfer_size'] + 1023) // 1024
    return ('<section class="guavajam-index-search guavajam-directory-enhancement" '
            'id="guavajam-directory-enhancement" aria-labelledby="guavajam-search-heading" hidden>'
            '<div class="guavajam-search-intro"><h2 id="guavajam-search-heading">' + heading + '</h2>'
            '<button type="button" id="guavajam-search-enable">Enable search</button></div>'
            '<p class="guavajam-search-help" id="guavajam-search-scope">Optional local SQLite search; '
            'downloads ' + str(kib) + ' KiB of index data only when enabled. '
            'Browse the static pages below without search.</p>'
            '<form id="guavajam-search" role="search" aria-describedby="guavajam-search-scope" '
            'data-scope="work" data-directory="' + escape(kind, quote=True) + '" '
            'data-root="' + escape(root, quote=True) + '" ' +
            download_attributes(download, prefix=root) +
            ' data-worker="' + escape(root + runtime + 'search-worker.js', quote=True) + '">'
            '<div class="guavajam-query-field"><label for="guavajam-query">' + escape(label) + '</label>'
            '<input id="guavajam-query" name="q" type="search" maxlength="256" autocomplete="off"></div>'
            '<button type="submit">Search</button>'
            '<details class="guavajam-search-filters"><summary>Filters and sort</summary>'
            '<div class="guavajam-filter-fields"><div class="guavajam-query-sort">'
            '<label for="guavajam-sort">Sort</label>'
            '<select id="guavajam-sort" name="sort">' + sorts + '</select></div></div></details>'
            '<input type="hidden" name="kind" value="' + ('passages' if citations else escape(kind, quote=True)) + '">'
            '<input type="hidden" name="edition" value="' + escape(edition, quote=True) + '">' +
            ('<input type="hidden" name="document" value="' + escape(document, quote=True) + '">'
               if citations else '') +
            '</form>'
            '<p class="guavajam-meta" id="guavajam-search-status" role="status" aria-live="polite">'
            'Select Enable search to explore the SQLite index.</p>'
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
            '</h2><p class="guavajam-meta"><time datetime="' + escape(date, quote=True) +
            '">' + escape(date) + '</time> · <code title="' + escape(revision, quote=True) +
            '">' + escape(revision[:12]) + '</code> · <a href="' +
            escape(links_prefix + revision + '/verify.html', quote=True) +
            '">Verify</a></p>' +
            '<p class="guavajam-message">' + escape(subject) + '</p>')
    if details.strip():
        html += ('<details class="guavajam-message-detail"><summary>Full commit message</summary>'
                 '<p class="guavajam-message">' + escape(details) + '</p></details>')
    return html + '</li>'


def _write_history(output, entries, title, *, collection, runtime, download):
    chunks = _slices(reversed(entries))  # full verified history, newest first
    directory = output / 'editions'
    for number, chunk in enumerate(chunks, 1):
        first = number == 1
        root = '../' if first else '../../'
        destination = directory / 'index.html' if first else directory / 'page' / f'{number}.html'
        destination.parent.mkdir(parents=True, exist_ok=True)
        crumbs = (((root + '../../index.html', 'GuavaJam'),) if collection else ()) + ((root + 'index.html', title),)
        body = '<p>Verified versions, newest first in publication order.</p>'
        if download is not None:
            body += _sqlite_controls(kind='editions', edition='all', root=root,
                                     runtime=runtime, download=download)
        body += ('<section id="guavajam-static-section" class="guavajam-directory">'
                 '<ol class="guavajam-editions">' +
                 ''.join(_history_row(item, links_prefix='' if first else '../') for item in chunk) +
                 '</ol>')
        if not entries:
            body += '<p>No published versions.</p>'
        body += _static_pager(number, len(chunks), first_href='index.html' if first else '../index.html',
                              page_prefix='page/' if first else '')
        body += '</section>'
        destination.write_text(page('Versions' if first else f'Versions — page {number}',
                                    body, search=download is not None, ancestors=crumbs,
                                    breadcrumb_current='Versions'), encoding='utf-8')


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
                (root + 'index.html', title), (read_root + 'index.html', 'v' + str(position)))
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
    from .citation_pages import build_citation_pages
    build_citation_pages(output, editions, collection=collection, runtime=runtime, download=download)
