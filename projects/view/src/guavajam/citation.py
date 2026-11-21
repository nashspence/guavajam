"""Readable metadata and exact Git identities, shared by every View surface."""
from html import escape
from guavajam_core.bibliography import ordered_authors, author_text


def edition_label(position):
    """The verified first-parent sequence, not a publisher-claimed timestamp."""
    return 'v' + str(position)


def citation_control(record, href, *, title='', authors='', published='', edition_number=None,
                     document_title='', provisional=False):
    """Readable Git-commit citation; keep canonical machine JSON downloadable.

    The bibliographic display uses verified manuscript facts and the signed
    edition's claimed date. The repository commit remains the exact identity;
    no HTML URL is treated as publication authority.
    """
    if record is None:
        return ''
    revision = record['revision']
    manuscript = record.get('manuscript', '')
    document = record.get('document', manuscript)
    subject = document_title or title
    description = (escape(authors) + '. ' if authors else '')
    if subject:
        description += '<cite>' + escape(subject) + '</cite>'
        if document_title and title and document_title != title:
            description += '. In <cite>' + escape(title) + '</cite>'
        description += '.'
    details = []
    if edition_number is not None:
        details.append(edition_label(edition_number))
    if published:
        details.append(escape(published))
    if details:
        description += (' ' if description else '') + ', '.join(details) + '.'
    source = ('Git commit <code>' + escape(revision) + '</code>' +
              (' · <code>' + escape(document) + '</code>' if document else '') +
              (' · <code>#' + escape(record['anchor']) + '</code>' if record.get('anchor') else ''))
    label = 'Download draft source identity JSON' if provisional else 'Download official citation JSON'
    return ('<blockquote class="guavajam-citation" aria-label="Git source citation">'
            + ('<p>' + description + '</p>' if description else '') +
            '<p class="guavajam-citation-source">' + source + '</p>' +
            '<a href="' + escape(href, quote=True) + '" download '
            'aria-label="' + label + '" title="Download citation JSON">'
            '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
            '<path d="M12 3v12m-4-4 4 4 4-4M5 17v4h14v-4"/></svg></a></blockquote>')


def manuscript_citation(revision, facts, *, preview=False):
    record = {'schema': 'guavajam.source-citation.v1', 'hash_algorithm': 'sha1',
              'revision': revision, 'manuscript': 'content/manuscript.md'}
    return citation_control(record, 'source.citation.json', title=facts['title'],
                            authors=author_text(ordered_authors(facts), identifiers=False),
                            provisional=preview)
