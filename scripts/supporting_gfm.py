"""Reviewed supporting GFM: explicit GitHub anchors, no implicit publication IDs."""
from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path
from datetime import date, datetime

from asset_policy import AssetError
from identifier_schema import SLUG
from render_profile import plain_text, walk
from supporting_metadata import split_frontmatter, metadata_block

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

def _heading(level: int, anchor: str, title: str, cls: str) -> list[dict]:
    return [
        {"t": "RawBlock", "c": ["html", f'<a id="{anchor}"></a>']},
        {"t": "Header", "c": [level, ["", [cls], []], [{"t": "Str", "c": title}]]},
    ]

def _provenance(scope: str, path: str, sha256: str) -> dict:
    # A definition list makes the reviewed source and full digest easy to find,
    # select, and print without making a raw SHA dominate the reading experience.
    return {"t": "RawBlock", "c": ["html", (
        '<div class="press-record-intro">'
        '<p class="press-record-scope">' + html.escape(scope) + '</p>'
        '<dl class="press-record-provenance">'
        '<dt>Source</dt><dd><code>' + html.escape(path) + '</code></dd>'
        '<dt>SHA-256</dt><dd><code>' + sha256 + '</code></dd>'
        '</dl></div>'
    )]}


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
    """A plain, review-owned end credit. Never infer technical labels from prose.

    The only accepted order is speaker, optional Model, optional Thinking effort;
    no markup, timestamps, HTML or arbitrary extra credit attributes are allowed.
    """
    if block.get('t') != 'Para' or any(n.get('t') not in {'Str', 'Space'} for n in block['c']):
        return None
    value = plain_text(block)
    if not value.startswith('— ') or len(value) > 450:
        return None
    parts = value[2:].split(' · ')
    label = parts.pop(0)
    if (not label or len(label) > 160 or label != label.strip()
            or any(ord(char) < 32 for char in label)):
        return None
    details = {'label': label}
    for index, item in enumerate(parts):
        if item.startswith('Model: ') and 'model' not in details and 'thinking_effort' not in details:
            key, token = 'model', item[len('Model: '):]
        elif item.startswith('Thinking effort: ') and 'thinking_effort' not in details and not parts[index + 1:]:
            key, token = 'thinking_effort', item[len('Thinking effort: '):]
        else:
            return None
        if not ATTRIBUTION_TOKEN.fullmatch(token):
            return None
        details[key] = token
    return details


def _turn_boundary(kind, identifier='', speaker='', caption=None):
    attrs = ([['data-press-speaker', speaker], ['data-press-attribution', caption['label']]]
             + ([['data-press-model', caption['model']]] if 'model' in caption else [])
             + ([['data-press-thinking-effort', caption['thinking_effort']]] if 'thinking_effort' in caption else [])
             if kind == 'start' else [])
    return {'t': 'Div', 'c': [[identifier, ['press-reviewed-turn-' + kind], attrs], []]}


def anchor_block(block):
    details = anchor_details(block)
    return details[0] if details else None


def load_gfm_record(path: Path, *, root=None):
    # Lazy imports keep the shared source validator and appendix builder acyclic.
    from validate_sources import read_text
    from asset_policy import load_policy
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
    from validate_sources import FORMAT, parse_pandoc, valid_edition_link
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
    for block in ast['blocks']:
        if block.get('t') == 'Div' and 'press-reviewed-turn-start' in block['c'][0][1]:
            identifier = block['c'][0][0]
            if pending or identifier in anchors:
                raise AssetError('duplicate or unattached supporting turn anchor')
            anchors[identifier] = len(blocks)
            blocks.append(block)
            continue
        if block.get('t') == 'Div' and 'press-reviewed-turn-end' in block['c'][0][1]:
            if pending:
                raise AssetError('unattached anchor inside supporting turn')
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
            elif not valid_edition_link(target):
                raise AssetError('supporting links require an explicit local fragment, HTTPS or mailto URL')
        if node.get('t') == 'Image' and not node['c'][2][0].startswith('https://'):
            raise AssetError('supporting images require the approved HTTPS asset origin')
    ast['blocks'] = blocks
    return ast, anchors


