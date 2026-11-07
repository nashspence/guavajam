"""Convert selected exported message text to an explicitly anchored review draft."""
import hashlib
import json
import re
import subprocess

from asset_policy import AssetError
from render_profile import plain_text


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
            return [clean(x) for x in value]
        if not isinstance(value, dict):
            return value
        kind = value.get('t')
        if kind in {'Code', 'CodeBlock', 'Math'}:
            return value  # Never rewrite citations, math delimiters or HTML in code.
        if kind in {'RawBlock', 'RawInline'}:
            report('raw-markup-as-literal-code')
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
            'markdown+raw_html+tex_math_dollars+tex_math_single_backslash-smart-yaml_metadata_block-implicit_header_references','--to','json'],
            input=text,capture_output=True,text=True)
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
        '--to=gfm+tex_math_dollars-tex_math_gfm','--wrap=none'],input=json.dumps({
            'pandoc-api-version':api,'meta':{},'blocks':blocks}),capture_output=True,text=True)
    if result.returncode:
        raise AssetError('ChatGPT GFM writing failed or warned')
    return (result.stdout.rstrip()+'\n').encode(), diagnostics
