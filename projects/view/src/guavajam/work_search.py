"""Optional work discovery; reading and source identities remain script-free."""
from __future__ import annotations

from contextlib import closing
import hashlib
from html import escape
import json
from pathlib import Path
import sqlite3

from guavajam_core.catalog import CatalogError, encoded
from .search_artifact import download_attributes, emit_download
from .search_index import create_index, emit_browser_assets


def publication_navigation(reader, view, revision, *, work_title, edition_number,
                           published="", in_collection=False):
    """Site-owned readers get an unobtrusive breadcrumb and exact Git citation.

    No source content or reusable render-cache copy is changed. Generated
    reading pages remain fully script-free.
    """
    from .site import citation_control, edition_label
    from guavajam_core.bibliography import ordered_authors, author_text
    authors = author_text(ordered_authors(view["facts"]), identifiers=False)
    paths = [details['view'] for details in view['documents'].values()]
    paths.append('documents.html')
    destinations = {details['view']: source for source, details in view['documents'].items()}
    additions = (Path(__file__).parent / 'styles/catalog.css').read_text('utf-8')
    for relative in paths:
        path = reader / relative
        prefix = '../' if relative.startswith('supporting/') else ''
        root = prefix + '../../../'
        parts = []
        if in_collection:
            parts.append('<li><a href="' + root + '../../index.html">GuavaJam</a></li>')
        parts.extend(('<li><a href="' + root + 'index.html">' + escape(work_title) + '</a></li>',
                      '<li><a href="' + prefix + '../index.html">' +
                      escape(edition_label(edition_number)) + '</a></li>'))
        current = ('Documents' if relative == 'documents.html' else
                   'Manuscript' if relative == 'index.html' else 'Supporting document')
        parts.append('<li>' + ('<h1 aria-current="page">' if relative == 'documents.html'
                               else '<span aria-current="page">') + current +
                     ('</h1>' if relative == 'documents.html' else '</span>') + '</li>')
        navigation = ('<nav class="guavajam-navigation" aria-label="Breadcrumb" tabindex="0"><ol>' +
                      ''.join(parts) + '</ol></nav>\n')
        if relative == 'documents.html':
            cited = json.loads((reader.parent / 'CITATION.json').read_text('utf-8'))
            cited_href = prefix + '../CITATION.json'
            document_title = ''
        else:
            source = destinations[relative]
            document_title = view['documents'][source]['title'] if source != 'content/manuscript.md' else ''
            if source == 'content/manuscript.md':
                cited_href = prefix + 'source.citation.json'
            else:
                cited_href = prefix + 'supporting/' + Path(relative).stem + '.citation.json' if not prefix else Path(relative).stem + '.citation.json'
            cited = json.loads((path.parent / Path(cited_href).name).read_text('utf-8'))
        text = path.read_text('utf-8')
        if text.count('<main ') != 1 or text.count('</style>') != 1:
            raise CatalogError('work navigation: expected one generated reading landmark and stylesheet')
        # The reader already has a document heading from the signed GFM.
        # Breadcrumb labels therefore describe the reader surface, not repeat
        # the source heading as a second visible page title.
        text = text.replace('</style>', '\n' + additions + '</style>', 1)
        text = text.replace('<header class="guavajam-status"><span>Verified signed source</span></header>', '', 1)
        text = text.replace('<main ', navigation + '<main ', 1)
        # Only the site-owned copy changes: the standalone renderer and cache
        # retain the original generated directory heading.
        if relative == 'documents.html':
            # The site builder replaces this intermediate, signed-source-derived
            # directory with bounded static pages after creating the SQLite
            # search artifact. Standalone renders remain unchanged.
            text = text.replace('<h1>Documents in this edition</h1>', '', 1)
        citation_html = citation_control(
            cited, cited_href, title=work_title, authors=authors, published=published,
            edition_number=edition_number, document_title=document_title)
        text = text.replace('<article class="guavajam-edition"',
                            citation_html + '<article class="guavajam-edition"', 1)
        text = text.replace('<div class="guavajam-source-identity">',
                            '<div class="guavajam-source-identity" hidden>', 1)
        text = text.replace('<footer>', '<footer><p>Verified signed source</p>', 1)
        path.write_text(text, encoding='utf-8')
        view['files'][relative] = hashlib.sha256(text.encode('utf-8')).hexdigest()
    (reader / 'VIEW.json').write_bytes(encoded(view))


