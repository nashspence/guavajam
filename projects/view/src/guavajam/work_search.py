"""Optional work discovery; reading and source identities remain script-free."""
from __future__ import annotations

from contextlib import closing
import hashlib
from html import escape
import json
from pathlib import Path
import sqlite3

from guavajam_core.catalog import CatalogError, encoded
from .navigation import document_directory
from .search_index import create_index, emit_browser_assets


def publication_navigation(reader, view, revision):
    """Decorate site-owned copies without mutating the reusable render cache."""
    paths = [details['view'] for details in view['documents'].values()]
    paths.append('documents.html')
    for relative in paths:
        path = reader / relative
        prefix = '../' if relative.startswith('supporting/') else ''
        navigation = ('<nav class="guavajam-document-nav" aria-label="Publication">'
                      '<a href="' + prefix + '../../../index.html">Work</a> · '
                      '<a href="' + prefix + '../index.html">Edition</a> · '
                      '<a href="' + prefix + '../../../browse/index.html?edition=' + revision +
                      '">Browse this edition</a></nav>\n')
        text = path.read_text('utf-8')
        if text.count('<main ') != 1:
            raise CatalogError('work navigation: expected one generated reading landmark')
        text = text.replace('<main ', navigation + '<main ', 1)
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
    from .site import edition_title, page
    with closing(sqlite3.connect(output / 'work.sqlite')) as db:
        title, latest = db.execute('SELECT title,latest_revision FROM works').fetchone()
    body = ('<nav aria-label="Breadcrumb"><a href="../index.html">' + escape(title) + '</a></nav>'
            '<p>Explore editions, documents, and explicitly anchored sections or passages in this work.</p>')
    if interactive:
        size = (output / 'work.sqlite').stat().st_size
        body += ('<section class="guavajam-index-search" aria-labelledby="guavajam-search-heading">'
                 '<h2 id="guavajam-search-heading">Search this work</h2>'
                 '<p id="guavajam-search-scope">Search starts with the latest edition in verified publication '
                 'order. Choose all editions to include history. Dates are publisher-supplied Git dates. '
                 'Quoted headings remain identified as quotations.</p>'
                 '<p>Enable search to download this work’s local index (' + str((size + 1023) // 1024) +
                 ' KiB) and search engine. Reading links below work without it.</p>'
                 '<button type="button" id="guavajam-search-enable">Enable search</button>'
                 '<form id="guavajam-search" role="search" aria-describedby="guavajam-search-scope" '
                 'data-scope="work" data-root="../" data-database="../work.sqlite" data-worker="' +
                 escape(assets + 'search-worker.js', quote=True) + '">'
                 '<div><label for="guavajam-query">Text search</label>'
                 '<input id="guavajam-query" name="q" type="search" maxlength="256" autocomplete="off"></div>'
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
                 '<button type="submit">Search</button></form>'
                 '<p id="guavajam-search-status" role="status" aria-live="polite">Search has not been enabled.</p>'
                 '<ol id="guavajam-search-results" class="guavajam-works" hidden></ol>'
                 '<nav aria-label="Search result pages">'
                 '<button type="button" id="guavajam-search-prev" disabled>Previous</button>'
                 '<span id="guavajam-search-page"></span>'
                 '<button type="button" id="guavajam-search-next" disabled>Next</button>'
                 '</nav></section>')
    body += '<section id="guavajam-static-section"><h2>Browse this work</h2>'
    if latest:
        navigation = json.loads((output / 'editions' / latest / 'read/NAV.json').read_text('utf-8'))
        body += ('<h3>Latest edition documents</h3>' +
                 document_directory(navigation, prefix='../editions/' + latest + '/read/') +
                 '<h3>All editions</h3><ol id="guavajam-static-list" reversed>')
        for edition in reversed(catalog['editions']):
            prefix = '../editions/' + edition['revision'] + '/'
            body += ('<li><a href="' + prefix + 'index.html">' + escape(edition_title(edition)) +
                     '</a> · <a href="' + prefix + 'read/documents.html">All documents and contents</a>'
                     '<time datetime="' + edition['published'] + '">' + edition['published'] + '</time>'
                     '<code>' + edition['revision'][:12] + '</code></li>')
        body += '</ol>'
    else:
        body += '<p>No signed editions have been published on the selected history.</p>'
    body += '</section><p><a href="../work.sqlite" download>Download work index</a></p>'
    if interactive:
        body += '<script src="' + escape(assets + 'search.js', quote=True) + '" defer></script>'
    return page('Browse ' + title, body, search=interactive)
