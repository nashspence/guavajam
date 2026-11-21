"""One route calculation for static citations, readers and the SQLite index."""
from pathlib import PurePosixPath
import posixpath

from .pagination import PAGE_SIZE


def citation_page(view, position=0):
    """Use the immutable NAV source position, never a filtered result offset."""
    if type(position) is not int or position < 0:
        raise ValueError('citation position must be a nonnegative source offset')
    path = PurePosixPath(view)
    first = path.with_name('cite.html' if view == 'index.html' else path.stem + '.cite.html')
    number = position // PAGE_SIZE + 1
    return str(first if number == 1 else first.with_suffix('') / 'page' / f'{number}.html')


def citation_fragment(anchor):
    # A colon cannot occur in a source ID. Shell/search IDs therefore never
    # compete with source anchors such as "main" or "guavajam-query".
    return 'citation:' + anchor


def citation_href(view, position, anchor):
    return citation_page(view, position) + '#' + citation_fragment(anchor)


def passage_json(view, anchor):
    return str(PurePosixPath(citation_page(view)).with_suffix('') / 'passage' / (anchor + '.json'))


def source_download(view):
    path = PurePosixPath(view)
    return str(path.with_name('source.gfm' if view == 'index.html' else path.stem + '.gfm'))


def relative_href(filename, target):
    """Both arguments are generator-owned paths relative to the reader root."""
    # posixpath.relpath resolves relative inputs against the process cwd,
    # which can clamp leading '..' components at '/' in a shallow container
    # workdir. These paths are relative to the reader root, so compare their
    # lexical components without consulting the host filesystem.
    origin = PurePosixPath(posixpath.dirname(filename)).parts
    destination = PurePosixPath(posixpath.normpath(target)).parts
    common = 0
    while common < min(len(origin), len(destination)) and origin[common] == destination[common]:
        common += 1
    return '/'.join(['..'] * (len(origin) - common) + list(destination[common:])) or '.'
