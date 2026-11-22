"""Readable metadata and exact Git identities, shared by every generated surface."""
from html import escape
from guavajam.bibliography import ordered_authors, author_text


def edition_label(position):
    """The verified first-parent sequence, not a publisher-claimed timestamp."""
    return 'v' + str(position)


def citation_presentation(record, href, *, title='', authors='', published='', edition_number=None,
                          document_title='', provisional=False):
    """One bibliographic formatter for static HTML and disposable SQLite rows.

    Parts contain plain text and fixed inline roles; browser results never
    interpret source or database values as HTML. Canonical JSON stays separate.
    """
    subject = document_title or title
    description = [['text', authors + '. ']] if authors else []
    if subject:
        description.append(['cite', subject])
        if document_title and title and document_title != title:
            description.extend([['text', '. In '], ['cite', title]])
        description.append(['text', '.'])
    details = ([edition_label(edition_number)] if edition_number is not None else [])
    if published:
        details.append(published)
    if details:
        description.append(['text', (' ' if description else '') + ', '.join(details) + '.'])
    source = [['text', 'Git commit '], ['code', record['revision']]]
    document = record.get('document', record.get('manuscript', ''))
    for value in [document, '#' + record['anchor'] if record.get('anchor') else '']:
        if value:
            source.extend([['text', ' · '], ['code', value]])
    return {'description': description, 'source': source, 'href': href,
            'label': 'Download draft source identity JSON' if provisional else 'Download official citation JSON'}


def passage_context(source, anchor, scope):
    """The same source label accompanies static entries and search results."""
    return (('Manuscript' if source == 'content/manuscript.md' else 'Supporting document') +
            ' · ' + source + ' · #' + anchor +
            (' · Quoted passage' if scope.startswith('quotation:') else ''))


def citation_control(record, href, *, heading=False, heading_anchor=None, **options):
    """Readable Git citation, optionally carrying the document's real title."""
    if record is None:
        return ''
    data = citation_presentation(record, href, **options)

    def inline(parts, *, title_heading=False):
        rendered = []
        for kind, value in parts:
            item = escape(value) if kind == 'text' else '<' + kind + '>' + escape(value) + '</' + kind + '>'
            if title_heading and kind == 'cite':
                identity = ' id="' + escape(heading_anchor, quote=True) + '"' if heading_anchor else ''
                permalink = (' <a class="guavajam-permalink" lang="en" aria-label="Permalink to passage ' +
                             escape(heading_anchor, quote=True) + '" href="#' + escape(heading_anchor, quote=True) + '">¶</a>'
                             if heading_anchor else '')
                item = '<h1 class="guavajam-citation-title"' + identity + '>' + item + permalink + '</h1>'
                title_heading = False
            rendered.append(item)
        return ''.join(rendered)

    # A bibliography describes a source; it is not a quotation from that source.
    # The description is flow content so its title can be a native heading.
    return ('<div class="guavajam-citation" role="group" aria-label="Git source citation">'
            + ('<div class="guavajam-citation-description">' +
               inline(data['description'], title_heading=heading) + '</div>' if data['description'] else '') +
            '<p class="guavajam-citation-source">' + inline(data['source']) + '</p>' +
            '<a href="' + escape(data['href'], quote=True) + '" download '
            'aria-label="' + data['label'] + '" title="Download citation JSON">'
            '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
            '<path d="M12 3v12m-4-4 4 4 4-4M5 17v4h14v-4"/></svg></a></div>')


def document_citation(revision, facts, *, source='content/manuscript.md', details=None, preview=False):
    record = {'schema': 'guavajam.source-citation.v1', 'hash_algorithm': 'sha1',
              'revision': revision, 'manuscript': 'content/manuscript.md'}
    supporting = source != record['manuscript']
    details = details or {}
    if supporting:
        record['document'] = source
    href = source.rsplit('/', 1)[-1][:-3] + '.citation.json' if supporting else 'source.citation.json'
    return citation_control(record, href, heading=True, title=facts['title'],
                            document_title=details.get('title', '') if supporting else '',
                            heading_anchor=details.get('title_anchor'),
                            authors=author_text(ordered_authors(facts), identifiers=False),
                            provisional=preview)
