"""Source-derived navigation shared by static views and disposable search indexes.

No headings or relationships are inferred from prose. Explicitly attributed
quotations retain independent outlines, even when their headings restart at H2.
"""
from __future__ import annotations

from bisect import bisect_left
from html import escape
import json
import re


ANCHOR = re.compile(r'<a id="([a-z][a-z0-9]*(?:-[a-z0-9]+)*)"></a>\Z')


def manuscript_anchor(block):
    """Recognize the reviewed standalone source anchor parsed by Pandoc."""
    kind, contents = block.get('t'), block.get('c')
    if kind == 'RawBlock' and contents[0] == 'html':
        match = ANCHOR.fullmatch(contents[1])
        return match[1] if match else None
    if (kind == 'Para' and len(contents) == 2
            and all(node.get('t') == 'RawInline' and node['c'][0] == 'html' for node in contents)):
        match = ANCHOR.fullmatch(''.join(node['c'][1] for node in contents))
        return match[1] if match else None
    return None


def source_text(value):
    """Keep inline words intact and block/cell/list boundaries separated.

    Only source content contributes: no attributes, link destinations, automatic
    identifiers, generated permalinks, navigation labels or raw markup.
    """
    chunks = []

    def visit(node):
        if isinstance(node, list):
            for child in node:
                visit(child)
            return
        if not isinstance(node, dict):
            return
        kind, content = node.get('t'), node.get('c', [])
        if kind in {'Str', 'MetaString'}:
            chunks.append(content)
        elif kind in {'Space', 'SoftBreak', 'LineBreak', 'HorizontalRule'}:
            chunks.append(' ')
        elif kind in {'RawInline', 'RawBlock'}:
            return
        elif kind in {'Code', 'Math'}:
            chunks.append(content[1])
        elif kind == 'CodeBlock':
            if 'guavajam-media' in content[0][1]:
                # Declarations are validated before navigation extraction.
                chunks.extend([' ', json.loads(content[1])['title'], ' '])
            else:
                chunks.extend([' ', content[1], ' '])
        elif kind == 'Header':
            chunks.append(' '); visit(content[2]); chunks.append(' ')
        elif kind in {'Link', 'Image', 'Span'}:
            visit(content[1])
        elif kind == 'Div':
            chunks.append(' '); visit(content[1]); chunks.append(' ')
        elif kind in {'Para', 'Plain', 'BlockQuote', 'Note', 'LineBlock'}:
            chunks.append(' '); visit(content); chunks.append(' ')
        elif kind in {'BulletList', 'DefinitionList'}:
            for item in content:
                chunks.append(' '); visit(item); chunks.append(' ')
        elif kind == 'OrderedList':
            visit(content[1])
        elif kind == 'Table':
            # Rows and cells contain Para/Plain blocks; table attributes and
            # widths are bare values and deliberately ignored by visit().
            chunks.append(' '); visit(content); chunks.append(' ')
        else:
            visit(content)

    visit(value)
    return ' '.join(''.join(chunks).split())


def extract(ast, positions=None):
    """Capture text and source-order anchor records before presentation edits.

    Section text runs to the next heading in the same outline, regardless of
    level; child sections therefore do not duplicate their parent's body.
    Supporting passages retain their own block text. An attributed turn anchor
    covers that whole quotation, while its child anchors remain independently
    searchable. Parent IDs never cross between author and quotation outlines.
    """
    blocks = ast.get('blocks', [])
    indexed = {position: key for key, position in (positions or {}).items()}
    records, chunks, stacks = [], [], {'document': []}
    scope, pending, quotation = 'document', None, None
    for index, block in enumerate(blocks):
        anchor = indexed.get(index)
        classes = block['c'][0][1] if block.get('t') == 'Div' else []
        if 'guavajam-reviewed-turn-start' in classes:
            quotation = anchor
            scope = 'quotation:' + anchor
            stacks[scope] = []
            attribution = dict(block['c'][0][2])['data-guavajam-attribution']
            records.append({'anchor': anchor, 'title': attribution, 'kind': 'passage',
                            'level': 0, 'position': len(records), 'parent': None,
                            'scope': scope, 'text': '', '_block': index, '_turn': True})
            continue
        if 'guavajam-reviewed-turn-end' in classes:
            chunks.append((index, scope, attribution))
            scope, quotation = 'document', None
            continue
        if positions is None:
            pending_anchor = manuscript_anchor(block)
            if pending_anchor:
                pending = pending_anchor
                continue
            if block.get('t') == 'Header':
                anchor, pending = pending, None
        text = source_text(block)
        chunks.append((index, scope, text))
        if not anchor:
            continue
        level = block['c'][0] if block.get('t') == 'Header' else 0
        stack = stacks[scope]
        if level:
            while stack and stack[-1]['level'] >= level:
                stack.pop()
        parent = stack[-1]['anchor'] if stack else quotation
        record = {'anchor': anchor, 'title': source_text(block['c'][2]) if level else text[:160] or anchor,
                  'kind': 'section' if level else 'passage', 'level': level,
                  'position': len(records), 'parent': parent, 'scope': scope,
                  'text': text, '_block': index}
        records.append(record)
        if level:
            stack.append(record)
    by_scope = {}
    for index, owner, text in chunks:
        positions, texts = by_scope.setdefault(owner, ([], []))
        positions.append(index)
        texts.append(text)
    following_heading = {}
    for record in reversed(records):
        if record['kind'] == 'section' or record.get('_turn'):
            end = following_heading.get(record['scope'], len(blocks)) if record['kind'] == 'section' else len(blocks)
            positions, texts = by_scope.get(record['scope'], ([], []))
            start, stop = bisect_left(positions, record['_block']), bisect_left(positions, end)
            record['text'] = ' '.join(text for text in texts[start:stop] if text)
            if record['kind'] == 'section':
                following_heading[record['scope']] = record['_block']
        record.pop('_block')
        record.pop('_turn', None)
    return {'text': ' '.join(text for _, _, text in chunks if text), 'anchors': records}


