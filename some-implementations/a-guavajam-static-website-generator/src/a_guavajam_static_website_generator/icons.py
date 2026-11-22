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
                   for name, label in [('history', 'Versions'), ('quote', 'Cite'), ('info', 'Metadata')])


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
    """Decorate complete HTML by inserting at parsed offsets, preserving source bytes.

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

    class Links(HTMLParser):
        anchor = None

        def handle_starttag(self, tag, attrs):
            if tag == 'a':
                line, column = self.getpos(); start = offsets[line - 1] + column
                self.anchor = (start, start + len(self.get_starttag_text()), dict(attrs))

        def handle_endtag(self, tag):
            if tag != 'a' or self.anchor is None: return
            start, end, attrs = self.anchor; self.anchor = None
            if 'data-guavajam-link' in attrs or 'data-guavajam-icon' in attrs: return
            href = attrs.get('href', ''); parsed = urlsplit(href)
            names = ['external-link'] if external_http(parsed) else []
            if parsed.scheme == 'mailto': names.append('mail')
            download = 'download' in attrs
            mime = attrs.get('type') or (link_mime(href) if download else '')
            if download: names.append(download_icon(mime))
            if not names: return
            edits.append((start + 2, ' data-guavajam-link=""'))
            if mime and 'type' not in attrs: edits.append((start + 2, ' type="' + escape(mime, quote=True) + '"'))
            if download and mime and 'title' not in attrs:
                edits.append((start + 2, ' title="Download (' + escape(mime, quote=True) + ')"'))
            edits.append((end, ''.join(icon(name) + ' ' for name in names)))
            if download and mime:
                line, column = self.getpos()
                edits.append((offsets[line - 1] + column,
                              ' <span class="guavajam-link-mime">(' + escape(mime) + ')</span>'))

    parser = Links(convert_charrefs=True); parser.feed(html); parser.close()
    for offset, text in sorted(edits, reverse=True): html = html[:offset] + text + html[offset:]
    if 'guavajam-icon' in html and '<!-- Lucide icon notices' not in html:
        notice = files(__package__).joinpath('assets/lucide-LICENSE.txt').read_text('utf-8').replace('--', '- -')
        html = html.replace('</body>', '<!-- Lucide icon notices\n' + notice + '\n-->\n</body>', 1)
    return html
