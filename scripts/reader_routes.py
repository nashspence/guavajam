"""Audit the two unauthenticated URLs a reader uses for an issued edition."""
import hashlib
from html.parser import HTMLParser
import os
import re
from urllib.parse import urlsplit

from asset_policy import AssetError, MAX_HTML_BYTES, public_response, response_chunks
from deploy_record_gateway import target_base
from editorial_notices import EDITION_ID_LABELS
from identifier_schema import valid_record_url

# A record is a text page, separate from the potentially image-heavy edition.
MAX_RECORD_BYTES = 4 * 1024 * 1024


class RecordText(HTMLParser):
    """Inspect ordinary visible record text and links independently of page chrome."""
    hidden_tags = {'script', 'style', 'head', 'template', 'noscript'}
    void_tags = {'br', 'hr', 'img', 'input', 'meta', 'link', 'source', 'area', 'base',
                 'col', 'embed', 'param', 'track', 'wbr'}
    blocks = {'p', 'div', 'li', 'br', 'section', 'h1', 'h2', 'h3', 'h4', 'article'}

    def __init__(self):
        super().__init__()
        self.hidden = []
        self.text = []
        self.links = set()
        self.times = set()
        self.commit_text = None
        self.item_start = None
        self.items = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        hidden = (tag in self.hidden_tags or 'hidden' in attrs or attrs.get('aria-hidden') == 'true'
                  or (tag == 'details' and 'open' not in attrs))
        if self.hidden or hidden:
            if tag not in self.void_tags:
                self.hidden.append(tag)
            return
        if tag in self.blocks:
            self.text.append(' ')
        if tag == 'li':
            self.item_start = len(self.text)
        if tag == 'time':
            self.times.add(attrs.get('datetime'))
        if tag == 'a':
            href = attrs.get('href') or ''
            self.links.add(href)
            # GitHub visually abbreviates SHAs. Reconstruct the identifier for comparison
            # only if a visible commit link actually displays that SHA's abbreviation.
            match = re.fullmatch(r'(?:https://github\.com)?/[\w.-]+/[\w.-]+/commit/([a-f0-9]{40})', href)
            if match:
                self.commit_text = len(self.text), match[1]

    def handle_endtag(self, tag):
        if self.hidden and tag in self.hidden:
            del self.hidden[len(self.hidden) - 1 - self.hidden[::-1].index(tag):]
        elif not self.hidden and tag == 'a' and self.commit_text:
            start, sha = self.commit_text
            label = ''.join(self.text[start:])
            if len(label) >= 7 and sha.startswith(label):
                self.text[start:] = [sha]
            self.commit_text = None
        elif not self.hidden and tag in self.blocks:
            if tag == 'li' and self.item_start is not None:
                self.items.append(' '.join(''.join(self.text[self.item_start:]).split()))
                self.item_start = None
            self.text.append(' ')

    def handle_data(self, data):
        if not self.hidden:
            self.text.append(data)


def require_redirect(url, expected):
    with public_response(url, 'text/html') as response:
        if response.status not in {302, 307} or response.getheader('Location') != expected:
            details = [f'HTTP {response.status}']
            if response.getheader('cf-mitigated') == 'challenge':
                details.append('intermediary challenge')
            if response.status == 403:
                # Retain a numeric denial code without copying an intermediary's page,
                # which can contain request details. Bound the diagnostic read.
                denied = RecordText()
                denied.feed(response.read(16384).decode('utf-8', errors='replace'))
                code = re.search(r'\b(?:Error|error code:)\s*(1[0-9]{3})\b', ''.join(denied.text))
                if code:
                    details.append('intermediary error ' + code[1])
            raise AssetError(f'{url}: expected a temporary redirect to {expected} ({"; ".join(details)})')


def require_html(response, url):
    mime = response.getheader('Content-Type', '').partition(';')[0].strip().lower()
    disposition = response.getheader('Content-Disposition', 'inline').partition(';')[0].strip().lower()
    if response.status != 200 or mime != 'text/html' or disposition != 'inline':
        raise AssetError(f'{url}: expected publicly readable inline HTML (HTTP {response.status})')


def verify_reader_routes(manifest, repo, *, notices=(), identifiers=None, archive_base=None, published_at=None):
    tag = manifest['tag']
    url = manifest['record_url']
    if not valid_record_url(url, tag):
        raise AssetError('invalid edition record URL')
    github = 'https://github.com/' + repo + '/releases/tag'
    base = target_base(archive_base or os.environ.get('RECORD_TARGET') or github)
    record = base + '/' + tag
    require_redirect(url, record)
    parser = RecordText()
    with public_response(record, 'text/html') as response:
        require_html(response, record)
        parser.feed(b''.join(response_chunks(response, MAX_RECORD_BYTES)).decode('utf-8'))
        parser.close()
    text = ' '.join(''.join(parser.text).split())
    source = manifest['source_commit']
    if source not in text:
        raise AssetError(f'{url}: public record omits the source commit')
    if manifest['canonical_artifact'] not in text:
        raise AssetError(f'{url}: public record omits the canonical filename')
    visible_notices = [item for item in parser.items if re.match(r'\d{4}-\d{2}-\d{2} (Correction|Withdrawal|Supersession): ', item)]
    if visible_notices != [' '.join(n[2:].split()) for n in notices]:
        raise AssetError(f'{url}: public record differs from the committed notice history')
    labels = tuple(name + ': ' for name in EDITION_ID_LABELS.values())
    visible_ids = [item for item in parser.items if item.startswith(labels)]
    expected_ids = [EDITION_ID_LABELS[key] + ': ' + value for key, value in (identifiers or {}).items()]
    if sorted(visible_ids) != sorted(expected_ids):
        raise AssetError(f'{url}: public record differs from the registered identifiers')
    read_url = url + '/read'
    if read_url not in parser.links:
        raise AssetError(f'{url}: public record omits its reading link')
    # A migrated archive hosts the record and the reading copy side by side.
    # GitHub downloads attachments, so the Worker serves those bytes inline.
    if urlsplit(base).hostname != 'github.com':
        if published_at is not None and published_at not in text and published_at not in parser.times:
            raise AssetError(f'{url}: recovered record omits the original publication timestamp')
        require_redirect(read_url, record + '/read')
        read_url = record + '/read'
    digest = hashlib.sha256()
    with public_response(read_url, 'text/html', timeout=120) as response:
        require_html(response, read_url)
        for chunk in response_chunks(response, MAX_HTML_BYTES):
            digest.update(chunk)
    if digest.hexdigest() != manifest['html_sha256']:
        raise AssetError(f'{read_url}: reading copy differs from the authorized edition bytes')
