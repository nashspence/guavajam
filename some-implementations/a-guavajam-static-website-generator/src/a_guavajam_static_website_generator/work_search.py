"""Optional work discovery; reading and source identities remain script-free."""
from __future__ import annotations

import hashlib
from html import escape
import json
import re
from pathlib import Path

from guavajam.catalog import CatalogError, encoded
from .search_index import create_index, emit_browser_assets


def publication_navigation(reader, view, revision, *, work_title, edition_number,
                           published="", in_collection=False):
    """Site-owned readers get an unobtrusive breadcrumb and exact Git citation.

    No source content or reusable render-cache copy is changed. Generated
    reading pages remain fully script-free.
    """
    from .site import citation_control, edition_label
    from .citation_pages import add_passage_links
    from .citation_routes import relative_href, source_offsets
    from guavajam.bibliography import ordered_authors, author_text
    authors = author_text(ordered_authors(view["facts"]), identifiers=False)
    source_navigation = json.loads((reader / 'NAV.json').read_text('utf-8'))
    starts = source_offsets(source_navigation)
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
        parts.append('<li><a href="' + root + 'index.html">' + escape(work_title) + '</a></li>')
        if relative == 'index.html':
            # The manuscript's bibliographic identity appears in its citation.
            parts.append('<li><span aria-current="page">' +
                         escape(edition_label(edition_number)) + '</span></li>')
        else:
            parts.append('<li><a href="' + prefix + 'index.html">' +
                         escape(edition_label(edition_number)) + '</a></li>')
            current = ('Documents' if relative == 'documents.html' else
                       view['documents'][destinations[relative]]['title'])
            parts.append('<li>' + ('<h1 aria-current="page">' if relative == 'documents.html'
                                   else '<span aria-current="page">') + escape(current) +
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
        # Breadcrumb labels describe the reader surface, not manuscript metadata.
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
        if relative == 'index.html':
            from .citation import manuscript_citation
            text = text.replace(manuscript_citation(revision, view['facts']), '', 1)
            # The native TOC moves to Contents on the site-owned reader only.
            # The standalone renderer and its immutable cache keep their own TOC.
            text = re.sub(r'<nav\b[^>]*class="guavajam-contents"[^>]*>.*?</nav>',
                          '', text, count=1, flags=re.S)
        citation_html = citation_control(
            cited, cited_href, title=work_title, authors=authors, published=published,
            edition_number=edition_number, document_title=document_title,
            heading=relative == 'index.html')
        menu = ''
        if relative != 'documents.html':
            # The renderer's utility directory is useful standalone but
            # redundant with the version menu in site-owned readers.
            from .navigation import reader_navigation
            text = text.replace(reader_navigation(source), '', 1)
            text = add_passage_links(text, relative, source,
                                     source_navigation['documents'][source]['anchors'], starts[source])
            if relative == 'index.html':
                # One compact route menu, directly above the manuscript; no
                # second edition cover, repeated abstract, or extra citation.
                menu = ('<nav class="guavajam-actions guavajam-version-menu" '
                        'aria-label="Version links" tabindex="0">'
                        '<a href="contents.html">Contents</a>'
                        '<a href="documents.html">Documents</a>'
                        '<a href="passages.html">Passages</a>'
                        '<a href="../metadata.html">Metadata</a>'
                        '<a href="source.gfm" download>Download</a></nav>')
        # Keep the document's title/citation within its article, with the route
        # menu still immediately above it. Supporting sources already own H1.
        text, replaced = re.subn(r'<article class="guavajam-edition"[^>]*>',
                                lambda match: menu + match.group(0) + citation_html,
                                text, count=1)
        if replaced != 1:
            raise CatalogError('work navigation: expected one generated document article')
        text = text.replace('<div class="guavajam-source-identity">',
                            '<div class="guavajam-source-identity" hidden>', 1)
        # Replace renderer-only rights/identity boilerplate in the published
        # site-owned copy. The signed rights statement has a License page.
        from .site import generator_footer
        text, replaced = re.subn(r'<footer>.*?</footer>', generator_footer(), text, count=1, flags=re.S)
        if replaced != 1:
            raise CatalogError('work navigation: expected one renderer footer')
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
        emit_browser_assets(output)
    else:
        if search_assets and (not isinstance(search_assets, str)
                              or search_assets not in {'../../'}):
            raise CatalogError('work search: unsupported shared runtime location')
        # The work index is disposable and shared with the directory pages.
    return database
