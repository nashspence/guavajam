"""Shared bounds and ordinary links for static publication directories."""
from html import escape

from .icons import link_icon

PAGE_SIZE = 20


def _slices(items):
    items = list(items)
    return [items[start:start + PAGE_SIZE] for start in range(0, len(items), PAGE_SIZE)] or [[]]


def page_control(direction, *, href=None, identifier=None):
    """One arrow control serves static hrefs and opt-in browser pagination."""
    previous = direction == 'prev'
    label = 'Previous' if previous else 'Next'
    attrs = (' class="guavajam-page-control" aria-label="' + label + ' page" title="' + label + ' page"' +
             (' id="' + escape(identifier, quote=True) + '"' if identifier else ''))
    cue = link_icon('arrow-left' if previous else 'arrow-right', label)
    if href is not None:
        return '<a href="' + escape(href, quote=True) + '" rel="' + direction + '"' + attrs + '>' + cue + '</a>'
    return '<button type="button"' + attrs + ' disabled>' + cue + '</button>'


def _static_pager(number, count, *, first_href, page_prefix):
    """Ordinary href pagination for HTTP crawlers and browsers without JS."""
    def href(page):
        return first_href if page == 1 else page_prefix + str(page) + '.html'

    return ('<nav class="guavajam-pagination guavajam-static-pages" aria-label="List pages">' +
            page_control('prev', href=href(number - 1) if number > 1 else None) +
            '<span class="guavajam-page-label">Page ' + str(number) + ' of ' + str(count) + '</span>' +
            page_control('next', href=href(number + 1) if number < count else None) + '</nav>')
