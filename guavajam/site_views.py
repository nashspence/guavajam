"""Static bibliographic surfaces and source-citation affordances.

Only validated catalog values and facts extracted by the signed-source renderer
are accepted here. No input is interpreted as a template, stylesheet or raw HTML.
"""
from __future__ import annotations

from html import escape
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
import re
import posixpath
from urllib.parse import quote

from .bibliography import ordered_authors, author_text
from .catalog import doi_url, source_url


def e(value):
    return escape(str(value), quote=True)


def relative(page, destination):
    return posixpath.relpath(destination, posixpath.dirname(page) or '.')


def absolute(catalog, page):
    route = page[:-10] if page.endswith('index.html') else page
    return catalog['base_url'] + '/' + route


def edition_root(slug, sha):
    return 'works/' + slug + '/editions/' + sha


def metadata(facts, publisher, *, identifier=None, published=None, canonical=None):
    entries = [('citation_title', facts['title']), ('DC.title', facts['title']),
               ('DC.publisher', publisher), ('DC.language', facts['language'])]
    for author in ordered_authors(facts):
        entries.extend([('citation_author', author['name']), ('DC.creator', author['name'])])
    if 'abstract' in facts:
        entries.append(('description', facts['abstract']))
    if 'rights' in facts:
        entries.append(('DC.rights', facts['rights']))
    if identifier:
        entries.extend([('citation_doi', identifier), ('DC.identifier', doi_url(identifier))])
    if published:
        entries.extend([('citation_publication_date', published), ('DC.date', published)])
    if canonical:
        entries.extend([('guavajam:git-algorithm', 'sha1'),
                        ('guavajam:git-revision', canonical['revision']),
                        ('guavajam:source-path', canonical.get('document', canonical['manuscript']))])
    return '\n'.join('<meta name="' + e(key) + '" content="' + e(value) + '">' for key, value in entries)


def navigation(catalog, filename, *, work=None, edition=None, work_title=None):
    """A short, source-aware breadcrumb; only ancestors are links.

    The catalog and signed-source bibliography supply labels. The page URL is
    a navigation location only, never the publication's canonical identity.
    """
    root = 'index.html'
    crumbs = [(relative(filename, root), 'Publications')] if filename != root else [(None, catalog['title'])]
    if work is not None:
        work_page = 'works/' + work['slug'] + '/index.html'
        crumbs.append((relative(filename, work_page) if filename != work_page else None,
                       work_title or work['slug']))
    if edition is not None:
        record = edition_root(work['slug'], edition['revision']) + '/index.html'
        label = edition.get('label', 'Revision ' + edition['revision'][:12])
        crumbs.append((relative(filename, record) if filename != record else None, label))
        if filename != record:
            supporting = '/read/supporting/' in filename
            if filename.endswith('/cite.html') or filename.endswith('.cite.html'):
                read = (filename[:-len('cite.html')] + 'index.html' if filename.endswith('/cite.html')
                        else filename[:-len('.cite.html')] + '.html')
                crumbs.append((relative(filename, read), 'Supporting source' if supporting else 'Reading'))
                crumbs.append((None, 'Citations'))
            else:
                crumbs.append((None, 'Supporting source' if supporting else 'Reading'))
    if len(crumbs) > 5 or crumbs[-1][0] is not None:
        raise ValueError('generated breadcrumb has no current page')
    def entry(href, label, current):
        marker = '<span class="guavajam-crumb-separator" aria-hidden="true">/</span>' if current else ''
        text = ('<span aria-current="page">' + e(label) + '</span>' if href is None
                else '<a href="' + e(href) + '">' + e(label) + '</a>')
        return '<li>' + marker + text + '</li>'
    return ('<nav class="guavajam-navigation" lang="en" aria-label="Breadcrumb"><ol>' +
            ''.join(entry(href, label, index > 0) for index, (href, label) in enumerate(crumbs)) +
            '</ol></nav>')


def stylesheet():
    root = Path(__file__).parent / 'styles'
    return (root / 'reading.css').read_text('utf-8') + '\n' + (root / 'catalog.css').read_text('utf-8')


def page(catalog, filename, title, body, *, head='', work=None, edition=None, work_title=None, indexable=True):
    feed = ('<link rel="alternate" type="application/atom+xml" title="Publication updates" href="' +
            e(relative(filename, 'feed.xml')) + '">') if catalog['works'] else ''
    csp = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; script-src 'none'; base-uri 'none'; form-action 'none'; object-src 'none'"
    return ('<!doctype html>\n<html lang="' + e(catalog['language']) + '"><head>\n'
            '<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">\n'
            '<meta http-equiv="Content-Security-Policy" content="' + e(csp) + '">\n'
            '<meta name="guavajam-viewer" content="guavajam.site.v1">\n'
            '<link rel="canonical" href="' + e(absolute(catalog, filename)) + '">\n' + feed + '\n' +
            ('' if indexable else '<meta name="robots" content="noindex">\n') + head + '\n'
            '<title>' + e(title) + ' — ' + e(catalog['publisher']) + '</title>\n'
            '<style>' + stylesheet() + '</style></head><body class="guavajam-site">\n'
            '<a class="press-skip" href="#guavajam-catalog-main">Skip to content</a>\n' +
            navigation(catalog, filename, work=work, edition=edition, work_title=work_title) +
            '<main id="guavajam-catalog-main" tabindex="-1">' + body + '</main>\n'
            '<footer><p>' + e(catalog['publisher']) +
            '. This catalog and its reading views are presentations. The independently trusted signed Git revision identifies the publication.</p>'
            '<p>DOI assignments, publication dates and editorial notices are reviewed catalog records, not claims made by the source signature or an independent timestamp service.</p>'
            '</footer></body></html>\n')


