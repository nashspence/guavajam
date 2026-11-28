"""Immutable per-document navigation, loaded one source at a time."""
from collections.abc import Mapping
import hashlib
import json
from pathlib import Path

from guavajam.catalog import CatalogError
from guavajam.identifiers import SLUG

MAX_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_NAVIGATION_BYTES = 64 * 1024 * 1024


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def owned(root, relative):
    if not isinstance(relative, str) or not relative or '\\' in relative:
        raise CatalogError('navigation: invalid generated path')
    part = Path(relative)
    path = root / part
    if (part.is_absolute() or any(item in {'.', '..'} for item in relative.split('/'))
            or path.resolve() != path.absolute() or not path.is_relative_to(root)):
        raise CatalogError('navigation: generated path must stay inside its owned directory')
    return path


def read_json(root, relative, maximum):
    path = owned(root, relative)
    if not path.is_file() or path.stat().st_size > maximum:
        raise CatalogError('navigation: missing file or metadata exceeds its operation budget')
    with path.open('r', encoding='utf-8') as stream:
        return json.load(stream)


def write_json(path, value, maximum=MAX_MANIFEST_BYTES):
    """Bound serialization without making a second full encoded copy."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with path.open('wb') as stream:
            encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            for piece in encoder.iterencode(value):
                data = piece.encode('utf-8')
                total += len(data)
                if total + 1 > maximum:
                    raise CatalogError('navigation: generated metadata exceeds its explicit serialization budget')
                stream.write(data)
            stream.write(b'\n')
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return total + 1


def file_inventory(directory, *, exclude=()):
    result, budget = {}, 0
    for path in sorted(Path(directory).rglob('*')):
        if path.is_symlink():
            raise CatalogError('generated inventory contains a symlink')
        if path.is_file() and path.name not in exclude:
            name = path.relative_to(directory).as_posix()
            budget += len(name.encode('utf-8')) + 80
            if budget > MAX_MANIFEST_BYTES:
                raise CatalogError('generated file inventory exceeds its 64-MiB operation budget')
            result[name] = file_hash(path)
    return result


def write_document(root, canonical, preview, source, document):
    value = {'schema': 'guavajam.document-navigation.v1', 'canonical': canonical,
             'preview': preview, 'source': source, 'document': document}
    temporary = root / 'navigation' / '.document.json'
    size = write_json(temporary, value, MAX_NAVIGATION_BYTES)
    digest = file_hash(temporary)
    relative = 'navigation/' + digest + '.json'
    target = root / relative
    if target.exists():
        if target.is_symlink() or file_hash(target) != digest:
            raise CatalogError('navigation: immutable document differs')
        temporary.unlink()
    else:
        temporary.replace(target)
    return {'url': relative, 'sha256': digest, 'size': size, 'anchor_count': len(document['anchors'])}


def _validate(document, details):
    if any(document.get(key) != details.get(key) for key in
           ('title', 'language', 'view', 'source_sha256', 'abstract')):
        raise CatalogError('search: navigation document identity mismatch')
    if not isinstance(document.get('text'), str) or len(document['text'].encode('utf-8')) > MAX_DOCUMENT_BYTES:
        raise CatalogError('search: document text exceeds indexing bounds')
    anchors = document.get('anchors')
    if not isinstance(anchors, list) or not isinstance(document.get('blocks'), list):
        raise CatalogError('search: missing ordered navigation anchors or source blocks')
    seen = {}
    for position, anchor in enumerate(anchors):
        identifier = anchor.get('anchor')
        if (not isinstance(identifier, str) or not SLUG.fullmatch(identifier) or identifier in seen
                or anchor.get('position') != position or anchor.get('kind') not in {'section', 'passage'}
                or not isinstance(anchor.get('title'), str) or not isinstance(anchor.get('text'), str)
                or len(anchor['text'].encode('utf-8')) > MAX_DOCUMENT_BYTES
                or type(anchor.get('level')) is not int or anchor['level'] not in range(7)
                or not isinstance(anchor.get('scope'), str)
                or not (anchor['scope'] == 'document' or anchor['scope'].startswith('quotation:')
                        and SLUG.fullmatch(anchor['scope'].split(':', 1)[1]))):
            raise CatalogError('search: invalid ordered navigation anchor')
        parent = anchor.get('parent')
        if parent is not None and (parent not in seen or seen[parent]['scope'] != anchor['scope']):
            raise CatalogError('search: navigation parent crosses source outline')
        seen[identifier] = anchor
    if sorted(seen) != sorted(details.get('anchors', [])):
        raise CatalogError('search: navigation anchors differ from verified view')


class NavigationDocuments(Mapping):
    def __init__(self, root, manifest, view):
        self.root, self.manifest, self.view = root, manifest, view
        self.descriptors = manifest['documents']

    def __iter__(self):
        return iter(self.descriptors)

    def __len__(self):
        return len(self.descriptors)

    def __getitem__(self, source):
        descriptor = self.descriptors[source]
        if (not isinstance(descriptor, dict) or type(descriptor.get('size')) is not int
                or not 0 < descriptor['size'] <= MAX_NAVIGATION_BYTES
                or type(descriptor.get('anchor_count')) is not int or descriptor['anchor_count'] < 0):
            raise CatalogError('navigation: invalid document descriptor')
        path = owned(self.root, descriptor['url'])
        if (not path.is_file() or path.stat().st_size != descriptor['size']
                or file_hash(path) != descriptor.get('sha256')
                or descriptor['sha256'] != self.view['files'].get(descriptor['url'])):
            raise CatalogError('navigation: document integrity differs')
        value = read_json(self.root, descriptor['url'], MAX_NAVIGATION_BYTES)
        if (value.get('schema') != 'guavajam.document-navigation.v1'
                or value.get('canonical') != self.manifest['canonical']
                or value.get('preview') is not self.manifest['preview'] or value.get('source') != source):
            raise CatalogError('navigation: document source identity differs')
        document = value.get('document', {})
        _validate(document, self.view['documents'][source])
        if descriptor['anchor_count'] != len(document['anchors']):
            raise CatalogError('navigation: document anchor count differs')
        return document


def load_navigation(root, view=None, *, allow_preview=False):
    root = Path(root)
    view = view if view is not None else read_json(root, 'VIEW.json', MAX_MANIFEST_BYTES)
    path = owned(root, 'NAV.json')
    if not path.is_file() or path.stat().st_size > MAX_MANIFEST_BYTES:
        raise CatalogError('search: missing or oversized navigation inventory')
    if file_hash(path) != view.get('files', {}).get('NAV.json'):
        raise CatalogError('search: navigation inventory hash mismatch')
    manifest = read_json(root, 'NAV.json', MAX_MANIFEST_BYTES)
    if (manifest.get('schema') != 'guavajam.navigation.v1' or manifest.get('layout') != 'documents'
            or manifest.get('canonical') != view['canonical']
            or type(manifest.get('preview')) is not bool or manifest['preview'] and not allow_preview
            or set(manifest.get('documents', {})) != set(view['documents'])):
        raise CatalogError('search: navigation source identity mismatch')
    return {**manifest, 'documents': NavigationDocuments(root, manifest, view)}
