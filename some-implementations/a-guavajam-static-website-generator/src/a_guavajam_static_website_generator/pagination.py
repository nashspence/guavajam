"""Shared bounds and ordinary links for static publication directories."""
from html import escape

from .icons import link_icon

PAGE_SIZE = 20


def _slices(items):
    items = list(items)
    return [items[start:start + PAGE_SIZE] for start in range(0, len(items), PAGE_SIZE)] or [[]]


def _static_pager(number, count, *, first_href, page_prefix):
    """Ordinary href pagination for HTTP crawlers and browsers without JS."""
    if count < 2:
        return ''

    def link(page_number, label, *, relation=''):
        href = first_href if page_number == 1 else page_prefix + str(page_number) + '.html'
        return ('<a href="' + escape(href, quote=True) + '"' +
                (' rel="' + relation + '" aria-label="' + escape(label + ' page', quote=True) +
                 '" title="' + escape(label + ' page', quote=True) + '"' if relation else '') + '>' +
                (link_icon('arrow-left' if relation == 'prev' else 'arrow-right', label)
                 if relation else escape(str(label))) + '</a>')

    links = []
    if number > 1:
        links.append(link(number - 1, 'Previous', relation='prev'))
    if number > 3:
        links.append(link(1, '1'))
        if number > 4:
            links.append('<span aria-hidden="true">…</span>')
    for candidate in range(max(1, number - 2), min(count, number + 2) + 1):
        if candidate == number:
            links.append('<span aria-current="page">' + str(number) + '</span>')
        else:
            links.append(link(candidate, candidate))
    if number < count - 2:
        if number < count - 3:
            links.append('<span aria-hidden="true">…</span>')
        links.append(link(count, count))
    if number < count:
        links.append(link(number + 1, 'Next', relation='next'))
    return ('<nav class="guavajam-pagination guavajam-static-pages" aria-label="Directory pages">'
            '<span class="guavajam-meta">Page ' + str(number) + ' of ' + str(count) + '</span>' +
            ''.join(links) + '</nav>')
