"""One categorical row presentation for work labels and document metadata."""
from html import escape


def facet(kind, key, label, *, field=''):
    name = field if kind == 'metadata' else 'Tag' if kind == 'keyword' else 'Subject'
    extra = ' data-field="' + escape(field, quote=True) + '"' if kind == 'metadata' else ''
    return ('<span class="guavajam-facet guavajam-' + ('tag' if kind == 'keyword' else 'subject' if kind == 'subject' else 'metadata') + '" data-facet="' + kind + '" data-value="' +
            escape(key, quote=True) + '"' + extra + '><span class="guavajam-facet-type">' +
            escape(name) + '</span> <span class="guavajam-facet-value">' + escape(label) + '</span></span>')


def facet_line(chips, label):
    return ('<p class="guavajam-facets guavajam-scrollline" tabindex="0" aria-label="' +
            escape(label, quote=True) + '">' + ''.join(chips) + '</p>') if chips else ''


def document_facets(fields):
    chips = []
    for key, value in fields:
        values = value if isinstance(value, list) else [value]
        for literal in values:
            chips.append(facet('metadata', literal, literal or '(empty string)', field=key))
        if not values:
            chips.append('<span class="guavajam-facet"><span class="guavajam-facet-type">' +
                         escape(key) + '</span> <span class="guavajam-facet-value">[]</span></span>')
    return facet_line(chips, 'Document metadata')
