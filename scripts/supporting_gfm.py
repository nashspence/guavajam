"""Reviewed supporting GFM: explicit GitHub anchors, no implicit publication IDs."""
from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path

from asset_policy import AssetError
from identifier_schema import SLUG
from render_profile import plain_text, walk

ANCHOR = re.compile(r'<a (?:name|id)="(' + SLUG.pattern + r')"></a>\Z')


def anchor_block(block):
    if block.get('t') == 'RawBlock' and block['c'][0] == 'html':
        text = block['c'][1]
    elif block.get('t') == 'Para' and all(n.get('t') == 'RawInline' and n['c'][0] == 'html'
                                         for n in block['c']):
        text = ''.join(n['c'][1] for n in block['c'])
    else:
        return None
    match = ANCHOR.fullmatch(text)
    return match[1] if match else None


def load_gfm_record(path: Path, *, root=None):
    # Lazy imports keep the shared source validator and appendix builder acyclic.
    from supporting_records import MAX_RECORD_BYTES
    from validate_sources import read_text, parse_pandoc, valid_edition_link
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
    ast = parse_pandoc(source)
    if ast.get('meta'):
        raise AssetError('supporting GFM does not accept YAML metadata')
    blocks, anchors, pending = [], {}, None
    headers = []
    for block in ast['blocks']:
        identifier = anchor_block(block)
        if identifier:
            if pending or identifier in anchors:
                raise AssetError('duplicate or unattached supporting anchor')
            pending = identifier
            continue
        if any(n.get('t') in {'RawBlock', 'RawInline', 'Note'} for n in walk(block)):
            raise AssetError('supporting GFM permits only standalone explicit anchors as HTML; footnotes are unsupported')
        nested = [n for n in walk(block) if n.get('t') == 'Header']
        if nested:
            if block.get('t') != 'Header' or len(nested) != 1 or not pending or block['c'][0] > 5:
                raise AssetError('supporting headings require top-level explicit anchors and levels H1–H5')
            headers.append(block)
        if pending:
            anchors[pending] = len(blocks)
            pending = None
        blocks.append(block)
    if pending or not blocks or blocks[0].get('t') != 'Header' or blocks[0]['c'][0] != 1:
        raise AssetError('supporting GFM requires an explicitly anchored H1 title and no dangling anchor')
    if sum(h['c'][0] == 1 for h in headers) != 1 or 0 not in anchors.values():
        raise AssetError('supporting GFM requires exactly one explicitly anchored H1 title')
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
    return ast, anchors, hashlib.sha256(raw).hexdigest(), len(raw)


def append_gfm(root, relative, slug):
    from supporting_records import _heading, _provenance
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
    result.append(provenance)
    for node in walk(ast['blocks']):
        if node.get('t') == 'Link' and node['c'][2][0].startswith('#'):
            node['c'][2][0] = '#' + anchors[node['c'][2][0][1:]]
    for index, block in enumerate(ast['blocks'][1:], 1):
        anchor = by_index.get(index)
        if block['t'] == 'Header':
            block['c'][0] += 1
            block['c'][1][0] = ''
            result.extend([{'t':'RawBlock','c':['html',f'<a id="{anchor}"></a>']}, block])
        elif anchor:
            # Generated wrappers, never source HTML; the ordinary reader remains static.
            result.extend([{'t':'RawBlock','c':['html',f'<div id="{anchor}">']}, block,
                {'t':'Para','c':[{'t':'Link','c':[['', ['press-permalink'],
                    [['data-press-passage',anchor],['aria-label','Permalink to passage '+anchor],['lang','en']]],
                    [{'t':'Str','c':'¶'}],['press-passage:'+anchor,'']]}]},
                {'t':'RawBlock','c':['html','</div>']}])
        else:
            result.append(block)
    inventory = {'source_path':relative.as_posix(), 'sha256':digest, 'byte_count':size, 'anchors':anchors}
    return result, anchors, record_id, inventory
