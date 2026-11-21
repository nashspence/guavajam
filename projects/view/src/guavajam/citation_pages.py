"""Bounded source citation pages and script-free reader links.

NAV supplies verified source order; Core supplies canonical citation identity.
The optional search form uses the same work SQLite index as every directory.
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
                              passage_json, relative_href, source_download)
from .pagination import PAGE_SIZE, _slices, _static_pager
from .site import citation_control, page


def add_passage_links(html, view, anchors):
    """Append Cite only to the renderer's explicit source permalinks.

    Preserve the original HTML byte for byte around each insertion, including
    quotations, footnotes and source anchors. Joining the edits once keeps this
    linear even for readers with many passages.
    """
    routes = {record['anchor']: relative_href(
        view, citation_href(view, record['position'], record['anchor'])) for record in anchors}
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
    """Write 20 source-order entries per page, with bounded native page links.

    The work index has already validated NAV against the signed view inventory.
    Each canonical JSON is additionally checked against Core's verified proof.
    No full passage list or alternate client-side index is embedded in HTML.
    """
    from .directory_pages import _sqlite_controls

    for position, (revision, _, published, _) in enumerate(editions, 1):
        read = Path(output) / 'editions' / revision / 'read'
        view_path = read / 'VIEW.json'
        view = json.loads(view_path.read_text('utf-8'))
        navigation = json.loads((read / 'NAV.json').read_text('utf-8'))
        proof = json.loads((read.parent / 'SOURCE.json').read_text('utf-8'))
        # Core validates membership for each canonical record. Keep that check
        # constant-time for long documents; this local proof is never serialized.
        proof['documents'] = {source: frozenset(anchors) for source, anchors in proof['documents'].items()}
        title = view['facts']['title']
        authors = author_text(ordered_authors(view['facts']), identifiers=False)

        def write(relative, raw):
            target = read / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            view['files'][relative] = hashlib.sha256(raw).hexdigest()

        for source, document in navigation['documents'].items():
            reader = document['view']
            canonical = citation(proof, document=source)
            source_file = source_download(reader)
            canonical_file = str(PurePosixPath(source_file).with_suffix('.citation.json'))
            document_title = document['title'] if source != proof['manuscript'] else ''
            chunks = _slices(document['anchors'])
            for number, chunk in enumerate(chunks, 1):
                filename = citation_page(reader, (number - 1) * PAGE_SIZE)

                def relative(target):
                    return relative_href(filename, target)

                root = relative('../../../') + '/'
                ancestors = (((relative('../../../../../index.html'), 'GuavaJam'),) if collection else ()) + (
                    (relative('../../../index.html'), title),
                    (relative('../index.html'), f'Edition {position}'),
                    (relative(reader), 'Manuscript' if not document_title else 'Supporting document'))
                body = ('<p>Use the committed source identity below when citing this document or a passage.</p>'
                        '<p class="guavajam-actions"><a href="' + escape(relative(reader), quote=True) +
                        '">Return to reading</a><a href="' + escape(relative(source_file), quote=True) +
                        '" download>Download exact GFM source</a><a href="' +
                        escape(relative(canonical_file), quote=True) +
                        '" download>Download citation JSON</a></p><h2>Committed passages</h2>')
                if download is not None:
                    body += _sqlite_controls(kind='citations', edition=revision, document=source,
                                             root=root, runtime=runtime, download=download)
                body += '<section id="guavajam-static-section" class="guavajam-directory">'
                pager = _static_pager(number, len(chunks), first_href=relative(citation_page(reader)),
                                      page_prefix=relative(str(PurePosixPath(citation_page(reader)).with_suffix('') / 'page')) + '/')
                if chunk:
                    body += ('<p class="guavajam-meta">Passages ' + str((number - 1) * PAGE_SIZE + 1) +
                             '–' + str((number - 1) * PAGE_SIZE + len(chunk)) + ' of ' +
                             str(len(document['anchors'])) + '</p>' + pager +
                             '<ol class="guavajam-citation-list">')
                    for record in chunk:
                        anchor = record['anchor']
                        cited = citation(proof, anchor=anchor, document=source)
                        json_file = passage_json(reader, anchor)
                        write(json_file, encoded(cited))
                        body += ('<li class="guavajam-citation-entry" id="' + citation_fragment(anchor) +
                                 '" tabindex="-1"><h3>Passage <code>#' + escape(anchor) + '</code></h3>'
                                 '<p class="guavajam-meta">' +
                                 ('Quoted passage · ' if record['scope'].startswith('quotation:') else '') +
                                 escape(record['title'][:240]) + '</p>' +
                                 citation_control(cited, relative(json_file), title=title, authors=authors,
                                                  published=published, edition_number=position,
                                                  document_title=document_title) +
                                 '<p><a href="' + escape(relative(reader) + '#' + anchor, quote=True) +
                                 '">Read this passage</a></p></li>')
                    body += '</ol>' + pager
                else:
                    body += '<p>No passage anchors were declared. Cite this document as a whole.</p>'
                body += '</section>'
                html = page(document['title'] + ' — Citations' + (f' — page {number}' if number > 1 else ''),
                            body, search=download is not None, ancestors=ancestors,
                            breadcrumb_current='Citations', citation_record=canonical,
                            citation_href=relative(canonical_file), citation_title=title,
                            citation_authors=authors, citation_published=published,
                            citation_edition=position, citation_document_title=document_title)
                write(filename, html.encode('utf-8'))
        view_path.write_bytes(encoded(view))
