"""Bounded, literal, document-owned supporting metadata; no field vocabulary."""
import re
from pathlib import Path

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode
from yaml.tokens import AliasToken, AnchorToken, DirectiveToken, TagToken
from asset_policy import AssetError

MAX_METADATA_BYTES = 64 * 1024
MAX_METADATA_FIELDS = 64
CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]')


def parse_metadata(source):
    if len(source.encode('utf-8')) > MAX_METADATA_BYTES:
        raise AssetError('supporting metadata exceeds 64 KiB')
    try:
        if any(isinstance(token, (AliasToken, AnchorToken, DirectiveToken, TagToken)) for token in yaml.scan(source)):
            raise AssetError('supporting metadata prohibits YAML aliases, anchors, tags and directives')
        root = yaml.compose(source, Loader=yaml.SafeLoader)
    except (yaml.YAMLError, RecursionError):
        raise AssetError('invalid supporting metadata YAML') from None
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


def split_frontmatter(source):
    if not source.startswith('---\n'):
        return [], source
    end = source.find('\n---\n', 3)
    if end == -1:
        raise AssetError('supporting metadata requires a closing --- delimiter')
    return parse_metadata(source[4:end + 1]), source[end + 5:].lstrip('\n')


def read_metadata(path: Path):
    with path.open('rb') as stream:
        raw = stream.read(MAX_METADATA_BYTES + 1)
    if len(raw) > MAX_METADATA_BYTES:
        raise AssetError('supporting metadata exceeds 64 KiB')
    try:
        return parse_metadata(raw.decode('utf-8'))
    except UnicodeError:
        raise AssetError('supporting metadata requires UTF-8') from None


def metadata_frontmatter(fields):
    if not fields:
        return ''
    source = yaml.safe_dump(dict(fields), allow_unicode=True, sort_keys=False, width=1000)
    if parse_metadata(source) != fields:
        raise AssetError('supporting metadata cannot be serialized without loss')
    return '---\n' + source + '---\n\n'


def metadata_block(fields):
    def span(value, marker, *, cls=None):
        return {'t':'Span','c':[['', [cls] if cls else [], [[marker, value if marker.endswith('-key') else '']]],
                              [{'t':'Str','c':value}]]}
    entries = []
    for key, value in fields:
        values = value if isinstance(value, list) else [value]
        content = [{'t':'Plain','c':[span(item, 'data-press-metadata-value', cls='press-metadata-value')]} for item in values]
        blocks = [{'t':'BulletList','c':[[item] for item in content]}] if isinstance(value, list) else content
        definition = {'t':'Div','c':[['', [], [['data-press-metadata-kind', 'list' if isinstance(value, list) else 'text']]], blocks]}
        entries.append([[span(key, 'data-press-metadata-key')], [[definition]]])
    return {'t':'Div','c':[['', ['press-document-metadata'], []], [{'t':'DefinitionList','c':entries}]]}
