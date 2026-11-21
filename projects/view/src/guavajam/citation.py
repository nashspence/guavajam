"""Readable metadata and exact Git identities, shared by every View surface."""
from html import escape
from guavajam_core.bibliography import ordered_authors, author_text


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


def citation_control(record, href, **options):
    """Readable Git citation with its exact canonical JSON download."""
    if record is None:
        return ''
    data = citation_presentation(record, href, **options)

    def inline(parts):
        return ''.join(escape(value) if kind == 'text' else
                       '<' + kind + '>' + escape(value) + '</' + kind + '>' for kind, value in parts)

    return ('<blockquote class="guavajam-citation" aria-label="Git source citation">'
            + ('<p>' + inline(data['description']) + '</p>' if data['description'] else '') +
            '<p class="guavajam-citation-source">' + inline(data['source']) + '</p>' +
            '<a href="' + escape(data['href'], quote=True) + '" download '
            'aria-label="' + data['label'] + '" title="Download citation JSON">'
            '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
            '<path d="M12 3v12m-4-4 4 4 4-4M5 17v4h14v-4"/></svg></a></blockquote>')


def manuscript_citation(revision, facts, *, preview=False):
    record = {'schema': 'guavajam.source-citation.v1', 'hash_algorithm': 'sha1',
              'revision': revision, 'manuscript': 'content/manuscript.md'}
    return citation_control(record, 'source.citation.json', title=facts['title'],
                            authors=author_text(ordered_authors(facts), identifiers=False),
                            provisional=preview)


def work_bibliography(title, authors):
    """Bibliographic work identity without claiming any one Git revision."""
    description = (escape(authors) + '. ' if authors else '') + '<cite>' + escape(title) + '</cite>.'
    return ('<blockquote class="guavajam-citation guavajam-work-citation" '
            'aria-label="Work bibliography"><p>' + description + '</p></blockquote>')