def append_gfm(root, relative, slug):
    ast, positions, digest, size = load_gfm_record(root / relative, root=root)
    record_id = f'record-md-{len(slug)}-{slug}'
    anchors = {key: record_id if index == 0 else f'support-md-{len(slug)}-{slug}-{key}'
               for key, index in positions.items()}
    by_index = {index: anchors[key] for key, index in positions.items()}
    title = plain_text(ast['blocks'][0]['c'][2])
    result = _heading(2, record_id, 'Supporting record — ' + title, 'press-record-heading')
    provenance = _provenance('Reviewed supporting Markdown.', relative.as_posix(), digest)
    provenance['c'][1] = provenance['c'][1].replace('<div ', '<div data-press-source="' +
        html.escape(relative.as_posix()) + '" data-press-sha256="' + digest + '" ', 1)
    for node in walk(ast['blocks']):
        if node.get('t') == 'Span' and {'data-press-metadata-key', 'data-press-metadata-value'} & dict(node['c'][0][2]).keys():
            # Escape trusted literal AST text and its whitespace before the HTML
            # writer can insert formatting newlines into machine-readable values.
            literal = html.escape(node['c'][1][0]['c']).replace(' ', '&#32;').replace('\r', '&#13;').replace('\n', '&#10;').replace('\t', '&#9;')
            node['c'][1] = [{'t':'RawInline','c':['html', literal]}]
        if node.get('t') == 'Span' and 'data-press-datetime' in dict(node['c'][0][2]):
            instant = dict(node['c'][0][2])['data-press-datetime']
            node.clear()
            node.update(t='RawInline', c=['html', '<time datetime="'+instant+'">'+html.escape(timestamp_label(instant))+'</time>'])
        if node.get('t') == 'Link' and node['c'][2][0].startswith('#'):
            node['c'][2][0] = '#' + anchors[node['c'][2][0][1:]]
    if len(ast['blocks']) > 1 and ast['blocks'][1]['t'] == 'Div' and 'press-document-metadata' in ast['blocks'][1]['c'][0][1]:
        block = ast['blocks'][1]
        anchor = by_index[1]
        block['c'][0][0] = anchor
        block['c'][0][2].extend([['data-press-document', record_id], ['role', 'group'], ['aria-label', 'Document metadata']])
        block['c'][1].insert(0, {'t':'Para','c':[{'t':'Str','c':'Document metadata'}, {'t':'Space'},
            {'t':'Link','c':[['', ['press-permalink'], [['data-press-passage',anchor], ['aria-label','Permalink to document metadata'], ['lang','en']]],
                           [{'t':'Str','c':'¶'}], ['press-passage:'+anchor,'']]}]})
        # Keep publisher-generated source provenance first. The following
        # document-scoped fields are reviewed source content, not publisher
        # metadata; their original key order and values remain unchanged.
        result.append(provenance)
        result.append(block)
    else:
        result.append(provenance)
    first_passage = True
    turn_start = None
    for index, block in enumerate(ast['blocks'][1:], 1):
        if block['t'] == 'Div' and 'press-document-metadata' in block['c'][0][1]:
            continue
        anchor = by_index.get(index)
        if block['t'] == 'Div' and 'press-reviewed-turn-start' in block['c'][0][1]:
            speaker = dict(block['c'][0][2])['data-press-speaker']
            turn_start, turn_index = block, index
            result.append({'t':'RawBlock','c':['html',
                '<figure class="press-turn" id="' + anchor + '" data-press-speaker="' + speaker + '">']})
            result.append({'t':'RawBlock','c':['html', '<blockquote class="press-turn-quotation">']})
            continue
        if block['t'] == 'Div' and 'press-reviewed-turn-end' in block['c'][0][1]:
            # The paired opening marker provides the exact reviewed caption.
            details = dict(turn_start['c'][0][2])
            caption = details['data-press-attribution']
            turn_anchor = by_index[turn_index]
            result.append({'t':'RawBlock','c':['html', '</blockquote><figcaption class="press-turn-attribution">']})
            permalink = {'t':'Link','c':[['', ['press-permalink'],
                [['data-press-passage',turn_anchor],
                 ['aria-label','Permalink to attributed turn '+turn_anchor], ['lang','en']]],
                 [{'t':'Str','c':'¶'}], ['press-passage:'+turn_anchor,'']]}
            credit = {'t':'Span', 'c':[['', ['press-turn-credit'], []],
                [{'t':'Str','c':'—'}, {'t':'Space'}, {'t':'Str','c':caption}]]}
            content = [credit, {'t':'Space'}, permalink]
            facts = []
            for key, label in (('data-press-model', 'Model'), ('data-press-thinking-effort', 'Thinking effort')):
                if key in details:
                    if facts:
                        facts.extend([{'t':'Space'}, {'t':'Str','c':'·'}, {'t':'Space'}])
                    facts.extend([{'t':'Str','c':label + ':'}, {'t':'Space'}, {'t':'Str','c':details[key]}])
            if facts:
                content.extend([{'t':'Space'}, {'t':'Span','c':[['',['press-turn-details'],[]],facts]}])
            result.append({'t':'Para','c':content})
            result.append({'t':'RawBlock','c':['html', '</figcaption></figure>']})
            turn_start = None
            continue
        if block['t'] == 'Header':
            if block['c'][0] == 2 and turn_start is None:
                block['c'][1][1].append('press-passage-heading')
                if first_passage:
                    block['c'][1][1].append('press-first-passage')
                    first_passage = False
            block['c'][0] += 1
            block['c'][1][0] = ''
            result.extend([{'t':'RawBlock','c':['html',f'<a id="{anchor}"></a>']}, block])
        elif anchor:
            metadata = (block['t'] == 'Para' and len(block['c']) == 1 and block['c'][0]['t'] == 'Emph'
                        and (any(n.get('t') == 'RawInline' and n['c'][1].startswith('<time ') for n in walk(block))
                             or plain_text(block).startswith(('Model: ', 'Thinking effort: '))))
            if metadata:
                block['c'] = block['c'][0]['c']
            cls = 'press-passage-datetime' if metadata else 'press-supporting-element'
            permalink = {'t':'Link','c':[['', ['press-permalink'],
                    [['data-press-passage',anchor],['aria-label','Permalink to passage '+anchor],['lang','en']]],
                    [{'t':'Str','c':'¶'}],['press-passage:'+anchor,'']]}
            children = [block]
            if block['t'] in {'Para', 'Plain'}:
                block['c'].extend([{'t':'Space'}, permalink])
            else:
                children.append({'t':'Para','c':[permalink]})
            result.append({'t':'Div','c':[[anchor,[cls],[]],children]})
        else:
            result.append(block)
    inventory = {'source_path':relative.as_posix(), 'sha256':digest, 'byte_count':size, 'anchors':anchors}
    return result, anchors, record_id, inventory


