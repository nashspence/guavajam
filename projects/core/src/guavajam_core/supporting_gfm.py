"""Reviewed supporting GFM: explicit GitHub anchors, no implicit publication IDs."""
from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path
from datetime import date, datetime

from .asset_policy import AssetError
from .identifiers import SLUG
from .source_ast import plain_text, walk
from .supporting_metadata import split_frontmatter, metadata_block

MAX_RECORD_BYTES = 8 * 1024 * 1024

TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"(?:T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?"
    r"(?:Z|[+-][0-9]{2}:[0-5][0-9]))?\Z"
)

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

def timestamp_label(value: str) -> str:
    """Display source precision, date, and explicit zone without conversion."""
    day = date.fromisoformat(value[:10])
    readable = f"{day.day} {MONTHS[day.month - 1]} {day.year}"
    if len(value) == 10:
        return readable
    zone_suffix = "Z" if value.endswith("Z") else value[-6:]
    time = value[11:-len(zone_suffix)]
    # Only omit an explicitly zero seconds field for legibility; the machine-
    # readable <time datetime> always retains its exact source value.
    if time.endswith(":00"):
        time = time[:-3]
    zone = "UTC" if zone_suffix == "Z" else "UTC" + zone_suffix
    return readable + " · " + time + " " + zone

def valid_timestamp(value) -> bool:
    if not isinstance(value, str) or not TIMESTAMP.fullmatch(value):
        return False
    try:
        if len(value) == 10:
            date.fromisoformat(value)
        else:
            instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
            # Real-world offsets run from −12 to +14; RFC 3339's −00:00
            # denotes an unknown offset, which must not be presented as known.
            seconds = instant.utcoffset().total_seconds()
            if value.endswith("-00:00") or seconds < -12 * 3600 or seconds > 14 * 3600:
                return False
        return True
    except ValueError:
        return False

def record_path(relative: Path) -> bool:
    parts = relative.parts
    return (len(parts) == 4 and parts[0] == "works" and bool(SLUG.fullmatch(parts[1]))
            and parts[2] == "supporting" and relative.suffix == ".md" and bool(SLUG.fullmatch(relative.stem)))




TOOLTIP = re.compile(r"""<span title=(?:"([^"<>]*)"|'([^'<>]*)')>\Z""")
TIME = re.compile(r"""<time datetime=(?:"([^"<>]*)"|'([^'<>]*)')>\Z""")


def _semantic_times(value):
    if isinstance(value, dict):
        return {key: _semantic_times(item) for key, item in value.items()}
    if not isinstance(value, list):
        return value
    result, index = [], 0
    while index < len(value):
        item = value[index]
        raw = item.get('c') if isinstance(item, dict) and item.get('t') == 'RawInline' else None
        match = TIME.fullmatch(raw[1]) if raw and raw[0] == 'html' else None
        if not match:
            result.append(_semantic_times(item)); index += 1
            continue
        instant = html.unescape(match[1] if match[1] is not None else match[2])
        end = index + 1
        while end < len(value) and value[end] != {'t': 'RawInline', 'c': ['html', '</time>']}:
            end += 1
        contents = value[index + 1:end]
        if (not valid_timestamp(instant) or end == len(value) or
                any(node.get('t') not in {'Str', 'Space'} for node in contents) or
                plain_text(contents) != timestamp_label(instant)):
            raise AssetError('semantic time requires an exact valid datetime and canonical plain-text label')
        result.append({'t': 'Span', 'c': [['', [], [['data-press-datetime', instant]]], contents]})
        index = end + 1
    return result


def _tooltip_spans(value):
    """Normalize balanced title-only spans; every other raw tag stays prohibited."""
    if isinstance(value, dict):
        return {key: _tooltip_spans(item) for key, item in value.items()}
    if not isinstance(value, list):
        return value
    result = []
    stack = [result]
    for item in value:
        raw = item.get('c') if isinstance(item, dict) and item.get('t') == 'RawInline' else None
        match = TOOLTIP.fullmatch(raw[1]) if raw and raw[0] == 'html' else None
        if match:
            title = html.unescape(match[1] if match[1] is not None else match[2])
            if (not title.strip() or len(title) > 4096 or title.splitlines() != [title] or
                    any(ord(char) < 32 for char in title)):
                raise AssetError('supporting tooltip requires a nonempty single-line title')
            span = {'t': 'Span', 'c': [['', [], [['title', title]]], []]}
            stack[-1].append(span)
            stack.append(span['c'][1])
        elif raw == ['html', '</span>']:
            if len(stack) == 1:
                raise AssetError('unbalanced supporting tooltip span')
            stack.pop()
        else:
            stack[-1].append(_tooltip_spans(item))
    if len(stack) != 1:
        raise AssetError('unbalanced supporting tooltip span')
    return result

