"""Pinned Lucide artwork shared by static pages and optional search controls."""
from functools import cache
from html import escape
from html.parser import HTMLParser
from importlib.resources import files
import json
import re
from urllib.parse import unquote, urlsplit


@cache
def _artwork():
    return json.loads(files(__package__).joinpath('assets/lucide.json').read_text('utf-8'))['icons']


def icon(name):
    return ('<svg class="guavajam-icon guavajam-icon-' + name + '" viewBox="0 0 24 24" '
            'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
            'stroke-linejoin="round" aria-hidden="true" focusable="false">' + re.sub(r'>\s+<', '><', _artwork()[name]) + '</svg>')


def link_icon(name, fallback):
    """Static SVG with legible text for CSS-free and SVG-free text clients.

    Graphical clients hide the fallback with CSS; no JavaScript is required.
    The surrounding link retains its own descriptive accessible name.
    """
    return icon(name) + '<span class="guavajam-icon-fallback">' + escape(fallback) + '</span>'


def inline_icon(name, fallback):
    """The trusted SVG and fallback as one Pandoc presentation-only inline."""
    return {'t': 'RawInline', 'c': ['html', link_icon(name, fallback)]}


def search_icon_templates():
    return ''.join('<template id="guavajam-icon-' + name + '">' + link_icon(name, label) + '</template>'
                   for name, label in [('history', 'Versions'), ('quote', 'Cite'), ('info', 'Metadata'), ('link', 'Link')])


MIME_TYPES = {'.json': 'application/json', '.pdf': 'application/pdf', '.gfm': 'text/markdown',
              '.md': 'text/markdown', '.txt': 'text/plain', '.csv': 'text/csv', '.html': 'text/html',
              '.zip': 'application/zip', '.gz': 'application/gzip', '.mp3': 'audio/mpeg',
              '.wav': 'audio/wav', '.ogg': 'audio/ogg', '.mp4': 'video/mp4', '.webm': 'video/webm',
              '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}


def link_mime(href):
    path = unquote(urlsplit(href).path).lower()
    return next((mime for suffix, mime in MIME_TYPES.items() if path.endswith(suffix)), '')


def download_icon(mime):
    if mime == 'application/json' or mime.endswith('+json'): return 'file-json'
    if mime.startswith('image/'): return 'file-image'
    if mime.startswith('audio/'): return 'file-audio'
    if mime.startswith('video/'): return 'file-video'
    if mime in {'application/zip', 'application/gzip', 'application/x-tar'}: return 'file-archive'
    if mime in {'text/html', 'application/xml', 'text/xml'}: return 'file-code'
    if mime.startswith('text/') or mime == 'application/pdf': return 'file-text'
    return 'file-down'


def normalize_site_origin(value):
    """Accept an operator-selected HTTP(S) origin, never infer one from source."""
    if value is None:
        return None
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value) or '\\' in value:
        raise ValueError('site origin must be an HTTP(S) origin without credentials, path, query or fragment')
    parsed = urlsplit(value)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in {'', '/'} or parsed.query or parsed.fragment):
        raise ValueError('site origin must be an HTTP(S) origin without credentials, path, query or fragment')
    host = parsed.hostname.encode('idna').decode('ascii')
    host = '[' + host + ']' if ':' in host else host
    port = parsed.port
    if port is not None and port != (443 if parsed.scheme == 'https' else 80):
        host += ':' + str(port)
    return parsed.scheme + '://' + host


