from guavajam.source_ast import walk, plain_text
from guavajam.supporting_gfm import timestamp_label
from .icons import link_icon, inline_icon

def present(ast, positions, navigation=None):
    """Put source anchors on their own semantic elements, preserving quoted turns."""
    from html import escape
    from .navigation import contents_blocks, extract
    navigation = navigation if navigation is not None else extract(ast, positions)
    indexed={position:key for key,position in positions.items()}
    result=[];turn=None
    for index,block in enumerate(ast['blocks']):
        if index == 0:
            continue  # The citation presents the metadata title.
        anchor=indexed.get(index)
        if block['t']=='Div' and 'guavajam-reviewed-turn-start' in block['c'][0][1]:
            turn=dict(block['c'][0][2]);turn['anchor']=anchor
            result.extend([{'t':'RawBlock','c':['html','<figure class="guavajam-turn" id="'+escape(anchor)+'" data-guavajam-speaker="'+turn['data-guavajam-speaker']+'">']},
                           {'t':'RawBlock','c':['html','<blockquote class="guavajam-turn-quotation">']}]);continue
        if block['t']=='Div' and 'guavajam-reviewed-turn-end' in block['c'][0][1]:
            result.append({'t':'RawBlock','c':['html','</blockquote><figcaption class="guavajam-turn-attribution">'+escape('— '+turn['data-guavajam-attribution'])+' <a class="guavajam-permalink" aria-label="Permalink to attributed turn" href="#'+escape(turn['anchor'])+'">'+link_icon('hash', 'Permalink')+'</a></figcaption></figure>']});turn=None;continue
        if anchor:
            link={'t':'Link','c':[['',['guavajam-permalink'],[['aria-label','Permalink to passage '+anchor],['lang','en']]],[inline_icon('hash', 'Permalink')],['#'+anchor,'']]}
            if block['t']=='Header':
                block['c'][1][0]=anchor;block['c'][2].extend([{'t':'Space'},link])
            else:
                metadata=block['t']=='Para' and len(block['c'])==1 and block['c'][0]['t']=='Emph' and (
                    any(n.get('t')=='Span' and 'data-guavajam-datetime' in dict(n['c'][0][2]) for n in walk(block))
                    or plain_text(block).startswith(('Model: ','Thinking effort: ')))
                if metadata:block['c']=block['c'][0]['c']
                cls='guavajam-passage-datetime' if metadata else 'guavajam-supporting-element'
                if block['t']=='Div' and 'guavajam-document-metadata' in block['c'][0][1]:
                    block['c'][0][0]=anchor;block['c'][0][2].extend([['role','group'],['aria-label','Document metadata']])
                    block['c'][1].insert(0,{'t':'Para','c':[{'t':'Str','c':'Document metadata'},{'t':'Space'},link]})
                else:
                    if block['t'] in {'Para','Plain'}:block['c'].extend([{'t':'Space'},link]);children=[block]
                    else:children=[block,{'t':'Para','c':[link]}]
                    block={'t':'Div','c':[[anchor,[cls],[]],children]}
        result.append(block)
    contents = contents_blocks(navigation['anchors'], positions)
    if contents:
        # Keep the literal document metadata beside its title, ahead of the TOC.
        insertion = 1 if result and result[0].get('t') == 'Div' and 'guavajam-document-metadata' in result[0]['c'][0][1] else 0
        result[insertion:insertion] = contents
    ast['blocks']=result
    for node in walk(ast['blocks']):
        if node['t']=='Span' and 'data-guavajam-datetime' in dict(node['c'][0][2]):
            instant=dict(node['c'][0][2])['data-guavajam-datetime'];node.clear();node.update(t='RawInline',c=['html','<time datetime="'+escape(instant,quote=True)+'">'+escape(timestamp_label(instant))+'</time>'])
    from .presentation import wrap_reading_blocks
    return wrap_reading_blocks(ast)