def authors(facts):
    return '<p class="press-edition-identity">' + e(author_text(ordered_authors(facts), identifiers=True)) + '</p>'


def doi_display(identifier, label):
    return '<p class="guavajam-identifier">' + e(label) + ' <a href="' + e(doi_url(identifier)) + '">' + e(doi_url(identifier)) + '</a></p>'


def abstract(facts):
    return '<p class="guavajam-abstract" lang="' + e(facts['language']) + '">' + e(facts['abstract']) + '</p>' if facts.get('abstract') else ''


def status(edition):
    return '<span class="guavajam-status">' + e(edition['status'].capitalize()) + '</span>'


def notices(edition):
    items = edition.get('notices', [])
    if not items:
        return ''
    return '<section class="guavajam-notices" aria-label="Editorial notices"><h2>Editorial notices</h2><ol>' + ''.join(
        '<li><time datetime="' + e(n['date']) + '">' + e(n['date']) + '</time> — ' + e(n['text']) +
        (' <a href="' + e(n['url']) + '">Notice details</a>' if 'url' in n else '') + '</li>' for n in items) + '</ol></section>'


def canonical_record(sha, manuscript, document=None, anchor=None):
    result = {'schema': 'press.citation.v1', 'hash_algorithm': 'sha1', 'revision': sha, 'manuscript': manuscript}
    if document and document != manuscript:
        result['document'] = document
    if anchor:
        result['anchor'] = anchor
    return result


def source_identity(canonical):
    return ('Git SHA-1 ' + canonical['revision'] + ' · ' + canonical.get('document', canonical['manuscript']) +
            (' · #' + canonical['anchor'] if 'anchor' in canonical else ''))


def citation(catalog, facts, edition, canonical):
    text = (author_text(ordered_authors(facts)) + '. ' + facts['title'] + '. ' + catalog['publisher'] + ', ' + edition['published'] + '.')
    # DOI remains the default bibliographic link when assigned. Commit and path
    # supply the additional independent source specificity; never cite a view URL.
    identifier = ' ' + doi_display(edition['doi'], 'Edition identifier') if edition.get('doi') else ''
    return ('<div class="guavajam-citation"><p>' + e(text) + '</p>' + identifier +
            '<p><strong>Exact committed source:</strong> <code>' + e(source_identity(canonical)) + '</code></p></div>')


def citation_data(catalog, facts, edition, canonical, anchors):
    return {'schema': 'guavajam.citation.v1', 'canonical': canonical, 'anchors': anchors,
            'bibliography': {'title': facts['title'], 'authors': ordered_authors(facts),
                             'publisher': catalog['publisher'], 'published': edition['published'],
                             **({'doi': edition['doi']} if 'doi' in edition else {})},
            'status': edition['status']}


def citation_filename(view):
    p = PurePosixPath(view)
    return str(p.with_name('cite.html' if p.name == 'index.html' else p.stem + '.cite.html'))


def source_filename(view):
    p = PurePosixPath(view)
    return str(p.with_name('source.gfm' if p.name == 'index.html' else p.stem + '.gfm'))


def _add_passage_links(html, anchors, href):
    """Decorate only declared source IDs, never generated heading/footnote IDs.

    A small citation link accompanies each existing local permalink. Do not
    add a new paragraph to every source block or rewrite its local fragment.
    """
    offsets = [0]
    offsets += [i + 1 for i, c in enumerate(html) if c == '\n']
    edits, stack, found = [], [], set()
    void = set('area base br col embed hr img input link meta param source track wbr'.split())

    class Passages(HTMLParser):
        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            target = values.get('href', '')
            ident = target[1:] if target.startswith('#') else None
            cite = tag == 'a' and 'press-permalink' in values.get('class', '').split() and ident in anchors
            if tag not in void:
                stack.append((tag, ident if cite else None))

        def handle_endtag(self, tag):
            if tag in void:
                return
            if not stack or stack[-1][0] != tag:
                raise ValueError('unbalanced generated reading HTML')
            _, ident = stack.pop()
            if ident:
                if ident in found:
                    raise ValueError('duplicate generated source permalink')
                found.add(ident)
                line, column = self.getpos()
                end = html.index('>', offsets[line - 1] + column) + 1
                edits.append((end, '<a class="guavajam-passage-cite" lang="en" href="' + e(href + '#' + quote(ident, safe='')) +
                              '" aria-label="Cite committed source passage ' + e(ident) + '">Cite</a>'))

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)
            if tag not in void:
                self.handle_endtag(tag)

    parser = Passages(convert_charrefs=True)
    parser.feed(html)
    parser.close()
    if stack or found != set(anchors):
        raise ValueError('generated reading HTML lost a declared source citation anchor')
    for position, extra in sorted(edits, reverse=True):
        html = html[:position] + extra + html[position:]
    return html


