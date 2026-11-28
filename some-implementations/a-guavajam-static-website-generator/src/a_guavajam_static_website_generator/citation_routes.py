"""One edition-wide human citation index; Git source identities remain exact."""
from hashlib import sha256
from pathlib import PurePosixPath
import posixpath

from .pagination import PAGE_SIZE


def source_offsets(navigation):
    """Return the first source-order slot for each verified document."""
    offset, starts = 0, {}
    for source in sorted(navigation['documents']):
        starts[source] = offset
        documents = navigation['documents']
        descriptors = getattr(documents, 'descriptors', None)
        offset += descriptors[source]['anchor_count'] if descriptors is not None else len(documents[source]['anchors'])
    return starts


def citation_page(position=0):
    """An immutable edition-wide static page, independently of SQLite sorting."""
    if type(position) is not int or position < 0:
        raise ValueError('citation position must be a nonnegative source offset')
    number = position // PAGE_SIZE + 1
    return 'passages.html' if number == 1 else f'passages/page/{number}.html'


def citation_fragment(source, anchor):
    """Disambiguate equal anchor IDs across source documents in one HTML page."""
    return 'citation:' + sha256(source.encode('utf-8')).hexdigest()[:20] + ':' + anchor


def citation_href(source, position, anchor):
    return citation_page(position) + '#' + citation_fragment(source, anchor)


def passage_json(view, anchor):
    """Preserve the existing document-scoped, canonical citation JSON path."""
    path = PurePosixPath(view)
    location = path.with_name('cite' if view == 'index.html' else path.stem + '.cite')
    return str(location / 'passage' / (anchor + '.json'))


def source_download(view):
    path = PurePosixPath(view)
    return str(path.with_name('source.gfm' if view == 'index.html' else path.stem + '.gfm'))


def relative_href(filename, target):
    """Lexical, deployment-independent links relative to the same reader root."""
    origin = PurePosixPath(posixpath.dirname(filename)).parts
    destination = PurePosixPath(posixpath.normpath(target)).parts
    common = 0
    while common < min(len(origin), len(destination)) and origin[common] == destination[common]:
        common += 1
    return '/'.join(['..'] * (len(origin) - common) + list(destination[common:])) or '.'


def document_contents(view):
    path = PurePosixPath(view)
    return str(path.with_name('contents.html' if view == 'index.html' else path.stem + '.contents.html'))


def document_metadata(view):
    path = PurePosixPath(view)
    return '../metadata.html' if view == 'index.html' else str(path.with_name(path.stem + '.metadata.html'))