# A source annotation applies only to externally quoted responders.
# Ordinary, unquoted material is always authorial, without a role label.
SPEAKERS = frozenset({'user', 'assistant', 'author', 'responder'})
# Source-backed, bounded technical labels; shared with the ChatGPT importer.
ATTRIBUTION_TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}\Z')
ANCHOR = re.compile(r'<a (?:name|id)="(' + SLUG.pattern + r')"></a>\Z')
SPEAKER_ANCHOR = re.compile(r'<a (?:name|id)="(' + SLUG.pattern +
                            r')" data-press-speaker="(assistant|responder)"></a>\Z')


def anchor_details(block):
    if block.get('t') == 'RawBlock' and block['c'][0] == 'html':
        text = block['c'][1]
    elif block.get('t') == 'Para' and all(n.get('t') == 'RawInline' and n['c'][0] == 'html'
                                         for n in block['c']):
        text = ''.join(n['c'][1] for n in block['c'])
    else:
        return None
    match = ANCHOR.fullmatch(text)
    if match:
        return match[1], None
    match = SPEAKER_ANCHOR.fullmatch(text)
    return (match[1], match[2]) if match else None


def _turn_caption(block):
    """Accept a single reviewed source-entity credit, not a metadata list.

    A technical model slug and optional effort can be synthesized by the
    private ChatGPT converter, but the published source has one plain caption.
    Neither model identity nor speaker role is inferred from caption text.
    """
    if block.get('t') != 'Para' or any(n.get('t') not in {'Str', 'Space'} for n in block['c']):
        return None
    value = plain_text(block)
    if not value.startswith('— ') or len(value) > 320:
        return None
    label = value[2:]
    if (not label or label != label.strip() or len(label) > 310
            or any(ord(char) < 32 for char in label)
            or label.casefold() in {'assistant', 'author', 'user'}
            or ' · ' in label or 'Model:' in label or 'Thinking effort:' in label):
        return None
    return {'label': label}


def _turn_boundary(kind, identifier='', speaker='', caption=None):
    attrs = ([['data-press-speaker', speaker], ['data-press-attribution', caption['label']]]
             if kind == 'start' else [])
    return {'t': 'Div', 'c': [[identifier, ['press-reviewed-turn-' + kind], attrs], []]}


def anchor_block(block):
    details = anchor_details(block)
    return details[0] if details else None


def load_gfm_record(path: Path, *, root=None):
    # Lazy imports keep the shared source validator and appendix builder acyclic.
    from .validate_sources import read_text
    from .asset_policy import load_policy
    policy = load_policy(root) if root is not None else None
    if not path.is_file() or path.resolve() != path.absolute() or path.stat().st_size > MAX_RECORD_BYTES:
        raise AssetError('supporting GFM requires a regular file of at most 8 MiB')
    with path.open('rb') as stream:
        raw = stream.read(MAX_RECORD_BYTES + 1)
    if len(raw) > MAX_RECORD_BYTES:
        raise AssetError('supporting GFM exceeds 8 MiB')
    source, errors = read_text(path, Path(path.name), raw=raw)
    if errors:
        raise AssetError('\n'.join(errors))
    ast, anchors = parse_gfm_record(source, policy=policy)
    return ast, anchors, hashlib.sha256(raw).hexdigest(), len(raw)