def decorate_reading(html, catalog, filename, work, edition, document, canonical, work_title=None):
    """Add breadcrumbs and citation guidance without changing citable source text.

    The renderer's generated source identity is moved from *before* the H1 to
    the bibliographic area following the title/byline. All source anchors,
    prose, quotations, media and their canonical citation targets stay intact.
    """
    cite = citation_filename(filename)
    html = _add_passage_links(html, document['anchors'], relative(filename, cite))
    head = '<link rel="canonical" href="' + e(absolute(catalog, filename)) + '">\n'
    if document['view'] == 'index.html':
        head += '<meta name="citation_publication_date" content="' + e(edition['published']) + '">\n'
        if edition.get('doi'):
            head += '<meta name="citation_doi" content="' + e(edition['doi']) + '">\n'
    else:
        # Supporting evidence is not itself the DOI-identified publication.
        if edition.get('doi'):
            head += '<meta name="DC.relation" content="' + e(doi_url(edition['doi'])) + '">\n'
    head += '<meta name="guavajam:git-revision" content="' + e(canonical['revision']) + '">\n'
    html = html.replace(' — GuavaJam</title>', ' — ' + e(catalog['publisher']) + '</title>', 1)
    html = html.replace('</head>', head + '</head>', 1)
    css = (Path(__file__).parent / 'styles/catalog.css').read_text('utf-8')
    html = html.replace('</style>', css + '</style>', 1)
    nav = navigation(catalog, filename, work=work, edition=edition, work_title=work_title)
    html = html.replace('</header>', '</header>' + nav, 1)
    marker = '<div class="press-source-identity">'
    if html.count(marker) != 1:
        raise ValueError('expected exactly one generated source identity block')
    start = html.index(marker)
    end = html.find('</div>', start)
    if end == -1:
        raise ValueError('generated source identity block is malformed')
    source_identity = html[start:end + len('</div>')]
    html = html[:start] + html[end + len('</div>'):]
    # The first H1 belongs to the source. The author byline, when present, is
    # renderer-generated immediately after it; group bibliographic provenance
    # beneath both rather than let the reader reach it before the publication.
    match = re.search(r'</h1>(?:\s*<p><span class="press-edition-identity">.*?</span></p>)?', html, re.S)
    if match is None:
        raise ValueError('generated reading has no H1 for its source identity')
    context = ('<aside class="guavajam-reading-context" lang="en" aria-label="Source and citation">'
               '<p>Cite the committed work or passage, not this presentation URL. '
               '<a href="' + e(relative(filename, cite)) + '">Source citations and download</a>.</p>'
               + source_identity + '</aside>')
    html = html[:match.end()] + context + html[match.end():]
    return html


def citation_page(catalog, filename, work, edition, facts, document, canonical, view_page):
    source_path = canonical.get('document', canonical['manuscript'])
    title = 'Cite committed source: ' + document['title']
    body = '<h1>' + e(title) + '</h1><p>The citation identifies the exact signed source. A reading URL is only an access path.</p>'
    body += citation(catalog, facts, edition, canonical)
    body += ('<p><a href="' + e(relative(filename, view_page)) + '">Return to reading</a> · '
             '<a download href="' + e(relative(filename, source_filename(view_page))) + '">Download exact GFM source</a> · '
             '<a download href="' + e(relative(filename, str(PurePosixPath(filename).with_suffix('.json')))) + '">Download citation data</a></p>')
    remote = source_url(catalog, edition['revision'], source_path)
    if remote:
        body += '<p><a href="' + e(remote) + '">Open the pinned source in another viewer</a></p>'
    body += '<h2>Committed passages</h2>'
    if not document['anchors']:
        body += '<p>This document has no declared passage anchors; cite the document as a whole.</p>'
    for anchor in document['anchors']:
        selected = {**canonical, 'anchor': anchor}
        body += '<section class="guavajam-citation-entry" id="' + e(anchor) + '"><h3>Passage <code>#' + e(anchor) + '</code></h3>'
        body += citation(catalog, facts, edition, selected)
        body += '<p><a href="' + e(relative(filename, view_page) + '#' + quote(anchor, safe='')) + '">Read this passage</a></p></section>'
    # Source anchors could be named guavajam-catalog-main. The shell's only ID
    # is chosen outside the declared source namespace on citation pages.
    result = page(catalog, filename, title, body, work=work, edition=edition, work_title=facts['title'], indexable=False)
    main_id = 'guavajam-catalog-main'
    while main_id in document['anchors']:
        main_id += '-view'
    if main_id != 'guavajam-catalog-main':
        result = result.replace('href="#guavajam-catalog-main"', 'href="#' + main_id + '"', 1)
        result = result.replace('id="guavajam-catalog-main" tabindex="-1"', 'id="' + main_id + '" tabindex="-1"', 1)
    return result