def build_work_index(output, catalog, *, repository='', search_assets=None):
    """Share an index model and, in a collection, one copy of the WASM runtime.

    None discovers/copies local runtime assets; an empty string retains static
    navigation only. A relative prefix selects a collection's shared assets.
    Browser loading remains opt-in even when runtime files are available.
    """
    database = create_index(output, [{'repository': repository, 'path': '', 'tip': catalog['tip']}],
                            filename='work.sqlite', scope='work')
    if search_assets is None:
        interactive = emit_browser_assets(output)
        asset_prefix = '../'
    else:
        if search_assets and (not isinstance(search_assets, str)
                              or search_assets not in {'../../'}):
            raise CatalogError('work search: unsupported shared runtime location')
        interactive = bool(search_assets)
        asset_prefix = '../' + search_assets
    directory = output / 'browse'
    directory.mkdir()
    (directory / 'index.html').write_text(
        browse_page(output, catalog, interactive=interactive, assets=asset_prefix), encoding='utf-8')
    return database


def browse_page(output, catalog, *, interactive, assets):
    """Browse within the same editorial hierarchy, with full static fallback."""
    from .site import page
    with closing(sqlite3.connect(output / 'work.sqlite')) as db:
        title, latest = db.execute('SELECT title,latest_revision FROM works').fetchone()
    body = '<p>Read documents, compare signed editions, or find a specific passage.</p>'
    if interactive:
        download = emit_download(output / 'work.sqlite')
        body += (
            '<section class="guavajam-index-search" aria-labelledby="guavajam-search-heading">'
            '<div class="guavajam-search-intro"><h2 id="guavajam-search-heading">Find in this work</h2>'
            '<button type="button" id="guavajam-search-enable">Enable search</button></div>'
            '<p class="guavajam-search-help" id="guavajam-search-scope">Local search starts with '
            'the latest verified edition; include history using the edition filter. '
            'Downloads ' + str((download['transfer_size'] + 1023) // 1024) +
            ' KiB only when enabled.</p>'
            '<form id="guavajam-search" role="search" aria-describedby="guavajam-search-scope" '
            'data-scope="work" data-root="../" ' + download_attributes(download, prefix='../') +
            ' data-worker="' + escape(assets + 'search-worker.js', quote=True) + '">'
            '<div class="guavajam-query-field"><label for="guavajam-query">Search text</label>'
            '<input id="guavajam-query" name="q" type="search" maxlength="256" autocomplete="off"></div>'
            '<button type="submit">Search</button>'
            '<details class="guavajam-search-filters"><summary>Browse and filter</summary>'
            '<div class="guavajam-filter-fields">'
            '<div><label for="guavajam-kind">Show</label><select id="guavajam-kind" name="kind">'
            '<option value="documents">Documents</option><option value="editions">Editions</option>'
            '<option value="passages">Sections and passages</option></select></div>'
            '<div><label for="guavajam-edition">Edition</label><select id="guavajam-edition" name="edition">'
            '<option value="latest">Latest edition</option><option value="all">All editions</option></select></div>'
            '<div><label for="guavajam-document">Document</label>'
            '<select id="guavajam-document" name="document"><option value="">All documents</option></select></div>'
            '<div><label for="guavajam-sort">Sort</label><select id="guavajam-sort" name="sort">'
            '<option value="publication">Publication order (newest)</option>'
            '<option value="oldest">Publication order (oldest)</option>'
            '<option value="title">Title</option><option value="relevance">Relevance</option></select></div>'
            '</div></details></form></section>')
        body += ('<p class="guavajam-meta" id="guavajam-search-status" role="status" '
                 'aria-live="polite">Showing the static directory.</p>'
                 '<ol id="guavajam-search-results" class="guavajam-works" hidden></ol>'
                 '<nav class="guavajam-pagination" aria-label="Search result pages" hidden>'
                 '<button type="button" id="guavajam-search-prev" disabled>Previous</button>'
                 '<span id="guavajam-search-page"></span>'
                 '<button type="button" id="guavajam-search-next" disabled>Next</button></nav>')
    body += '<nav id="guavajam-static-section" class="guavajam-directory" aria-label="Static directories">'
    if latest:
        body += '<a href="../editions/' + latest + '/read/documents.html">Documents</a>'
        if len(catalog['editions']) > 1:
            body += ' · <a href="../editions/index.html">Previous editions</a>'
    else:
        body += '<p>No signed editions have been published on the selected history.</p>'
    body += '</nav>'
    if interactive:
        body += '<script src="' + escape(assets + 'search.js', quote=True) + '" defer></script>'
    # Search spans editions and has no singular Git-commit citation.
    return page('Search ' + title, body, search=interactive,
                ancestors=(('../index.html', title),), breadcrumb_current='Search')
