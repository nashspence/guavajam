"""Convert selected exported message text to an explicitly anchored review draft."""
import hashlib
import json
import re
import subprocess
from pathlib import Path
import html
from supporting_gfm import MAX_RECORD_BYTES, SPEAKERS, valid_timestamp, timestamp_label

from asset_policy import AssetError
from identifier_schema import SLUG
from chat_references import citation_inlines, component_inlines
from render_profile import plain_text, walk

ATTRIBUTION_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}\Z")

def _text(value, *, single_line=False):
    if not isinstance(value, str) or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]", value):
        return False
    return not single_line or (bool(value.strip()) and value.splitlines() == [value])

def _validate_transcript(record: dict) -> None:
    if not isinstance(record, dict) or record.keys() != {"title", "scope", "passages"}:
        raise AssetError("supporting record must contain exactly title, scope and passages")
    for field in ("title", "scope"):
        if not _text(record[field], single_line=True):
            raise AssetError(f"supporting record {field} must be nonempty single-line text")
    if not isinstance(record["passages"], list) or not record["passages"]:
        raise AssetError("supporting record requires a nonempty array of passages")
    seen = set()
    for i, passage in enumerate(record["passages"], start=1):
        if (not isinstance(passage, dict) or
                not {"id", "label", "text"} <= passage.keys() or
                passage.keys() - {"id", "label", "text", "timestamp", "model", "thinking_effort", "speaker"}):
            raise AssetError(f"passage {i} must contain id, label, text and optional timestamp, model, thinking_effort or speaker only")
        if "speaker" in passage and (not isinstance(passage["speaker"], str) or passage["speaker"] not in SPEAKERS):
            raise AssetError(f"passage {i} requires an explicit supported speaker role")
        for field in ("model", "thinking_effort"):
            if field in passage and (not isinstance(passage[field], str) or
                                     not ATTRIBUTION_TOKEN.fullmatch(passage[field])):
                raise AssetError(f"passage {i} requires a bounded single-token {field}")
        if "timestamp" in passage and not valid_timestamp(passage["timestamp"]):
            raise AssetError(f"passage {i} requires a valid date or offset-qualified timestamp")
        identifier = passage["id"]
        if not isinstance(identifier, str) or not SLUG.fullmatch(identifier) or identifier in seen:
            raise AssetError(f"passage {i} requires a unique slug id")
        seen.add(identifier)
        if not _text(passage["label"], single_line=True) or not _text(passage["text"]):
            raise AssetError(f"passage {i} requires a single-line label and literal text")

def attribution_labels(passage: dict) -> list[str]:
    return [label + passage[field] for field, label in
            (("model", "Model: "), ("thinking_effort", "Thinking effort: ")) if field in passage]

PRESENTATION = re.compile(r'<(text|row|box|b|strong|i|em)(?:\s[^<>\r\n]*)?>')
DIVIDER = re.compile(r'<divider(?:\s[^<>\r\n]*)?/\s*>')


def _divider_blocks(value, *, inline_only=False):
    """Lift separators through inline wrappers without putting blocks inside spans."""
    def split(inlines):
        chunks = [[]]
        for item in inlines:
            if item.get('t') == 'RawInline' and item['c'][0] == 'press-divider':
                chunks.append([])
                continue
            kind = item.get('t')
            inner = item['c'] if kind in {'Emph', 'Strong', 'Strikeout'} else (
                item['c'][1] if kind in {'Span', 'Link', 'Quoted'} else None)
            pieces = split(inner) if inner is not None else None
            if pieces is None or len(pieces) == 1:
                chunks[-1].append(item)
                continue
            for index, piece in enumerate(pieces):
                if index:
                    chunks.append([])
                if piece:
                    contents = piece if kind in {'Emph', 'Strong', 'Strikeout'} else [
                        item['c'][0], piece, *item['c'][2:]]
                    chunks[-1].append({'t': kind, 'c': contents})
        if len(chunks) > 1:
            for chunk in chunks:
                while chunk and chunk[0].get('t') in {'Space', 'SoftBreak', 'LineBreak'}:
                    chunk.pop(0)
                while chunk and chunk[-1].get('t') in {'Space', 'SoftBreak', 'LineBreak'}:
                    chunk.pop()
        return chunks

    if isinstance(value, list):
        result = []
        for item in value:
            expanded = _divider_blocks(item, inline_only=inline_only)
            if not inline_only and isinstance(item, dict) and item.get('t') in {'Para', 'Plain'}:
                result.extend(expanded)
            else:
                result.append(expanded)
        return result
    if not isinstance(value, dict):
        return value
    if not inline_only and value.get('t') in {'Para', 'Plain'}:
        chunks = split(value['c'])
        blocks = []
        for index, chunk in enumerate(chunks):
            if index:
                blocks.append({'t': 'HorizontalRule'})
            if any(node.get('t') not in {'Space', 'SoftBreak', 'LineBreak'} for node in chunk):
                blocks.append({'t': value['t'], 'c': chunk})
        return blocks
    # Headings and GFM table cells cannot contain block separators.
    if value.get('t') == 'RawInline' and value['c'][0] == 'press-divider':
        return {'t': 'Span', 'c': [['', [], [['title', value['c'][1]]]], [{'t': 'Str', 'c': '—'}]]}
    return {key: _divider_blocks(item, inline_only=inline_only or value.get('t') in {'Header', 'Table'})
            for key, item in value.items()}


