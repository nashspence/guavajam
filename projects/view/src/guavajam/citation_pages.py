"""One bounded Passages index per signed version, across all its documents.

NAV determines source order; Core verifies canonical source citations. This
index remains traversable through ordinary HTML links without JavaScript.
"""
from __future__ import annotations

import hashlib
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath

from guavajam_core.bibliography import author_text, ordered_authors
from guavajam_core.catalog import CatalogError, encoded
from guavajam_core.publication_commit import citation

from .citation_routes import (citation_fragment, citation_href, citation_page,
                              passage_json, relative_href)
from .pagination import PAGE_SIZE, _slices, _static_pager
from .site import citation_control, page


def add_passage_links(html, view, source, anchors, start):
    """Add a link only beside verified source permalinks, never renderer IDs.

    Preserve every other source-derived HTML byte and the existing cache.
    """
    # Reader Cite links target only the central index; canonical identity
    # remains (revision, source, anchor), not a presentation URL.
    routes = {record['anchor']: relative_href(
        view, citation_href(source, start + record['position'], record['anchor']))
        for record in anchors}
    offsets = [0, *(index + 1 for index, char in enumerate(html) if char == '\n')]
    edits, stack, found = [], [], set()
    void = set('area base br col embed hr img input link meta param source track wbr'.split())

    class Passages(HTMLParser):
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            target = attrs.get('href', '')
            anchor = target[1:] if target.startswith('#') else None
            cited = (tag == 'a' and 'guavajam-permalink' in attrs.get('class', '').split()
                     and anchor in routes)
            if tag not in void:
                stack.append((tag, anchor if cited else None))

        def handle_endtag(self, tag):
            if tag in void:
                return
            if not stack or stack[-1][0] != tag:
                raise CatalogError('citations: unbalanced generated reading HTML')
            _, anchor = stack.pop()
            if anchor is not None:
                if anchor in found:
                    raise CatalogError('citations: duplicate generated source permalink')
                found.add(anchor)
                line, column = self.getpos()
                end = html.index('>', offsets[line - 1] + column) + 1
                edits.append((end, '<a class="guavajam-passage-cite" lang="en" href="' +
                              escape(routes[anchor], quote=True) +
                              '" aria-label="Cite committed source passage ' + escape(anchor, quote=True) +
                              '">Cite</a>'))

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)
            if tag not in void:
                self.handle_endtag(tag)

    parser = Passages(convert_charrefs=True)
    parser.feed(html)
    parser.close()
    if stack or found != set(routes):
        raise CatalogError('citations: generated reading HTML lost a declared source permalink')
    parts, previous = [], 0
    for offset, extra in edits:
        parts.extend((html[previous:offset], extra))
        previous = offset
    return ''.join(parts) + html[previous:]


def build_citation_pages(output, editions, *, collection, runtime, download):
    """Emit one bounded human citation listing per version, plus exact JSON records.

    No parallel per-document citation HTML is generated. Both static and SQLite
    links use the same source-order offsets and one set of citation identities.
    """
    from .directory_pages import _sqlite_controls, search_listing_open, search_listing_close

    for position, (revision, _, published, _) in enumerate(editions, 1):
        read = Path(output) / 'editions' / revision / 'read'
        view_path = read / 'VIEW.json'
        view = json.loads(view_path.read_text('utf-8'))
        navigation = json.loads((read / 'NAV.json').read_text('utf-8'))
        proof = json.loads((read.parent / 'SOURCE.json').read_text('utf-8'))
        # Core verifies source membership; do not substitute HTML IDs as proof.
        proof['documents'] = {source: frozenset(anchors) for source, anchors in proof['documents'].items()}
        title = view['facts']['title']
        authors = author_text(ordered_authors(view['facts']), identifiers=False)

        def write(relative, raw):
            target = read / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            view['files'][relative] = hashlib.sha256(raw).hexdigest()

        records = [(source, navigation['documents'][source], anchor)
                   for source in sorted(navigation['documents'])
                   for anchor in navigation['documents'][source]['anchors']]
        chunks = _slices(records)
        for number, chunk in enumerate(chunks, 1):
            filename = citation_page((number - 1) * PAGE_SIZE)

            def relative(target):
                return relative_href(filename, target)

            root = relative('../../../') + '/'
            ancestors = (((relative('../../../../../index.html'), 'GuavaJam'),) if collection else ()) + (
                (relative('../../../index.html'), title),
                (relative('index.html'), 'v' + str(position)),)
            body = ''
            if download is not None:
                body += _sqlite_controls(kind='citations', edition=revision,
                                         root=root, runtime=runtime, download=download)
            body += search_listing_open('Passages', number, len(chunks), interactive=download is not None)
            body += '<section id="guavajam-static-section" class="guavajam-directory">'
            pager = _static_pager(number, len(chunks), first_href=relative(citation_page()),
                                  page_prefix=relative('passages/page') + '/')
            if chunk:
                body += pager + '<ol class="guavajam-citation-list">'
                for source, document, record in chunk:
                    anchor = record['anchor']
                    reader = document['view']
                    document_title = document['title'] if source != proof['manuscript'] else ''
                    cited = citation(proof, anchor=anchor, document=source)
                    json_file = passage_json(reader, anchor)
                    write(json_file, encoded(cited))
                    body += ('<li class="guavajam-citation-entry" id="' +
                             citation_fragment(source, anchor) + '" tabindex="-1">'
                             '<h3>' + escape(record['title'][:240] or '#' + anchor) + '</h3>'
                             '<p class="guavajam-meta">' +
                             ('Supporting document · ' if document_title else 'Manuscript · ') +
                             escape(source) + ' · <code>#' + escape(anchor) + '</code>' +
                             (' · Quoted passage' if record['scope'].startswith('quotation:') else '') + '</p>' +
                             citation_control(cited, relative(json_file), title=title, authors=authors,
                                              published=published, edition_number=position,
                                              document_title=document_title) +
                             '<p><a href="' + escape(relative(reader) + '#' + anchor, quote=True) +
                             '">Read this passage</a></p></li>')
                body += '</ol>' + pager
            else:
                body += '<p>No individually citable passages were declared.</p>'
            body += '</section>' + search_listing_close(interactive=download is not None)
            html = page(title + ' — Passages' + (f' — page {number}' if number > 1 else ''),
                        body, search=download is not None, ancestors=ancestors,
                        breadcrumb_current='Passages')
            write(filename, html.encode('utf-8'))
        view_path.write_bytes(encoded(view))
