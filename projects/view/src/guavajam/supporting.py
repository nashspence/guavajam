from guavajam_core.source_ast import walk, plain_text
from guavajam_core.supporting_gfm import timestamp_label

def present(ast, positions):
    """Put source anchors on their own semantic elements, preserving quoted turns."""
    from html import escape
    indexed={position:key for key,position in positions.items()}
    result=[];turn=None
    for index,block in enumerate(ast['blocks']):
        anchor=indexed.get(index)
        if block['t']=='Div' and 'press-reviewed-turn-start' in block['c'][0][1]:
            turn=dict(block['c'][0][2]);turn['anchor']=anchor
            result.extend([{'t':'RawBlock','c':['html','<figure class="press-turn" id="'+escape(anchor)+'" data-press-speaker="'+turn['data-press-speaker']+'">']},
                           {'t':'RawBlock','c':['html','<blockquote class="press-turn-quotation">']}]);continue
        if block['t']=='Div' and 'press-reviewed-turn-end' in block['c'][0][1]:
            result.append({'t':'RawBlock','c':['html','</blockquote><figcaption class="press-turn-attribution">'+escape('— '+turn['data-press-attribution'])+' <a class="press-permalink" aria-label="Permalink to attributed turn" href="#'+escape(turn['anchor'])+'">¶</a></figcaption></figure>']});turn=None;continue
        if anchor:
            link={'t':'Link','c':[['',['press-permalink'],[['aria-label','Permalink to passage '+anchor],['lang','en']]],[{'t':'Str','c':'¶'}],['#'+anchor,'']]}
            if block['t']=='Header':
                block['c'][1][0]=anchor;block['c'][2].extend([{'t':'Space'},link])
            else:
                metadata=block['t']=='Para' and len(block['c'])==1 and block['c'][0]['t']=='Emph' and (
                    any(n.get('t')=='Span' and 'data-press-datetime' in dict(n['c'][0][2]) for n in walk(block))
                    or plain_text(block).startswith(('Model: ','Thinking effort: ')))
                if metadata:block['c']=block['c'][0]['c']
                cls='press-passage-datetime' if metadata else 'press-supporting-element'
                if block['t']=='Div' and 'press-document-metadata' in block['c'][0][1]:
                    block['c'][0][0]=anchor;block['c'][0][2].extend([['role','group'],['aria-label','Document metadata']])
                    block['c'][1].insert(0,{'t':'Para','c':[{'t':'Str','c':'Document metadata'},{'t':'Space'},link]})
                else:
                    if block['t'] in {'Para','Plain'}:block['c'].extend([{'t':'Space'},link]);children=[block]
                    else:children=[block,{'t':'Para','c':[link]}]
                    block={'t':'Div','c':[[anchor,[cls],[]],children]}
        result.append(block)
    ast['blocks']=result
    for node in walk(ast['blocks']):
        if node['t']=='Span' and 'data-press-datetime' in dict(node['c'][0][2]):
            instant=dict(node['c'][0][2])['data-press-datetime'];node.clear();node.update(t='RawInline',c=['html','<time datetime="'+escape(instant,quote=True)+'">'+escape(timestamp_label(instant))+'</time>'])
    from .presentation import wrap_reading_blocks
    return wrap_reading_blocks(ast)
