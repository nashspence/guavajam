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
from .icons import icon, link_icon
from .pagination import PAGE_SIZE, _slices, _static_pager, page_control
from .search_artifact import download_attributes, emit_download
from .site import detail_link, page


def search_progress(identifier):
    """A reserved inline slot keeps loading feedback from moving its label."""
    return ('<span class="guavajam-search-progress" aria-hidden="true">'
            '<span class="guavajam-search-spinner" id="' + identifier + '" hidden></span></span>')


def disclosure_summary(label, *, extra='', css=''):
    """Keep native keyboard toggling; only the arrow accepts pointer clicks."""
    return ('<summary class="guavajam-disclosure-summary ' + css + '">'
            '<span class="guavajam-disclosure-arrow">' + icon('chevron-right') + '</span>'
            '<span class="guavajam-disclosure-label">' + escape(label) + '</span>' + extra + '</summary>')


def search_submit():
    return '<div class="guavajam-search-submit"><button type="submit">Search</button></div>'


def search_listing_open(title, total, *, controls='', result_class='guavajam-editions',
                        feed_href=None, feed_title=None):
    """One stable heading, optional icon toggle, shared progress slot and rule."""
    body = '<section class="guavajam-listing" aria-labelledby="guavajam-works-heading">'
    body += ('<div class="guavajam-list-header guavajam-scrollline" tabindex="0" aria-label="List heading and status">')
    if controls:
        body += ('<span class="guavajam-search-toggle-slot"><button id="guavajam-search-toggle" '
                 'class="guavajam-search-toggle" type="button" hidden aria-expanded="false" '
                 'aria-controls="guavajam-index-search" aria-label="Search ' + escape(title, quote=True) +
                 '" title="Search ' + escape(title, quote=True) + '">' + link_icon('search', 'Search') + '</button></span>')
    body += ('<h2 class="guavajam-list-heading" id="guavajam-works-heading" '
             'data-static-heading="' + escape(title, quote=True) + '">' + escape(title) + '</h2>')
    feed_link = ''
    if feed_href:
        label = feed_title or 'Subscribe to ' + title
        feed_link = ('<a class="guavajam-feed-link" href="' + escape(feed_href, quote=True) +
                 '" aria-label="' + escape(label, quote=True) + '" title="' +
                 escape(label, quote=True) + '">' + link_icon('rss', 'Subscribe') + '</a>')
    if controls:
        body += search_progress('guavajam-search-spinner')
    body += ('<p class="guavajam-meta" id="guavajam-search-status" role="status" aria-live="polite">'
             + str(total) + ' total</p>' + feed_link + '</div>' + controls + '<hr class="guavajam-list-divider">')
    if controls:
        body += ('<span id="guavajam-search-progress-status" class="guavajam-visually-hidden" '
                 'role="status" aria-live="polite"></span>'
                 '<ol id="guavajam-search-results" class="' + result_class + ' guavajam-record-list" hidden></ol>')
    if controls and title in {'Passages', 'Versions'}:
        from .citation import citation_control
        body += ('<template id="guavajam-citation-template">' +
                 citation_control({'revision': ''}, '') + '</template>')
    return body


def search_listing_close(*, interactive):
    body = ''
    if interactive:
        body += ('<nav class="guavajam-pagination" aria-label="List pages" hidden>' +
                 page_control('prev', identifier='guavajam-search-prev') +
                 '<span class="guavajam-page-label" id="guavajam-search-page"></span>' +
                 page_control('next', identifier='guavajam-search-next') + '</nav>')
    return body + '</section>'