def append_supporting_records(ast: dict, root: Path, manuscript: Path, tracked: set[Path]) -> list[dict]:
    selector = re.compile(rf'supporting/({SLUG.pattern})\.md(?:#({SLUG.pattern}))?\Z')
    selected = {}
    for node in walk(ast['blocks']):
        if node.get('t') != 'Link' or not node['c'][2][0].startswith('supporting/'):
            continue
        match = selector.fullmatch(node['c'][2][0])
        if not match:
            raise AssetError('supporting citations require reviewed GFM .md sources: '+node['c'][2][0])
        slug, fragment = match.groups()
        selected.setdefault(slug, []).append((node, fragment))
    if len(selected) > 128:
        raise AssetError('too many supporting documents')
    inventory, total = [], 0
    for slug, citations in selected.items():
        relative = manuscript.parent / 'supporting' / (slug+'.md')
        if relative not in tracked:
            raise AssetError('supporting source is missing or untracked: '+str(relative))
        total += (root/relative).stat().st_size
        if total > 100*1024*1024:
            raise AssetError('supporting source inventory exceeds 100 MiB')
        blocks, anchors, record_id, entry = append_gfm(root, relative, slug)
        for node, fragment in citations:
            if fragment and fragment not in anchors:
                raise AssetError('unknown explicit supporting anchor: '+node['c'][2][0])
            node['c'][2][0] = '#'+(anchors[fragment] if fragment else record_id)
        ast['blocks'].extend(blocks)
        inventory.append(entry)
    return inventory
