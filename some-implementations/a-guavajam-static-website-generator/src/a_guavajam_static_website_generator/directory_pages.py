"""Bounded, crawlable HTML directories enhanced by the existing work SQLite worker.

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

from guavajam.catalog import encoded

from .navigation import document_directory
from .icons import link_icon
from .pagination import PAGE_SIZE, _slices, _static_pager
from .search_artifact import download_attributes, emit_download
from .site import page


def search_progress(identifier):
    """A reserved inline slot keeps loading feedback from moving its label."""
    return ('<span class="guavajam-search-progress" aria-hidden="true">'
            '<span class="guavajam-search-spinner" id="' + identifier + '" hidden></span></span>')


def search_summary(download):
    """Native disclosure is the sole entry point to the optional SQLite index."""
    kib = (download['transfer_size'] + 1023) // 1024
    return ('<summary class="guavajam-search-summary">'
            'Indexed Search <span class="guavajam-index-size" '
            'aria-label="Index download size: ' + str(kib) + ' KiB">(' + str(kib) +
            ' KiB)</span>' + search_progress('guavajam-search-spinner') + '</summary>')


def search_submit():
    """The same stable submit/progress row serves every search directory."""
    return ('<div class="guavajam-search-submit"><button type="submit">Search</button>' +
            search_progress('guavajam-search-query-spinner') +
            '<span id="guavajam-search-progress-status" class="guavajam-visually-hidden" '
            'role="status" aria-live="polite"></span></div>')


def search_listing_open(title, number, pages, *, interactive, result_class='guavajam-editions'):
    """Every static directory starts with the same handoff state as Works."""
    body = ('<section class="guavajam-listing" aria-labelledby="guavajam-works-heading">'
            '<h2 class="guavajam-list-heading" id="guavajam-works-heading" '
            'data-static-heading="' + escape(title, quote=True) + '">' + escape(title) + '</h2>')
    if interactive:
        body += ('<p class="guavajam-meta" id="guavajam-search-status" role="status" aria-live="polite">'
                 'Showing static page ' + str(number) + ' of ' + str(pages) + '.</p>'
                 '<ol id="guavajam-search-results" class="' + result_class + ' guavajam-record-list" hidden></ol>')
    if interactive and title == 'Passages':
        from .citation import citation_control
        body += ('<template id="guavajam-citation-template">' +
                 citation_control({'revision': ''}, '') + '</template>')
    return body


def search_listing_close(*, interactive):
    body = ''
    if interactive:
        body += ('<nav class="guavajam-pagination" aria-label="Search result pages" hidden>'
                 '<button type="button" id="guavajam-search-prev" disabled>Previous</button>'
                 '<span id="guavajam-search-page"></span>'
                 '<button type="button" id="guavajam-search-next" disabled>Next</button></nav>')
    return body + '</section>'


def _sqlite_controls(*, kind, edition, root, runtime, download):
    """Use the same collapsed native search disclosure as the works index.

    JavaScript reveals the closed details control, then initializes the worker
    only if the reader opens it. Static HTML stays independent of JavaScript.
    """
    history = kind == 'editions'
    citations = kind == 'citations'
    label = ('Search versions and commit messages' if history else
             'Search passages in this version' if citations else 'Search supporting-document titles and abstracts')
    sorts = (('<option value="publication">Publication order (newest)</option>'
              '<option value="oldest">Publication order (oldest)</option>'
              '<option value="date-newest">Commit date (newest)</option>'
              '<option value="date-oldest">Commit date (oldest)</option>'
              '<option value="relevance">Relevance</option>') if history else
             ('<option value="publication">Source order</option>'
              '<option value="title">Title (A–Z)</option>'
              '<option value="relevance">Relevance</option>'))
    return ('<details class="guavajam-index-search guavajam-directory-enhancement" '
            'id="guavajam-index-search" hidden>' + search_summary(download) +
            '<form id="guavajam-search" role="search" '
            'data-scope="work" data-directory="' + escape(kind, quote=True) + '" '
            'data-root="' + escape(root, quote=True) + '" ' +
            download_attributes(download, prefix=root) +
            ' data-worker="' + escape(root + runtime + 'search-worker.js', quote=True) + '">'
            '<div class="guavajam-query-field"><label class="guavajam-visually-hidden" for="guavajam-query">' + escape(label) + '</label>'
            '<input id="guavajam-query" name="q" type="search" maxlength="256" autocomplete="off"></div>'
            + search_submit() +
            '<details class="guavajam-search-filters"><summary>Filters and sort</summary>'
            '<div class="guavajam-filter-fields"><div class="guavajam-query-sort">'
            '<label for="guavajam-sort">Sort</label>'
            '<select id="guavajam-sort" name="sort">' + sorts + '</select></div>' +
            ('<div><label for="guavajam-supporting">Supporting documents</label>'
             '<select id="guavajam-supporting" name="supporting">'
             '<option value="all">Include</option><option value="manuscript">Exclude</option>'
             '</select></div>' if citations else '') + '</div></details>'
            '<input type="hidden" name="kind" value="' + ('passages' if citations else escape(kind, quote=True)) + '">'
            '<input type="hidden" name="edition" value="' + escape(edition, quote=True) + '">' +
            '</form></details><script src="' + escape(root + runtime + 'search.js', quote=True) +
            '" defer></script>')


def _history_row(edition, *, links_prefix):
    revision, label, date, message = edition
    subject, _, details = message.partition('\n')
    html = ('<li><article><h3><a href="' + escape(links_prefix + revision + '/read/index.html', quote=True) +
            '">' + escape(label) + '</a>' +
            '</h3><p class="guavajam-meta guavajam-scrollline" tabindex="0" aria-label="Version date and source"><time datetime="' + escape(date, quote=True) +
            '">' + escape(date) + '</time> · <code title="' + escape(revision, quote=True) +
            '">' + escape(revision[:12]) + '</code> · <a href="' +
            escape(links_prefix + revision + '/metadata.html#provenance', quote=True) +
            '">Metadata</a></p>' +
            '<p class="guavajam-message">' + escape(subject) + '</p>')
    if details.strip():
        html += ('<details class="guavajam-message-detail"><summary>Full commit message</summary>'
                 '<p class="guavajam-message">' + escape(details) + '</p></details>')
    return html + '</article></li>'


def _write_work(output, entries, title, *, collection, runtime, download, site_origin=None):
    chunks = _slices(reversed(entries))  # full verified history, newest first
    for number, chunk in enumerate(chunks, 1):
        first = number == 1
        root = '' if first else '../'
        destination = output / 'index.html' if first else output / 'page' / f'{number}.html'
        destination.parent.mkdir(parents=True, exist_ok=True)
        crumbs = ((root + '../../index.html', 'GuavaJam'),) if collection else ()
        if not first:
            crumbs += ((root + 'index.html', title),)
        body = ''
        if download is not None:
            body += _sqlite_controls(kind='editions', edition='all', root=root,
                                     runtime=runtime, download=download)
        body += search_listing_open('Versions', number, len(chunks), interactive=download is not None)
        body += ('<section id="guavajam-static-section" class="guavajam-directory">'
                 '<ol class="guavajam-editions guavajam-record-list">' +
                 ''.join(_history_row(item, links_prefix=root + 'editions/') for item in chunk) +
                 '</ol>')
        if not entries:
            body += '<p>No published versions.</p>'
        body += _static_pager(number, len(chunks), first_href='index.html' if first else '../index.html',
                              page_prefix='page/' if first else '')
        body += '</section>' + search_listing_close(interactive=download is not None)
        destination.write_text(page(title if first else f'{title} — page {number}',
                                    body, search=download is not None, ancestors=crumbs,
                                    breadcrumb_current=title if first else f'Page {number}', site_origin=site_origin), encoding='utf-8')


def _write_documents(output, entries, *, collection, runtime, download, site_origin=None):
    for position, (revision, _, date, _) in enumerate(entries, 1):
        read = output / 'editions' / revision / 'read'
        view_path = read / 'VIEW.json'
        view = json.loads(view_path.read_text('utf-8'))
        navigation = json.loads((read / 'NAV.json').read_text('utf-8'))
        chunks = _slices((path, doc) for path, doc in navigation['documents'].items()
                         if path != 'content/manuscript.md')
        title = view['facts']['title']
        for number, chunk in enumerate(chunks, 1):
            first = number == 1
            read_root = '' if first else '../../'
            root = read_root + '../../../'
            target = read / 'documents.html' if first else read / 'documents' / 'page' / f'{number}.html'
            target.parent.mkdir(parents=True, exist_ok=True)
            crumbs = (((root + '../../index.html', 'GuavaJam'),) if collection else ()) + (
                (root + 'index.html', title), (read_root + 'index.html', 'v' + str(position)))
            body = ''
            if download is not None:
                body += _sqlite_controls(kind='documents', edition=revision, root=root,
                                         runtime=runtime, download=download)
            body += search_listing_open('Documents', number, len(chunks), interactive=download is not None)
            body += ('<section id="guavajam-static-section" class="guavajam-directory">' +
                     document_directory({'documents': dict(chunk)}, prefix=read_root,
                                        headings=True))
            body += _static_pager(number, len(chunks),
                                  first_href='documents.html' if first else '../../documents.html',
                                  page_prefix='documents/page/' if first else '')
            body += '</section>' + search_listing_close(interactive=download is not None)
            html = page('Documents' if first else f'Documents — page {number}', body,
                        search=download is not None, ancestors=crumbs, breadcrumb_current='Documents', site_origin=site_origin)
            target.write_text(html, encoding='utf-8')
            relative = target.relative_to(read).as_posix()
            view['files'][relative] = hashlib.sha256(html.encode('utf-8')).hexdigest()
        view_path.write_bytes(encoded(view))



def _write_contents(output, entries, *, collection, site_origin=None):
    """One script-free Contents page per manuscript, from the verified NAV outline.

    Keeping source anchors unchanged lets every TOC link target the original
    manuscript reader. The standalone render cache retains its own outline.
    """
    for position, (revision, _, _, _) in enumerate(entries, 1):
        read = output / 'editions' / revision / 'read'
        view_path = read / 'VIEW.json'
        view = json.loads(view_path.read_text('utf-8'))
        navigation = json.loads((read / 'NAV.json').read_text('utf-8'))
        manuscript = navigation['canonical']['manuscript']
        doc = navigation['documents'][manuscript]
        from .navigation import section_outline
        roots = section_outline(doc['anchors'])

        def contents(nodes):
            return '<ol>' + ''.join(
                '<li><p class="guavajam-contents-entry guavajam-scrollline" tabindex="0" aria-label="Contents entry: ' + escape(node['record']['title'], quote=True) + '">'
                '<a class="guavajam-contents-link" href="index.html#' + escape(node['record']['anchor'], quote=True) +
                '" aria-label="Go to section ' + escape(node['record']['title'], quote=True) + '">' + link_icon('link', 'Go to section') + '</a> ' +
                escape(node['record']['title']) + '</p>' +
                (contents(node['children']) if node['children'] else '') + '</li>'
                for node in nodes) + '</ol>'

        ancestors = ((( '../../../../../index.html', 'GuavaJam'),) if collection else ()) + (
            ('../../../index.html', view['facts']['title']), ('index.html', 'v' + str(position)))
        body = '<h2>Table of Contents</h2>' + ('<nav class="guavajam-contents-directory" aria-label="Manuscript outline">'
                '<div class="guavajam-contents-list">' + contents(roots) + '</div></nav>' if roots
                else '<p>No individually titled sections were declared in this manuscript.</p>')
        html = page(view['facts']['title'] + ' — Contents', body,
                    ancestors=ancestors, breadcrumb_current='Contents', site_origin=site_origin)
        target = read / 'contents.html'
        target.write_text(html, encoding='utf-8')
        view['files']['contents.html'] = hashlib.sha256(html.encode('utf-8')).hexdigest()
        view_path.write_bytes(encoded(view))


def build_directories(output, editions, *, collection, search_assets, database, site_origin=None):
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
    _write_work(output, editions, title, collection=collection, runtime=runtime, download=download, site_origin=site_origin)
    _write_documents(output, editions, collection=collection, runtime=runtime, download=download, site_origin=site_origin)
    _write_contents(output, editions, collection=collection, site_origin=site_origin)
    from .citation_pages import build_citation_pages
    build_citation_pages(output, editions, collection=collection, runtime=runtime, download=download, site_origin=site_origin)