def _sqlite_controls(*, kind, edition, root, runtime, download):
    """Use the same optional search panel as the works index.

    JavaScript reveals the heading toggle, then initializes the worker
    only when a search executes. Static HTML stays independent of JavaScript.
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
    return ('<div class="guavajam-index-search guavajam-directory-enhancement" '
            'id="guavajam-index-search" hidden>' +
            '<form id="guavajam-search" role="search" '
            'data-scope="' + ('versions' if history else 'passages' if citations else 'documents') + '" data-directory="' + escape(kind, quote=True) + '" '
            'data-root="' + escape(root, quote=True) + '" ' +
            download_attributes(download, prefix=root) +
            ' data-worker="' + escape(root + runtime + 'search-worker.js', quote=True) + '">'
            + ('<fieldset class="guavajam-search-scope"><legend class="guavajam-visually-hidden">Search scope</legend>'
             '<div class="guavajam-scope-options"><label><input type="radio" name="search-scope" value="versions" checked>Versions</label>'
             '<label><input type="radio" name="search-scope" value="history">Complete history</label></div></fieldset>' if history else '') +
            '<div class="guavajam-query-field"><label class="guavajam-visually-hidden" for="guavajam-query">' + escape(label) + '</label>'
            '<input id="guavajam-query" name="q" type="search" maxlength="256" autocomplete="off"></div>'
            + search_submit() +
            '<details class="guavajam-search-filters">' + disclosure_summary('Filters and sort') +
            '<div class="guavajam-filter-fields"><div class="guavajam-query-sort">'
            '<label for="guavajam-sort">Sort</label>'
            '<select id="guavajam-sort" name="sort">' + sorts + '</select></div>' +
            ('<div><label for="guavajam-supporting">Supporting documents</label>'
             '<select id="guavajam-supporting" name="supporting">'
             '<option value="all">Include</option><option value="manuscript">Exclude</option>'
             '</select></div>' if citations or history else '') + '</div></details>'
            '<input type="hidden" name="kind" value="' + ('passages' if citations else escape(kind, quote=True)) + '">'
            '<input type="hidden" name="edition" value="' + escape(edition, quote=True) + '">' +
            '</form></div><script src="' + escape(root + runtime + 'search.js', quote=True) +
            '" defer></script>')


def _history_row(edition, *, links_prefix):
    revision, label, date, message = edition
    subject, _, details = message.partition('\n')
    html = ('<li><article><h3><a href="' + escape(links_prefix + revision + '/read/index.html', quote=True) +
            '">' + escape(label) + '</a>' +
            '</h3><p class="guavajam-meta guavajam-scrollline" tabindex="0" aria-label="Version date and source"><time datetime="' + escape(date, quote=True) +
            '">' + escape(date) + '</time> · <code title="' + escape(revision, quote=True) +
            '">' + escape(revision[:12]) + '</code>' +
            detail_link(links_prefix + revision + '/metadata.html#provenance', 'Metadata for ' + label,
                        icon_name='info', fallback='Metadata') + '</p>' +
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
        controls = ''
        if download is not None:
            controls = _sqlite_controls(kind='editions', edition='all', root=root,
                                     runtime=runtime, download=download)
        body = search_listing_open('Versions', len(entries), controls=controls,
                                   feed_href=root + 'feed.atom', feed_title='Subscribe to work editions')
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
                                     breadcrumb_current=title if first else f'Page {number}', site_origin=site_origin,
                                     feed_href=root + 'feed.atom', feed_title=title + ' — Editions'), encoding='utf-8')


def _write_documents(output, entries, *, collection, runtime, downloads, site_origin=None):
    for position, (revision, _, date, _) in enumerate(entries, 1):
        read = output / 'editions' / revision / 'read'
        download = downloads.get(revision)
        view_path = read / 'VIEW.json'
        view = json.loads(view_path.read_text('utf-8'))
        documents = [(path, doc) for path, doc in view['documents'].items()
                     if path != 'content/manuscript.md']
        chunks = _slices(documents)
        title = view['facts']['title']
        for number, chunk in enumerate(chunks, 1):
            first = number == 1
            read_root = '' if first else '../../'
            root = read_root + '../../../'
            target = read / 'documents.html' if first else read / 'documents' / 'page' / f'{number}.html'
            target.parent.mkdir(parents=True, exist_ok=True)
            crumbs = (((root + '../../index.html', 'GuavaJam'),) if collection else ()) + (
                (root + 'index.html', title), (read_root + 'index.html', 'v' + str(position)))
            controls = ''
            if download is not None:
                controls = _sqlite_controls(kind='documents', edition=revision, root=root,
                                         runtime=runtime, download=download)
            body = search_listing_open('Documents', len(documents), controls=controls)
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
    """Give every source document the same script-free author-outline view."""
    from .citation_routes import document_contents, relative_href
    from .navigation import section_outline
    for position, (revision, _, _, _) in enumerate(entries, 1):
        read = output / 'editions' / revision / 'read'
        view_path = read / 'VIEW.json'
        view = json.loads(view_path.read_text('utf-8'))
        from .navigation_manifest import load_navigation
        navigation = load_navigation(read, view)
        manuscript = navigation['canonical']['manuscript']
        for source, doc in navigation['documents'].items():
            relative = document_contents(doc['view'])
            reader_href = relative_href(relative, doc['view'])
            roots = section_outline(doc['anchors'])

            def contents(nodes):
                return '<ol>' + ''.join(
                    '<li><p class="guavajam-contents-entry guavajam-scrollline" tabindex="0" aria-label="Contents entry: ' + escape(node['record']['title'], quote=True) + '">'
                    '<a class="guavajam-contents-link" href="' + escape(reader_href, quote=True) + '#' + escape(node['record']['anchor'], quote=True) +
                    '" aria-label="Go to section ' + escape(node['record']['title'], quote=True) + '">' + link_icon('link', 'Go to section') + '</a>' +
                    escape(node['record']['title']) + '</p>' +
                    (contents(node['children']) if node['children'] else '') + '</li>'
                    for node in nodes) + '</ol>'

            ancestors = ((( '../../../../../index.html', 'GuavaJam'),) if collection else ()) + (
                ('../../../index.html', view['facts']['title']), ('index.html', 'v' + str(position)))
            if source != manuscript:
                ancestors += ((doc['view'], doc['title']),)
            ancestors = tuple((relative_href(relative, href), label) for href, label in ancestors)
            kind = 'Manuscript' if source == manuscript else 'Document'
            body = '<h2>Table of Contents</h2>' + ('<nav class="guavajam-contents-directory" aria-label="' + kind + ' outline">'
                    '<div class="guavajam-contents-list">' + contents(roots) + '</div></nav>' if roots
                    else '<p>No individually titled sections were declared in this ' + kind.lower() + '.</p>')
            html = page(doc['title'] + ' — Contents', body,
                        ancestors=ancestors, breadcrumb_current='Contents', site_origin=site_origin)
            target = read / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(html, encoding='utf-8')
            view['files'][relative] = hashlib.sha256(html.encode('utf-8')).hexdigest()
        view_path.write_bytes(encoded(view))


def build_directories(output, editions, *, collection, search_assets, databases, site_origin=None):
    """Select the physical artifact for each directory, never all-history SQL filtering."""
    output = Path(output)
    interactive = databases is not None and (bool(search_assets) or (output / 'search.js').is_file())
    runtime = '../../' if search_assets else ''

    def download(database):
        descriptor = emit_download(database)
        parent = database.parent.relative_to(output).as_posix()
        if parent != '.':
            descriptor['url'] = parent + '/' + descriptor['url']
            if descriptor.get('facets'):
                descriptor['facets']['url'] = parent + '/' + descriptor['facets']['url']
        return descriptor

    downloads = {scope: {sha: download(path) for sha, path in value.items()}
                 if isinstance(value, dict) else download(value)
                 for scope, value in (databases or {}).items()}
    if databases is not None:
        (output / 'SEARCH.json').write_bytes(encoded({
            'schema': 'guavajam.search-routing.v1',
            'versions': downloads['versions'], 'history': downloads['history']}))
        for revision, _, _, _ in editions:
            (output / 'editions' / revision / 'search' / 'SEARCH.json').write_bytes(encoded({
                'schema': 'guavajam.search-routing.v1',
                **{scope: downloads[scope][revision] for scope in ['documents', 'passages']}}))
    title = (json.loads((output / 'editions' / editions[-1][0] / 'BIBLIOGRAPHY.json').read_text())['facts']['title']
             if editions else 'Unpublished work')
    _write_work(output, editions, title, collection=collection, runtime=runtime,
                download=downloads['versions'] if interactive else None, site_origin=site_origin)
    _write_documents(output, editions, collection=collection, runtime=runtime,
                     downloads=downloads['documents'] if interactive else {}, site_origin=site_origin)
    _write_contents(output, editions, collection=collection, site_origin=site_origin)
    from .citation_pages import build_citation_pages
    build_citation_pages(output, editions, collection=collection, runtime=runtime,
                         downloads=downloads['passages'] if interactive else {}, site_origin=site_origin)