def section_outline(records):
    """Share the verified author outline between standalone and site contents."""
    sections = [record for record in records
                if record['kind'] == 'section' and record['level'] > 1 and record['scope'] == 'document']
    roots, stack = [], []
    for record in sections:
        node = {'record': record, 'children': []}
        while stack and stack[-1]['record']['level'] >= record['level']:
            stack.pop()
        (stack[-1]['children'] if stack else roots).append(node)
        stack.append(node)

    return roots


def contents_blocks(records, used):
    """A nested native TOC from the same document outline used for indexing."""
    sections = [record for record in records
                if record['kind'] == 'section' and record['level'] > 1 and record['scope'] == 'document']
    if len(sections) < 3:
        return []
    used = set(used)

    def fresh(base):
        name, number = base, 1
        while name in used:
            number += 1
            name = base + '-' + str(number)
        used.add(name)
        return name

    roots = section_outline(records)

    def items(nodes):
        result = []
        for node in nodes:
            record = node['record']
            item = [{'t': 'Plain', 'c': [{'t': 'Link', 'c': [
                ['', [], []], [{'t': 'Str', 'c': record['title']}], ['#' + record['anchor'], '']]}]}]
            if node['children']:
                item.append({'t': 'BulletList', 'c': items(node['children'])})
            result.append(item)
        return result

    contents, label = fresh('guavajam-contents'), fresh('guavajam-contents-label')
    return [{'t': 'RawBlock', 'c': ['html', '<nav id="' + contents +
             '" class="guavajam-contents" aria-labelledby="' + label + '"><p id="' + label +
             '" class="guavajam-contents-label" lang="en">Contents</p>']},
            {'t': 'BulletList', 'c': items(roots)}, {'t': 'RawBlock', 'c': ['html', '</nav>']}]


def reader_navigation(path):
    """Only local links whose targets also exist in a standalone rendered view."""
    supporting = '/supporting/' in path
    prefix = '../' if supporting else ''
    return ('<nav class="guavajam-document-nav" aria-label="Edition documents">' +
            ('<a href="../index.html">Manuscript</a> · ' if supporting else '') +
            '<a href="' + prefix + 'documents.html">All documents in this edition</a></nav>')


def document_directory(navigation, *, prefix='', include_sections=False, headings=False):
    """Every rendered source document is reachable, including unlinked notes."""
    rows = []
    for path, document in navigation['documents'].items():
        url = prefix + document['view']
        kind = 'Manuscript' if path == 'content/manuscript.md' else 'Supporting document'
        link = '<a href="' + escape(url, quote=True) + '">' + escape(document['title']) + '</a>'
        row = ('<li>' + ('<article><h3 tabindex="0">' + link + '</h3>' if headings else link) +
               '<p class="' + ('guavajam-meta guavajam-scrollline" tabindex="0" aria-label="Document source' if headings else 'guavajam-document-path') + '">' + kind + ' · <code>' + escape(path) + '</code></p>')
        if include_sections:
            sections = [record for record in document['anchors'] if record['kind'] == 'section'
                        and record['level'] > 1 and record['scope'] == 'document']
            if sections:
                row += '<ul>' + ''.join('<li><a href="' + escape(url + '#' + record['anchor'], quote=True) +
                                        '">' + escape(record['title']) + '</a></li>' for record in sections) + '</ul>'
        rows.append(row + ('</article>' if headings else '') + '</li>')
    return '<ul class="guavajam-document-list' + (' guavajam-record-list' if headings else '') + '">' + ''.join(rows) + '</ul>'
