"""Keep source fragment identities separate from optional viewer-generated IDs."""
from html.parser import HTMLParser
import re
from .asset_policy import AssetError


def writer_prefix(anchors):
    prefix='guavajam-view-';number=1
    while any(anchor.startswith(prefix) for anchor in anchors):
        number+=1;prefix='guavajam-view-'+str(number)+'-'
    return prefix


def restore_source_ids(rendered,anchors,prefix):
    offsets=[0]+[m.end() for m in re.finditer('\n',rendered)];edits=[]
    class SourceIDs(HTMLParser):
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs);raw=self.get_starttag_text();changed=raw
            classes=set(attrs.get('class','').split());identifier=attrs.get('id','')
            source_element=(tag in {'h1','h2','h3','h4','h5','h6'} or tag=='div' and bool(classes&{'press-supporting-element','press-passage-datetime','press-document-metadata'}))
            if source_element and identifier.startswith(prefix) and identifier[len(prefix):] in anchors:
                changed=changed.replace('id="'+identifier+'"','id="'+identifier[len(prefix):]+'"',1)
            href=attrs.get('href','')
            if tag=='a' and not classes&{'footnote-ref','footnote-back'} and attrs.get('aria-hidden')!='true' and href.startswith('#'+prefix) and href[len(prefix)+1:] in anchors:
                changed=changed.replace('href="'+href+'"','href="#'+href[len(prefix)+1:]+'"',1)
            if changed!=raw:
                line,column=self.getpos();start=offsets[line-1]+column;edits.append((start,start+len(raw),changed))
    parser=SourceIDs();parser.feed(rendered)
    for start,end,changed in reversed(edits):rendered=rendered[:start]+changed+rendered[end:]
    ids=element_ids(rendered)
    if len(ids)!=len(set(ids)):raise AssetError('derived view contains duplicate element identities')
    return rendered


def element_ids(rendered):
    ids=[]
    class IDs(HTMLParser):
        def handle_starttag(self,tag,attrs):
            identifier=dict(attrs).get('id')
            if identifier:ids.append(identifier)
    parser=IDs();parser.feed(rendered);return ids
