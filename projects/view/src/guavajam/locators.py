"""Paged version locators, rendered directly from signed publication facts."""
from __future__ import annotations

from html import escape
from pathlib import Path

from .pagination import _slices, _static_pager
from .site import page


SUBJECT_FIELDS = (
    ('subject_scheme', 'Scheme'),
    ('classification_code', 'Classification code'),
    ('scheme_uri', 'Scheme URI'),
    ('value_uri', 'Value URI'),
    ('language', 'Language'),
)


def render_subject(record):
    """Preserve the full structured subject without inferring equivalences."""
    result = '<li><article><h3>' + escape(record['subject']) + '</h3>'
    metadata = ''.join('<dt>' + label + '</dt><dd>' + escape(record[name]) + '</dd>'
                       for name, label in SUBJECT_FIELDS if record.get(name))
    if metadata:
        result += '<dl class="guavajam-locator-detail">' + metadata + '</dl>'
    return result + '</article></li>'


def write_locators(directory, *, facts, title, ancestors):
    """Create ordinary linked HTML pages, never an unbounded locator list."""
    directory = Path(directory)
    subjects = list(facts.get('subjects', ()))
    tags = list(facts.get('keywords', ()))
    entries = [('subject', subject) for subject in subjects] + [('tag', tag) for tag in tags]
    chunks = _slices(entries)
    for number, chunk in enumerate(chunks, 1):
        first = number == 1
        destination = (directory / 'locators.html' if first else
                       directory / 'locators' / 'page' / f'{number}.html')
        destination.parent.mkdir(parents=True, exist_ok=True)
        body = '<section class="guavajam-locators">'
        for kind, heading in (('subject', 'Subjects'), ('tag', 'Tags')):
            group = [value for entry_kind, value in chunk if entry_kind == kind]
            if group:
                body += ('<section aria-label="' + heading + '"><h2>' + heading + '</h2>'
                         '<ol class="guavajam-editions guavajam-locator-list">')
                for entry in group:
                    body += (render_subject(entry) if kind == 'subject' else
                             '<li><article><h3>' + escape(entry) + '</h3></article></li>')
                body += '</ol></section>'
        if not entries:
            body += '<p>No subjects or tags were declared for this version.</p>'
        body += _static_pager(number, len(chunks),
                              first_href='locators.html' if first else '../../locators.html',
                              page_prefix='locators/page/' if first else '') + '</section>'
        destination.write_text(page(title + ' — Locators' +
                                    (f' — page {number}' if not first else ''), body,
                                    ancestors=tuple(ancestors) if first else tuple(
                                        ('../../' + path, name) for path, name in ancestors),
                                    breadcrumb_current='Locators'), encoding='utf-8')
