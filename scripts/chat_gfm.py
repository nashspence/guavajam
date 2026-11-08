"""Convert selected exported message text to an explicitly anchored review draft."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

from asset_policy import AssetError
from render_profile import plain_text, walk


def _component_blocks(text, report):
    """Protect standalone rich code before Markdown interprets its contents."""
    lines = text.splitlines(keepends=True)
    result = []
    fence = None
    index = 0

    def literal(body, language=''):
        width = max((len(run) for run in re.findall(r'~+', body)), default=2) + 1
        marker = '~' * max(3, width)
        return '\n' + marker + language + '\n' + body + ('' if body.endswith('\n') else '\n') + marker + '\n\n'

    while index < len(lines):
        line = lines[index]
        if fence:
            if re.fullmatch(r' {0,3}' + re.escape(fence[0]) + r'{' + str(fence[1]) + r',}[ \t]*\r?\n?', line):
                fence = None
            result.append(line); index += 1
            continue
        opening = re.fullmatch(r' {0,3}(`{3,}|~{3,})([^\r\n]*)\r?\n?', line)
        if opening and not (opening[1][0] == '`' and '`' in opening[2]):
            fence = (opening[1][0], len(opening[1]))
            result.append(line); index += 1
            continue
        boundary = index == 0 or not lines[index - 1].strip(' \t\r\n')
        component = re.fullmatch(r'<CodeBlock([^>\r\n]*)>[ \t]*\r?\n?', line) if boundary else None
        if component:
            end = index + 1
            while end < len(lines) and not re.fullmatch(r'</CodeBlock>[ \t]*\r?\n?', lines[end]):
                end += 1
            if end == len(lines):
                raise AssetError('unterminated standalone ChatGPT CodeBlock')
            attributes = re.fullmatch(r'\s*(?:language=([\"\x27])([A-Za-z0-9_+.-]+)\1)?\s*(?:editable)?\s*', component[1])
            if attributes:
                report('chatgpt-codeblock-normalized')
                result.append(literal(''.join(lines[index + 1:end]), attributes[2] or ''))
            else:
                report('unsupported-rich-component-as-literal-code')
                result.append(literal(''.join(lines[index:end + 1])))
            index = end + 1
            continue
        if boundary and re.match(r'<[A-Z][A-Za-z0-9]*\b', line) and line.rstrip('\r\n').endswith('/>'):
            report('unsupported-rich-component-as-literal-code')
            result.append(literal(line)); index += 1
            continue
        result.append(line); index += 1
    return ''.join(result)


def review_gfm(record, *, identity='document'):
    from supporting_records import MAX_RECORD_BYTES, validate_record
    validate_record(record)
    if sum(len(p['text'].encode()) for p in record['passages']) > MAX_RECORD_BYTES:
        raise AssetError('selected message text exceeds 8 MiB')
    diagnostics = {'selection-omissions': 1, 'markdown-normalized-to-gfm': 1}
    def report(kind):
        diagnostics[kind] = diagnostics.get(kind, 0) + 1
    def clean(value):
        if isinstance(value, list):
            result = []
            for item in map(clean, value):
                if (isinstance(item, dict) and item.get('t') == 'Code' and result and
                        isinstance(result[-1], dict) and result[-1].get('t') == 'Code' and
                        result[-1]['c'][0] == item['c'][0]):
                    report('adjacent-inline-code-merged')
                    result[-1] = {'t':'Code','c':[item['c'][0],result[-1]['c'][1] + item['c'][1]]}
                else:
                    result.append(item)
            return result
        if not isinstance(value, dict):
            return value
        kind = value.get('t')
        if kind in {'Code', 'CodeBlock', 'Math'}:
            return value  # Never rewrite citations, math delimiters or HTML in code.
        if kind in {'RawBlock', 'RawInline'}:
            if re.match(r'<CodeBlock\b', value['c'][1]):
                raise AssetError('ChatGPT CodeBlock must stand alone between blank lines')
            report('raw-markup-as-literal-code')
            if re.match(r'<Cite\b', value['c'][1]):
                report('unresolved-chatgpt-citation')
            if re.match(r'<Link\b', value['c'][1]):
                report('unresolved-chatgpt-link')
            return {'t':'CodeBlock','c':[['',[],[]],value['c'][1]]} if kind == 'RawBlock' else {
                't':'Code','c':[['',[],[]],value['c'][1]]}
        if kind == 'Image':
            report('image-or-attachment-omitted')
            return {'t':'Str','c':'[Image omitted: '+plain_text(value['c'][1])+']'}
        if kind == 'Note':
            report('footnote-flattened')
            return {'t':'Str','c':'[Footnote: '+plain_text(value['c'])+']'}
        if kind in {'Span','SmallCaps','Underline','Superscript','Subscript','Cite'}:
            report('unsupported-inline-format-as-text')
            return {'t':'Str','c':plain_text(value)}
        if kind in {'Div','DefinitionList','LineBlock'}:
            report('unsupported-block-format-as-literal-code')
            return {'t':'CodeBlock','c':[['',[],[]],plain_text(value)]}
        if kind == 'Link':
            from validate_sources import valid_edition_link
            if not valid_edition_link(value['c'][2][0]) or value['c'][2][0].startswith('#'):
                report('unresolved-or-unsafe-link-as-text')
                return {'t':'Str','c':plain_text(value['c'][1])+' ['+value['c'][2][0]+']'}
        if kind == 'Str':
            text = value['c']
            text, count = re.subn('\ue200[^\ue201]*\ue201', '[Unresolved ChatGPT citation]', text)
            if count:
                report('unresolved-chatgpt-citation')
            if any('\ue000' <= ch <= '\uf8ff' for ch in text):
                report('unsupported-rich-token')
                text = re.sub('[\ue000-\uf8ff]', '[Unsupported token]', text)
            return {'t':'Str','c':text}
        return {key:clean(item) for key,item in value.items()}
    def anchor(identifier):
        return {'t':'RawBlock','c':['html',f'\n<a name="{identifier}"></a>\n']}
    def header(level,text):
        return {'t':'Header','c':[level,['',[],[]],[{'t':'Str','c':text}]]}
    title = record['title']
    doc_id = 'doc-'+hashlib.sha256(identity.encode()).hexdigest()
    blocks = [anchor(doc_id),header(1,title),{'t':'Para','c':[{'t':'Str','c':record['scope']}]}]
    api = None
    for passage in record['passages']:
        text = passage['text']
        if '[Non-text part omitted]' in text or '[Unsupported message content omitted]' in text:
            report('unsupported-message-content-omitted')
        result = subprocess.run(['pandoc','--fail-if-warnings','--from',
            'markdown+raw_html+tex_math_dollars+tex_math_single_backslash-smart-yaml_metadata_block-implicit_header_references-implicit_figures','--to','json'],
            input=_component_blocks(text, report),capture_output=True,text=True)
        if result.returncode:
            raise AssetError('ChatGPT text could not be parsed without warnings')
        ast = json.loads(result.stdout); api = ast['pandoc-api-version']
        label = passage['label'] + (' — '+passage['timestamp'] if 'timestamp' in passage else '')
        blocks.extend([anchor(passage['id']),header(2,label)])
        occurrences = {}
        for original in ast['blocks']:
            block = clean(original)
            if block['t'] == 'Header':
                block['c'][0] = min(5, block['c'][0]+2)
                block['c'][1] = ['',[],[]]
                report('heading-level-adjusted')
            digest = hashlib.sha256(json.dumps(block,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            occurrences[digest] = occurrences.get(digest,0)+1
            identifier = 'b-'+passage['id'][2:]+'-'+digest+'-'+str(occurrences[digest])
            blocks.extend([anchor(identifier),block])
    result = subprocess.run(['pandoc','--fail-if-warnings','--from=json',
        '--to=gfm+tex_math_dollars','--wrap=none'],input=json.dumps({
            'pandoc-api-version':api,'meta':{},'blocks':blocks}),capture_output=True,text=True)
    if result.returncode:
        raise AssetError('ChatGPT GFM writing failed or warned')
    data = (result.stdout.rstrip()+'\n').encode()
    from supporting_gfm import parse_gfm_record
    from validate_sources import read_text
    source, errors = read_text(Path('review.md'), Path('review.md'), raw=data)
    if errors:
        raise AssetError('converted GFM is not valid source: ' + '; '.join(errors))
    parsed, _ = parse_gfm_record(source)
    def retained(nodes):
        return [(node['t'], node['c'][0]['t'] if node['t'] == 'Math' else '',
                 node['c'][1].strip() if node['t'] == 'Math' else node['c'][1])
                for node in walk(nodes) if node.get('t') in {'Math','Code','CodeBlock'}]
    if retained(blocks) != retained(parsed['blocks']):
        raise AssetError('GFM conversion would change mathematics or literal code')
    return data, diagnostics
