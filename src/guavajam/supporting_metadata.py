"""Reviewed supporting titles/abstracts and separately scoped literal metadata."""
import re
from pathlib import Path

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode
from yaml.tokens import AliasToken, AnchorToken, DirectiveToken, TagToken
from .asset_policy import AssetError

MAX_METADATA_BYTES = 64 * 1024
MAX_METADATA_FIELDS = 64
CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]')


def _mapping(source):
    if len(source.encode('utf-8')) > MAX_METADATA_BYTES:
        raise AssetError('supporting metadata exceeds 64 KiB')
    try:
        if any(isinstance(token, (AliasToken, AnchorToken, DirectiveToken, TagToken)) for token in yaml.scan(source)):
            raise AssetError('supporting metadata prohibits YAML aliases, anchors, tags and directives')
        root = yaml.compose(source, Loader=yaml.SafeLoader)
    except (yaml.YAMLError, RecursionError):
        raise AssetError('invalid supporting metadata YAML') from None
    return root


def _fields(root):
    if root is None:
        return []
    if not isinstance(root, MappingNode) or len(root.value) > MAX_METADATA_FIELDS:
        raise AssetError('supporting metadata requires a mapping of at most 64 fields')
    fields, seen = [], set()
    def text(node):
        if not isinstance(node, ScalarNode) or CONTROL.search(node.value):
            raise AssetError('supporting metadata permits literal text or lists of text only')
        return node.value  # Preserve scalar spellings, without YAML type coercion.
    for key_node, value_node in root.value:
        key = text(key_node)
        if not key.strip() or key.splitlines() != [key] or key in seen:
            raise AssetError('supporting metadata requires nonempty single-line unique labels')
        seen.add(key)
        value = [text(item) for item in value_node.value] if isinstance(value_node, SequenceNode) else text(value_node)
        fields.append((key, value))
    return fields


def parse_metadata(source):
    """Read optional importer input without assigning semantics to its labels."""
    return _fields(_mapping(source))


def split_frontmatter(source):
    """The v1 supporting source has a title, optional abstract and metadata map."""
    from .bibliography import plain_string, check_optional_bibliography
    if not source.startswith('---\n'):
        raise AssetError('supporting GFM requires title front matter')
    end = source.find('\n---\n', 3)
    if end == -1:
        raise AssetError('supporting metadata requires a closing --- delimiter')
    root = _mapping(source[4:end + 1])
    if not isinstance(root, MappingNode):
        raise AssetError('supporting front matter requires a mapping')
    facts, fields, seen = {}, [], set()
    for key_node, node in root.value:
        if not isinstance(key_node, ScalarNode) or key_node.value not in {'title', 'abstract', 'metadata'}:
            raise AssetError('supporting front matter permits title, abstract and metadata only')
        key = key_node.value
        if key in seen:
            raise AssetError('supporting front matter requires unique fields')
        seen.add(key)
        if key == 'metadata':
            if not isinstance(node, MappingNode):
                raise AssetError('supporting metadata requires a mapping')
            fields = _fields(node)
        else:
            if not isinstance(node, ScalarNode) or node.tag != 'tag:yaml.org,2002:str':
                raise AssetError('supporting title and abstract require plain-text strings')
            facts[key] = node.value
    if not plain_string(facts.get('title')) or len(facts['title'].encode('utf-8')) > 4096:
        raise AssetError('supporting title must be one nonempty plain-text line of at most 4 KiB')
    check_optional_bibliography(facts)
    return facts, fields, source[end + 5:].lstrip('\n')


def document_frontmatter(title, *, abstract=None, metadata=()):
    facts = {'title': title}
    if abstract is not None:
        facts['abstract'] = abstract
    if metadata:
        facts['metadata'] = dict(metadata)
    source = '---\n' + yaml.safe_dump(facts, allow_unicode=True, sort_keys=False, width=1000) + '---\n\n'
    parsed, fields, _ = split_frontmatter(source)
    if parsed != {key: value for key, value in facts.items() if key != 'metadata'} or fields != list(metadata):
        raise AssetError('supporting metadata cannot be serialized without loss')
    return source


def read_metadata(path: Path):
    with path.open('rb') as stream:
        raw = stream.read(MAX_METADATA_BYTES + 1)
    if len(raw) > MAX_METADATA_BYTES:
        raise AssetError('supporting metadata exceeds 64 KiB')
    try:
        return parse_metadata(raw.decode('utf-8'))
    except UnicodeError:
        raise AssetError('supporting metadata requires UTF-8') from None


def metadata_block(fields):
    def span(value, marker, *, cls=None):
        return {'t':'Span','c':[['', [cls] if cls else [], [[marker, value if marker.endswith('-key') else '']]],
                              [{'t':'Str','c':value}]]}
    entries = []
    for key, value in fields:
        values = value if isinstance(value, list) else [value]
        content = [{'t':'Plain','c':[span(item, 'data-guavajam-metadata-value', cls='guavajam-metadata-value')]} for item in values]
        blocks = [{'t':'BulletList','c':[[item] for item in content]}] if isinstance(value, list) else content
        definition = {'t':'Div','c':[['', [], [['data-guavajam-metadata-kind', 'list' if isinstance(value, list) else 'text']]], blocks]}
        entries.append([[span(key, 'data-guavajam-metadata-key')], [[definition]]])
    return {'t':'Div','c':[['', ['guavajam-document-metadata'], []], [{'t':'DefinitionList','c':entries}]]}
