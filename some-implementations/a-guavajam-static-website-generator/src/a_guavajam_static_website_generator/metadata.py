"""One readable metadata tree derived from a signed version's verified facts.

This is a static presentation of authoritative Git and front-matter records.
Neither the page nor its links issue a second publication or citation identity.
"""
from __future__ import annotations

from html import escape
from pathlib import Path

from guavajam.bibliography import ordered_authors

from .site import page


SUBJECT_FIELDS = (
    ('subject_scheme', 'Scheme'),
    ('classification_code', 'Classification code'),
    ('scheme_uri', 'Scheme URI'),
    ('value_uri', 'Value URI'),
    ('language', 'Language'),
)


def _field(label, value, *, css=''):
    """Only escaped, verified values enter metadata; callers own fixed links."""
    return ('<div class="guavajam-metadata-field' + (' ' + css if css else '') + '">'
            '<dt>' + escape(label) + '</dt><dd>' + escape(str(value)) + '</dd></div>')


def _linked_field(label, href, text, *, download=False):
    return ('<div class="guavajam-metadata-field"><dt>' + escape(label) + '</dt><dd><a href="' +
            escape(href, quote=True) + '"' + (' download' if download else '') + '>' +
            escape(text) + '</a></dd></div>')


def _group(name, identifier, content, *, empty=None):
    fields = ('<dl class="guavajam-metadata-fields">' + content + '</dl>' if content or empty is None
              else '<p>' + escape(empty) + '</p>')
    return ('<section class="guavajam-metadata-branch" id="' + identifier + '" '
            'aria-labelledby="' + identifier + '-heading"><h2 id="' + identifier + '-heading">' +
            escape(name) + '</h2>' + fields + '</section>')


def write_metadata(directory, *, facts, title, ancestors, revision, published, message,
                   committer, repository, site_origin=None):
    """Present bibliography, provenance, rights, and locators on a single page.

    Retain the same edition-specific JSON records, source declarations, and
    author ordering as the previous separate pages. Metadata is bounded by
    GuavaJam's signed front-matter contract; it needs no SQLite or HTML paging.
    """
    directory = Path(directory)
    authors = ordered_authors(facts)
    publication = _field('Title', title)
    for number, author in enumerate(authors, 1):
        publication += _field('Author' if len(authors) == 1 else f'Author {number}', author['name'])
        if author.get('orcid'):
            publication += _field('ORCID', author['orcid'])
    if facts.get('language'):
        publication += _field('Language', facts['language'])
    if facts.get('schema_type'):
        publication += _field('Type', facts['schema_type'])
    if facts.get('abstract'):
        publication += _field('Abstract', facts['abstract'])
    for kind, value in sorted(facts.get('identifiers', {}).items()):
        publication += _field(kind.upper(), value)

    provenance = (_field('Git commit', revision, css='guavajam-metadata-identity') +
                  '<div class="guavajam-metadata-field"><dt>Publication date</dt>'
                  '<dd><time datetime="' + escape(published, quote=True) + '">' +
                  escape(published) + '</time></dd></div>' +
                  _field('Commit message', message, css='guavajam-message') +
                  _linked_field('Verification record', 'SOURCE.json', 'SOURCE.json', download=True) +
                  _linked_field('Canonical citation', 'CITATION.json', 'CITATION.json', download=True))
    if committer['name']:
        identity = escape(committer['name'])
        if committer['email']:
            identity = ('<a href="mailto:' + escape(committer['email'], quote=True) + '">' +
                        identity + '</a>')
        provenance += ('<div class="guavajam-metadata-field"><dt>Committer</dt><dd>' +
                       identity + '</dd></div>')
    provenance += _linked_field('Source repository', 'source.bundle', 'Git bundle', download=True)
    if repository:
        provenance += _field('Repository location', repository)

    rights = _field('Rights', facts.get('rights') or 'No rights statement declared')
    locators = ([('Subject', item) for item in facts.get('subjects', ())] +
                [('Tag', item) for item in facts.get('keywords', ())])
    locator_body = '<ol class="guavajam-metadata-locators">'
    for kind, item in locators:
        name = item['subject'] if kind == 'Subject' else item
        locator_body += ('<li><article class="guavajam-metadata-leaf">'
                         '<p class="guavajam-metadata-kind">' + kind + '</p>'
                         '<h3>' + escape(name) + '</h3>')
        if kind == 'Subject':
            details = ''.join(_field(label, item[key]) for key, label in SUBJECT_FIELDS if item.get(key))
            if details:
                locator_body += '<dl class="guavajam-metadata-fields">' + details + '</dl>'
        locator_body += '</article></li>'
    locator_body += '</ol>'
    if not locators:
        locator_body += '<p>No subjects or tags declared.</p>'

    body = (_group('Publication', 'publication', publication) +
            _group('Provenance', 'provenance', provenance) +
            _group('Rights', 'rights', rights) +
            '<section class="guavajam-metadata-branch" id="locators" aria-labelledby="locators-heading">'
            '<h2 id="locators-heading">Locators</h2>' + locator_body + '</section>')
    (directory / 'metadata.html').write_text(
        page(title + ' — Metadata', body, ancestors=tuple(ancestors),
             breadcrumb_current='Metadata', site_origin=site_origin), encoding='utf-8')


def write_document_metadata(target, *, facts, fields, source, revision, source_sha256,
                            ancestors, site_origin=None):
    """Present a supporting document's own facts and literal declared metadata."""
    target = Path(target)
    reader = target.with_name(target.name.removesuffix('.metadata.html') + '.html')
    download = reader.with_suffix('.gfm').name
    cited = reader.with_suffix('.citation.json').name
    document = _field('Title', facts['title'])
    if facts.get('abstract'):
        document += _field('Abstract', facts['abstract'])
    declared = ''
    for label, value in fields:
        content = ('<ul>' + ''.join('<li>' + escape(item) + '</li>' for item in value) + '</ul>'
                   if isinstance(value, list) else escape(value))
        declared += ('<div class="guavajam-metadata-field"><dt>' + escape(label) + '</dt><dd>' + content + '</dd></div>')
    provenance = (_field('Git commit', revision, css='guavajam-metadata-identity') +
                  _field('Source path', source) + _field('Source SHA-256', source_sha256) +
                  _linked_field('Source document', download, source, download=True) +
                  _linked_field('Canonical citation', cited, cited, download=True) +
                  _linked_field('Verification record', '../../SOURCE.json', 'SOURCE.json', download=True) +
                  _linked_field('Edition metadata', '../../metadata.html', 'Edition metadata') +
                  _linked_field('Source repository', '../../source.bundle', 'Git bundle', download=True))
    body = (_group('Document', 'document', document) +
            _group('Declared metadata', 'declared', declared, empty='No document metadata declared.') +
            _group('Provenance', 'provenance', provenance))
    target.write_text(page(facts['title'] + ' — Metadata', body, ancestors=ancestors,
                           breadcrumb_current='Metadata', site_origin=site_origin), encoding='utf-8')
