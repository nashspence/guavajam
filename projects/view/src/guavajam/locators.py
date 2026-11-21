"""A compact, one-page index of locators declared by a signed version."""
from __future__ import annotations

from html import escape
from pathlib import Path

from .site import page


SUBJECT_FIELDS = (
    ('subject_scheme', 'Scheme'),
    ('classification_code', 'Classification code'),
    ('scheme_uri', 'Scheme URI'),
    ('value_uri', 'Value URI'),
    ('language', 'Language'),
)


def write_locators(directory, *, facts, title, ancestors):
    """Display both kinds together, with their type and full declared metadata.

    No search runtime, extra hierarchy or static pagination is needed: the
    source front-matter contract already bounds the supplied locator metadata.
    """
    directory = Path(directory)
    entries = ([('Subject', item) for item in facts.get('subjects', ())] +
               [('Tag', item) for item in facts.get('keywords', ())])
    body = '<ol class="guavajam-locator-list">'
    for kind, value in entries:
        name = value['subject'] if kind == 'Subject' else value
        body += ('<li><article><p class="guavajam-locator-kind">' + kind + '</p>'
                 '<h2>' + escape(name) + '</h2>')
        if kind == 'Subject':
            details = ''.join('<dt>' + label + '</dt><dd>' + escape(value[key]) + '</dd>'
                              for key, label in SUBJECT_FIELDS if value.get(key))
            if details:
                body += '<dl class="guavajam-locator-detail">' + details + '</dl>'
        body += '</article></li>'
    body += '</ol>'
    if not entries:
        body += '<p>No subjects or tags were declared for this version.</p>'
    (directory / 'locators.html').write_text(
        page(title + ' — Locators', body, ancestors=tuple(ancestors),
             breadcrumb_current='Locators'), encoding='utf-8')