def _presentation_pairs(inlines):
    pairs, stack = {}, []
    for index, item in enumerate(inlines):
        raw = item.get('c') if isinstance(item, dict) and item.get('t') == 'RawInline' else None
        if not raw or raw[0] != 'html':
            continue
        opening = PRESENTATION.fullmatch(raw[1]) if len(raw[1]) <= 2000 else None
        closing = re.fullmatch(r'</(text|row|box|b|strong|i|em)>', raw[1])
        if opening:
            stack.append((opening[1], index))
        elif closing:
            if stack and stack[-1][0] == closing[1]:
                _, start = stack.pop()
                pairs[start] = index
            else:
                stack.clear()
    return pairs


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
        if (boundary and re.match(r'<[A-Z][A-Za-z0-9]*\b', line) and
                not re.match(r'<(?:Link|Cite|Icon)\b', line) and line.rstrip('\r\n').endswith('/>')):
            report('unsupported-rich-component-as-literal-code')
            result.append(literal(line)); index += 1
            continue
        result.append(line); index += 1
    return ''.join(result)


def review_gfm(record, *, identity='document', references=None, metadata=None):
    _validate_transcript(record)
    if sum(len(p['text'].encode()) for p in record['passages']) > MAX_RECORD_BYTES:
        raise AssetError('selected message text exceeds 8 MiB')
    diagnostics = {'selection-omissions': 1, 'markdown-normalized-to-gfm': 1}
    references = references or {}
    def report(kind):
        diagnostics[kind] = diagnostics.get(kind, 0) + 1
    def clean(value):
        if isinstance(value, list):
            result = []
            pairs = _presentation_pairs(value)
            index = 0
            while index < len(value):
                original = value[index]
                raw = original.get('c') if isinstance(original, dict) and original.get('t') == 'RawInline' else None
                if index in pairs:
                    opening = PRESENTATION.fullmatch(raw[1])
                    end = pairs[index]
                    contents = clean(value[index + 1:end])
                    if opening[1] in {'b', 'strong', 'i', 'em'}:
                        contents = [{'t': 'Strong' if opening[1] in {'b', 'strong'} else 'Emph', 'c': contents}]
                    title = raw[1] + ' … ' + value[end]['c'][1]
                    item = {'t': 'Span', 'c': [['', [], [['title', title]]], contents]}
                    report('presentation-wrapper-retained-in-tooltip')
                    index = end + 1
                else:
                    item = clean(original)
                    index += 1
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
            if len(value['c'][1]) <= 2000 and DIVIDER.fullmatch(value['c'][1]):
                report('chatgpt-divider-normalized')
                return {'t': 'HorizontalRule'} if kind == 'RawBlock' else {
                    't': 'RawInline', 'c': ['press-divider', value['c'][1]]}
            if re.match(r'<CodeBlock\b', value['c'][1]):
                raise AssetError('ChatGPT CodeBlock must stand alone between blank lines')
            if re.fullmatch(r'<(?:icon|Icon)\b[^<>\r\n]*/>', value['c'][1]) and len(value['c'][1]) <= 2000:
                report('unsupported-icon-retained-in-tooltip')
                span = {'t': 'Span', 'c': [['', [], [['title', value['c'][1]]]], [{'t': 'Str', 'c': '□'}]]}
                return {'t': 'Para', 'c': [span]} if kind == 'RawBlock' else span
            inlines = component_inlines(value['c'][1], references)
            if inlines is not None:
                report('chatgpt-reference-resolved')
                return ({'t': 'Para', 'c': inlines} if kind == 'RawBlock' else
                        {'t': 'Span', 'c': [['', [], []], inlines]})
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
            chunks = []
            position = 0
            for citation in re.finditer('\ue200cite((?:\ue202[^\ue201\ue202]+)+)\ue201', text):
                chunks.append({'t': 'Str', 'c': text[position:citation.start()]})
                resolved = citation_inlines(citation[1].split('\ue202')[1:], references)
                if resolved is None:
                    report('unresolved-chatgpt-citation')
                else:
                    report('chatgpt-reference-resolved')
                chunks.extend(resolved or [{'t': 'Str', 'c': '[Unresolved ChatGPT citation]'}])
                position = citation.end()
            if chunks:
                chunks.append({'t': 'Str', 'c': text[position:]})
                return {'t': 'Span', 'c': [['', [], []], [clean(item) if item['t'] == 'Str' else item for item in chunks]]}
            text, count = re.subn('\ue200[^\ue201]*\ue201', '[Unresolved ChatGPT citation]', text)
            if count:
                report('unresolved-chatgpt-citation')
            if any('\ue000' <= ch <= '\uf8ff' for ch in text):
                report('unsupported-rich-token')
                text = re.sub('[\ue000-\uf8ff]', '[Unsupported token]', text)
            return {'t':'Str','c':text}
        return {key:clean(item) for key,item in value.items()}
    def anchor(identifier, speaker=None):
        annotation = ' data-press-speaker="' + speaker + '"' if speaker else ''
        return {'t':'RawBlock','c':['html',f'\n<a name="{identifier}"{annotation}></a>\n']}
    def header(level,text):
        return {'t':'Header','c':[level,['',[],[]],[{'t':'Str','c':text}]]}
    title = record['title']
    doc_id = 'doc-'+hashlib.sha256(identity.encode()).hexdigest()
    blocks = [anchor(doc_id),header(1,title),anchor('scope-'+doc_id),{'t':'Para','c':[{'t':'Str','c':record['scope']}]}]
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
        blocks.extend([anchor(passage['id'], passage.get('speaker')),header(2,passage['label'])])
        details = []
        if 'timestamp' in passage:
            value = passage['timestamp']
            details.append({'t': 'RawInline', 'c': ['html', '<time datetime=\"'+value+'\">']})
            details.append({'t': 'Str', 'c': timestamp_label(value)})
            details.append({'t': 'RawInline', 'c': ['html', '</time>']})
        labels = attribution_labels(passage)
        if labels:
            details.append({'t': 'Str', 'c': (' · ' if details else '')+' · '.join(labels)})
        if details:
            blocks.extend([anchor('meta-'+passage['id']), {'t': 'Para', 'c': [{'t': 'Emph', 'c': details}]}])
        occurrences = {}
        for block in _divider_blocks(clean(ast['blocks'])):
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
    rendered = result.stdout.rstrip()+'\n'
    if metadata:
        from supporting_metadata import metadata_frontmatter
        rendered = metadata_frontmatter(metadata) + '<a name="metadata-'+doc_id+'"></a>\n\n' + rendered
        diagnostics['document-metadata-fields-added'] = len(metadata)
    data = rendered.encode()
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
    def links(nodes):
        return [(plain_text(node['c'][1]), node['c'][2]) for node in walk(nodes) if node.get('t') == 'Link']
    if links(blocks) != links(parsed['blocks']):
        raise AssetError('GFM conversion would change a link label or destination')
    def tooltips(nodes):
        return [node['c'][0] for node in walk(nodes) if node.get('t') == 'Span' and 'title' in dict(node['c'][0][2])]
    if tooltips(blocks) != tooltips(parsed['blocks']):
        raise AssetError('GFM conversion would change retained presentation markup')
    return data, diagnostics