def parse_gfm_record(source, *, policy=None):
    from .validate_sources import FORMAT, parse_pandoc, valid_source_link
    fields, body = split_frontmatter(source)
    ast = parse_pandoc(body, reader=FORMAT.replace('+yaml_metadata_block', '-yaml_metadata_block'))
    metadata_anchor = None
    if fields:
        metadata_details = anchor_details(ast['blocks'][0]) if ast['blocks'] else None
        if not metadata_details or metadata_details[1] is not None:
            raise AssetError('supporting metadata requires its own unannotated explicit anchor after the front matter')
        metadata_anchor = metadata_details[0]
        ast['blocks'] = ast['blocks'][1:]
    ast['blocks'] = _semantic_times(_tooltip_spans(ast['blocks']))
    # Only an outermost responder quotation followed by an unquoted credit
    # is an attributed turn. Each child is flattened to retain its own citable
    # anchor; publisher-only sentinels preserve the figure's exact boundaries.
    expanded = []
    index = 0
    while index < len(ast['blocks']):
        block = ast['blocks'][index]
        details = anchor_details(block)
        if details and details[1] is not None:
            if index + 2 >= len(ast['blocks']) or ast['blocks'][index + 1].get('t') != 'BlockQuote':
                raise AssetError('speaker annotation requires an outermost responder blockquote')
            identifier, speaker = details
            quote = ast['blocks'][index + 1]['c']
            caption = _turn_caption(ast['blocks'][index + 2])
            if not quote or caption is None or caption['label'].casefold() in {'user', 'author'}:
                raise AssetError('responder blockquote requires anchored content and an external-speaker credit')
            if any(anchor_details(child) and anchor_details(child)[1] is not None for child in quote):
                raise AssetError('speaker annotations cannot nest within a quoted turn')
            if not any(anchor_details(child) for child in quote):
                raise AssetError('responder blockquote requires individually anchored content')
            expanded.append(_turn_boundary('start', identifier, speaker, caption))
            expanded.extend(quote)
            expanded.append(_turn_boundary('end'))
            index += 3
            continue
        if block.get('t') == 'BlockQuote' and block['c']:
            # No implicit role recognition inside nested ordinary quotations.
            if any(anchor_details(child) and anchor_details(child)[1] is not None
                   for child in block['c']):
                raise AssetError('nested speaker annotations are not supported')
        expanded.append(block)
        index += 1
    ast['blocks'] = expanded
    if ast.get('meta'):
        raise AssetError('unexpected supporting parser metadata')
    blocks, anchors, pending = [], {}, None
    headers = []
    document_level, quotation_level, quotation = 0, 1, None
    for block in ast['blocks']:
        if block.get('t') == 'Div' and 'press-reviewed-turn-start' in block['c'][0][1]:
            identifier = block['c'][0][0]
            if pending or identifier in anchors:
                raise AssetError('duplicate or unattached supporting turn anchor')
            # An explicitly attributed quotation has its own heading sequence
            # beneath the document title. It neither continues nor changes the
            # author's outline; a new reply may legitimately begin again at H2.
            quotation, quotation_level = identifier, 1
            anchors[identifier] = len(blocks)
            blocks.append(block)
            continue
        if block.get('t') == 'Div' and 'press-reviewed-turn-end' in block['c'][0][1]:
            if pending:
                raise AssetError('unattached anchor inside supporting turn')
            quotation = None
            blocks.append(block)
            continue
        details = anchor_details(block)
        if details:
            identifier, explicit_speaker = details
            if pending or identifier in anchors:
                raise AssetError('duplicate or unattached supporting anchor')
            if explicit_speaker is not None:
                raise AssetError('speaker annotation must precede a top-level responder quotation')
            pending = identifier
            continue
        if any(n.get('t') in {'RawBlock', 'RawInline', 'Note'} for n in walk(block)):
            raise AssetError('supporting GFM permits only standalone anchors, exact semantic times and title-only spans as HTML; footnotes are unsupported')
        nested = [n for n in walk(block) if n.get('t') == 'Header']
        if nested:
            if block.get('t') != 'Header' or len(nested) != 1 or not pending or block['c'][0] > 5:
                raise AssetError('supporting headings require top-level explicit anchors and levels H1–H5')
            level = block['c'][0]
            previous = quotation_level if quotation is not None else document_level
            if previous and level > previous + 1:
                scope = 'quotation #' + quotation if quotation is not None else 'document outline'
                raise AssetError(f'supporting heading #{pending} skips H{previous} → H{level} in {scope}; '
                                 f'use H{previous + 1} or add the missing parent heading before this anchor')
            if quotation is None:
                document_level = level
            else:
                quotation_level = level
            headers.append(block)
        if not pending:
            raise AssetError('every top-level supporting content block requires its own explicit anchor')
        anchors[pending] = len(blocks)
        pending = None
        blocks.append(block)
    if pending or not blocks or blocks[0].get('t') != 'Header' or blocks[0]['c'][0] != 1:
        raise AssetError('supporting GFM requires an explicitly anchored H1 title and no dangling anchor')
    if sum(h['c'][0] == 1 for h in headers) != 1 or 0 not in anchors.values():
        raise AssetError('supporting GFM requires exactly one explicitly anchored H1 title')
    if fields:
        if metadata_anchor in anchors:
            raise AssetError('duplicate supporting metadata anchor')
        shifted = {key: position + (position > 0) for key, position in anchors.items()}
        first = next(iter(shifted))
        anchors = {first: 0, metadata_anchor: 1, **{key: pos for key, pos in shifted.items() if key != first}}
        blocks.insert(1, metadata_block(fields))
    if set(anchors) & {h['c'][1][0] for h in headers}:
        raise AssetError('explicit supporting anchors collide with automatic heading IDs')
    for node in walk(blocks):
        if node.get('t') in {'Link','Image'}:
            target = node['c'][2][0]
            if policy and policy.manages(target):
                (policy.parse_image if node['t']=='Image' else policy.parse)(target)
            if node['t']=='Image' and (policy is None or not policy.manages(target)):
                raise AssetError('supporting images require the approved HTTPS asset origin')
        if node.get('t') == 'Link':
            target = node['c'][2][0]
            if target.startswith('#'):
                if target[1:] not in anchors:
                    raise AssetError('supporting link requires an existing explicit anchor: ' + target)
            elif not valid_source_link(target):
                raise AssetError('supporting links require an explicit local fragment, HTTPS or mailto URL')
        if node.get('t') == 'Image' and not node['c'][2][0].startswith('https://'):
            raise AssetError('supporting images require the approved HTTPS asset origin')
    ast['blocks'] = blocks
    return ast, anchors


