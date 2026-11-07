"""One package inventory for builds, draft promotion, offline checks and recovery."""
from __future__ import annotations
import base64
import hashlib
from html import escape
from html.parser import HTMLParser
import io
from pathlib import Path
import re
import zipfile

from asset_policy import AssetError, MAX_BYTES, MAX_HTML_BYTES, MAX_TOTAL_BYTES, AssetPolicy, check_html_size, verify_bytes

MAX_BUNDLE_BYTES = MAX_HTML_BYTES + MAX_TOTAL_BYTES + 128 * 512 + 4096


def bundle_name(manifest):
    return manifest['canonical_artifact'].removesuffix('.html')+'-offline.zip'


def package_names(manifest):
    from identifier_schema import TAG
    match=TAG.fullmatch(manifest.get('tag',''))
    if not match or manifest.get('canonical_artifact')!=match[1]+'-'+match[2]+'.html':
        raise AssetError('invalid canonical package filename')
    names=[manifest['canonical_artifact'],'provenance.json','SHA256SUMS']
    bundle=manifest.get('supplementary_package')
    if bundle is not None:
        if (not isinstance(bundle,dict) or set(bundle)!={'filename','sha256','byte_count'}
                or bundle['filename']!=bundle_name(manifest)
                or not re.fullmatch('[0-9a-f]{64}',str(bundle['sha256']))
                or type(bundle['byte_count']) is not int or not 0 < bundle['byte_count'] <= MAX_BUNDLE_BYTES):
            raise AssetError('invalid supplementary package identity')
        names.append(bundle['filename'])
    return names


def downloads(manifest):
    return [record for record in manifest.get('assets',[]) if record.get('role')=='download']


def offline_html(raw,manifest,payloads):
    """Derive bounded, inert offline downloads without changing canonical HTML."""
    policy=AssetPolicy(manifest['asset_origin'])
    targets={record['source_url']:record['source_url'].removeprefix(policy.origin+'/') for record in downloads(manifest)}
    urls={url:'data:application/octet-stream;base64,'+base64.b64encode(payloads[key]).decode('ascii')
          for url,key in targets.items()}
    text=raw.decode('utf-8');offsets=[0]+[m.end() for m in re.finditer('\n',text)];edits=[];seen=set()
    note='<p lang="en">Offline copy — file downloads use verified bytes included here.</p>'
    size=len(raw)+len(note.encode())
    class Links(HTMLParser):
        def handle_starttag(self,tag,attrs):
            nonlocal size
            url=dict(attrs).get('href')
            if tag!='a' or url not in targets:return
            policy.parse(url)
            original=self.get_starttag_text()
            value='href="'+escape(url)+'"'
            if original.count(value)!=1:raise AssetError('unexpected canonical download link encoding')
            replacement='href="'+urls[url]+'" download="'+Path(targets[url]).name+'"'
            size+=len(replacement)-len(value);check_html_size(size)
            replacement=original.replace(value,replacement,1)
            line,column=self.getpos();edits.append((offsets[line-1]+column,len(original),replacement));seen.add(url)
    parser=Links();parser.feed(text);parser.close()
    if seen!=set(targets):raise AssetError('canonical HTML omits a packaged download')
    chunks=[];position=0
    for start,length,replacement in edits:
        chunks.extend((text[position:start],replacement));position=start+length
    chunks.append(text[position:]);text=''.join(chunks)
    marker='<body>'
    if text.count(marker)!=1:raise AssetError('unexpected canonical body for offline package')
    result=text.replace(marker,marker+note,1).encode()
    check_html_size(len(result))
    return result


def required_payloads(manifest):
    """Object identity is exact bytes, length and media policy; URLs locate reviewed sources."""
    records=downloads(manifest)
    if not records:return {}
    policy=AssetPolicy(manifest['asset_origin'])
    entries={}
    for record in records:
        digest,extension=policy.parse(record['source_url'])
        if record['sha256']!=digest or record['byte_count']>MAX_BYTES:
            raise AssetError('invalid supplementary asset identity')
        key='sha256/'+digest+'.'+extension
        if key in entries:raise AssetError('duplicate supplementary asset')
        entries[key]=record
    return entries


def create_bundle(directory,raw_html,manifest,source):
    entries=required_payloads(manifest)
    if not entries:return None
    content={}
    for key,record in entries.items():
        path=source/key
        if not path.is_file() or path.resolve()!=path.absolute():raise AssetError('missing supplementary source bytes')
        with path.open('rb') as stream:raw=stream.read(MAX_BYTES+1)
        if len(raw)!=record['byte_count'] or hashlib.sha256(raw).hexdigest()!=record['sha256']:
            raise AssetError('supplementary source bytes changed after verification')
        content[key]=raw
    content['index.html']=offline_html(raw_html,manifest,content)
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(content):
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.create_system=3
            info.external_attr=0o100644<<16
            archive.writestr(info,content[name])
    raw=output.getvalue()
    if len(raw)>MAX_BUNDLE_BYTES:raise AssetError('supplementary package exceeds bounded size')
    name=bundle_name(manifest);(directory/name).write_bytes(raw)
    return {'filename':name,'sha256':hashlib.sha256(raw).hexdigest(),'byte_count':len(raw)}


def verify_bundle(directory,raw_html,manifest):
    entries=required_payloads(manifest) if downloads(manifest) else {}
    bundle=manifest.get('supplementary_package')
    package_names(manifest)
    if bool(bundle)!=bool(entries):raise AssetError('every download requires an immutable supplementary package')
    if not bundle:return {}
    path=directory/bundle['filename']
    if path.stat().st_size!=bundle['byte_count']:raise AssetError('supplementary package size differs from provenance')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=bundle['sha256']:raise AssetError('supplementary package digest differs from provenance')
    result={}
    index=None
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members=archive.infolist()
            expected={'index.html',*entries}
            if len(members)!=len(expected) or {m.filename for m in members}!=expected:
                raise AssetError('supplementary ZIP inventory differs from reviewed assets')
            for member in members:
                size=MAX_HTML_BYTES if member.filename=='index.html' else entries[member.filename]['byte_count']
                if (member.compress_type!=zipfile.ZIP_STORED or member.flag_bits&1
                        or member.external_attr>>16&0o170000!=0o100000 or member.file_size>size
                        or (member.filename!='index.html' and member.file_size!=size)):
                    raise AssetError('supplementary ZIP has unsafe or oversized members')
                with archive.open(member) as stream:content=stream.read(size+1)
                if member.filename=='index.html':
                    index=content
                else:
                    record=entries[member.filename]
                    extension=member.filename.rsplit('.',1)[1]
                    if len(content)!=record['byte_count'] or hashlib.sha256(content).hexdigest()!=record['sha256']:
                        raise AssetError('supplementary bytes differ from reviewed asset')
                    verify_bytes(content,extension,record['media_type']);result[member.filename]=content
    except (zipfile.BadZipFile,RuntimeError,NotImplementedError) as exc:
        raise AssetError('invalid supplementary ZIP') from exc
    if index!=offline_html(raw_html,manifest,result):raise AssetError('offline reading copy differs from canonical derivation')
    return result