def finish_html(html, *, site_origin=None):
    """Decorate links while preserving targets and separating navigation captions.

    Relative links stay local. When the site origin is supplied, same-origin
    absolute HTTP(S) links also stay local; without that information absolute
    HTTP(S) links retain their existing external marker. Declared download
    types take precedence over known suffixes.
    """
    site_origin = normalize_site_origin(site_origin)
    origin = urlsplit(site_origin) if site_origin is not None else None

    def external_http(target):
        if target.scheme not in {'http', 'https'} and not target.netloc:
            return False
        if origin is None:
            return True  # Build output alone cannot determine its deployed hostname.
        scheme = target.scheme or origin.scheme
        try:
            port = target.port if target.port is not None else (443 if scheme == 'https' else 80)
            origin_port = origin.port if origin.port is not None else (443 if origin.scheme == 'https' else 80)
        except ValueError:
            return True
        host = target.hostname.encode('idna').decode('ascii') if target.hostname else None
        return (scheme, host, port) != (origin.scheme, origin.hostname, origin_port)

    offsets = [0, *(i + 1 for i, char in enumerate(html) if char == '\n')]
    edits = []
    void = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    class Text(HTMLParser):
        def __init__(self, markup):
            super().__init__(); self.parts = []; self.feed(markup)
        def handle_data(self, text): self.parts.append(text)

    def attributes(attrs):
        return ''.join(' ' + key + ('="' + escape(value, quote=True) + '"' if value is not None else '')
                       for key, value in attrs.items())

    class Links(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True); self.anchor = None; self.stack = []

        def handle_starttag(self, tag, pairs):
            attrs = dict(pairs)
            line, column = self.getpos(); start = offsets[line - 1] + column
            if tag == 'h3' and any('guavajam-record-list' in a.get('class', '').split() for _, a in self.stack):
                attrs['class'] = ' '.join(dict.fromkeys([*attrs.get('class', '').split(), 'guavajam-scrollline']))
                attrs['tabindex'] = '0'
                edits.append((start, start + len(self.get_starttag_text()), '<h3' + attributes(attrs) + '>'))
            if tag == 'a':
                nav = any(t == 'nav' and set(a.get('class', '').split()) &
                          {'guavajam-navigation', 'guavajam-version-menu', 'guavajam-contents'} for t, a in self.stack)
                heading = any(t in {'h1','h2','h3','h4','h5','h6'} for t, _ in self.stack)
                self.anchor = (start, start + len(self.get_starttag_text()), attrs, nav, heading)
            if tag not in void: self.stack.append((tag, attrs))

        def handle_endtag(self, tag):
            if tag == 'a' and self.anchor is not None:
                self.decorate()
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index][0] == tag:
                    del self.stack[index:]; break

        def decorate(self):
            start, end, attrs, nav, heading = self.anchor; self.anchor = None
            if not attrs.get('href') or 'data-guavajam-link' in attrs or 'data-guavajam-icon' in attrs: return
            if 'guavajam-skip' in attrs.get('class', '').split(): return
            if (attrs.get('role') in {'doc-noteref', 'doc-backlink'} or
                    set(attrs.get('class', '').split()) & {'footnote-ref', 'footnote-back'}): return
            if any('guavajam-pagination' in a.get('class', '').split() for _, a in self.stack): return
            line, column = self.getpos(); close = offsets[line - 1] + column
            inner = html[end:close]
            if not inner.strip() or attrs.get('aria-hidden') == 'true': return
            artwork = re.search(r'<svg class="guavajam-icon guavajam-icon-([a-z-]+)".*?</svg>', inner, re.S)
            if artwork and not nav: return
            href = attrs['href']; parsed = urlsplit(href)
            names = ['external-link'] if external_http(parsed) else []
            if parsed.scheme == 'mailto': names.append('mail')
            download = 'download' in attrs
            mime = attrs.get('type') or (link_mime(href) if download else '')
            if download: names.append(download_icon(mime))
            elif not names and parsed.scheme in {'', 'http', 'https'}: names.append('link')
            if not names: return
            if mime and 'type' not in attrs: attrs['type'] = mime
            if download and mime and 'title' not in attrs: attrs['title'] = 'Download (' + mime + ')'
            if nav or heading:
                if artwork:
                    inner = inner[:artwork.start()] + inner[artwork.end():]
                label = ' '.join(''.join(Text(inner).parts).split())
                if not label: return
                attrs['class'] = (attrs.get('class', '') + ' guavajam-icon-control').strip()
                attrs['data-guavajam-icon'] = ''
                attrs.setdefault('aria-label', label + (' (' + mime + ')' if download and mime else ''))
                attrs.setdefault('title', attrs['aria-label'])
                cue = download_icon(mime) if download else artwork.group(1) if artwork else names[0]
                suffix = ' <span class="guavajam-link-mime">(' + escape(mime) + ')</span>' if download and mime else ''
                replacement = ('<span class="guavajam-icon-label"><a' + attributes(attrs) + '>' +
                               link_icon(cue, 'Link') + '</a> <span class="guavajam-link-label" aria-hidden="true">' +
                               inner.strip() + suffix + '</span></span>')
            else:
                attrs['data-guavajam-link'] = ''
                suffix = ' <span class="guavajam-link-mime">(' + escape(mime) + ')</span>' if download and mime else ''
                replacement = '<a' + attributes(attrs) + '>' + ''.join(icon(name) + ' ' for name in names) + inner + suffix + '</a>'
            edits.append((start, html.index('>', close) + 1, replacement))

    parser = Links(); parser.feed(html); parser.close()
    for start, end, text in sorted(edits, reverse=True): html = html[:start] + text + html[end:]
    if 'guavajam-icon' in html and '<!-- Lucide icon notices' not in html:
        notice = files(__package__).joinpath('assets/lucide-LICENSE.txt').read_text('utf-8').replace('--', '- -')
        html = html.replace('</body>', '<!-- Lucide icon notices\n' + notice + '\n-->\n</body>', 1)
    return html
