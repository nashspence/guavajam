"""Versioned offline packages; content identity is independent of ZIP transport."""
from __future__ import annotations
import base64
from contextlib import contextmanager
import hashlib
from html import escape
from html.parser import HTMLParser
from pathlib import Path
import re
import tempfile
import zipfile

from asset_policy import (AssetError, MAX_ASSETS, MAX_BYTES, MAX_HTML_BYTES, MAX_RECORD_BYTES,
                          MAX_TOTAL_BYTES, MIME, AssetPolicy, check_html_size, verify_bytes)

V1 = 'press.offline.v1'
V2 = 'press.offline.v2'
V3 = 'press.offline.v3'
COMPLETE_FORMATS = frozenset({V2, V3})
CURRENT_FORMAT = V3
MAX_SUMS_BYTES = 32 * 1024
MAX_BUNDLE_BYTES = 2 * MAX_HTML_BYTES + MAX_TOTAL_BYTES + MAX_RECORD_BYTES + MAX_SUMS_BYTES + 128 * 512 + 4096
V1_MAX_BUNDLE_BYTES = MAX_HTML_BYTES + MAX_TOTAL_BYTES + 128 * 512 + 4096


def bundle_name(manifest):
    return manifest['canonical_artifact'].removesuffix('.html')+'-offline.zip'


def bundle_format(manifest):
    return manifest.get('supplementary_package',{}).get('format',V1)


def package_names(manifest):
    from identifier_schema import TAG
    match=TAG.fullmatch(manifest.get('tag',''))
    if not match or manifest.get('canonical_artifact')!=match[1]+'-'+match[2]+'.html':
        raise AssetError('invalid canonical package filename')
    names=[manifest['canonical_artifact'],'provenance.json','SHA256SUMS']
    bundle=manifest.get('supplementary_package')
    if bundle is not None:
        if not isinstance(bundle,dict) or bundle.get('filename')!=bundle_name(manifest):
            raise AssetError('invalid supplementary package identity')
        if bundle_format(manifest)==V1:
            fields={'filename','sha256','byte_count'}
            valid=(re.fullmatch('[0-9a-f]{64}',str(bundle.get('sha256',''))) and
                   type(bundle.get('byte_count')) is int and 0<bundle['byte_count']<=V1_MAX_BUNDLE_BYTES)
        elif bundle_format(manifest) in COMPLETE_FORMATS:
            fields={'filename','format','reading_copy_sha256','reading_copy_byte_count'}
            valid=(re.fullmatch('[0-9a-f]{64}',str(bundle.get('reading_copy_sha256',''))) and
                   type(bundle.get('reading_copy_byte_count')) is int and 0<bundle['reading_copy_byte_count']<=MAX_HTML_BYTES)
        else:
            raise AssetError('unsupported offline package format')
        if set(bundle)!=fields or not valid:raise AssetError('invalid supplementary package identity')
        names.append(bundle['filename'])
    return names


def bundle_limit(manifest):
    package_names(manifest)
    return manifest['supplementary_package']['byte_count'] if bundle_format(manifest)==V1 else MAX_BUNDLE_BYTES


def check_bundle_size(manifest,size):
    if type(size) is not int or not 0<size<=bundle_limit(manifest):
        raise AssetError('supplementary release attachment size differs from reviewed package or exceeds its bound')
    if bundle_format(manifest)==V1 and size!=bundle_limit(manifest):
        raise AssetError('supplementary package size differs from provenance')


def downloads(manifest):
    return [record for record in manifest.get('assets',[]) if record.get('role')=='download']


def _offline_v1(raw,manifest,payloads):
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


def _offline_v3(raw,manifest,payloads):
    rendered=_offline_v1(raw,manifest,payloads)
    opening=b'<aside aria-label="Offline copy">';closing=b'</aside>'
    check_html_size(len(rendered)+len(opening)+len(closing))
    start=rendered.index(b'<body>')+len(b'<body>')
    end=rendered.index(b'</p>',start)+len(b'</p>')
    return rendered[:start]+opening+rendered[start:end]+closing+rendered[end:]


# These transformations are frozen by fixtures; future formats get a new entry.
OFFLINE_FORMATS = {V1: _offline_v1, V2: _offline_v1, V3: _offline_v3}


def offline_html(raw,manifest,payloads):
    try:renderer=OFFLINE_FORMATS[bundle_format(manifest)]
    except KeyError:raise AssetError('unsupported offline package format') from None
    return renderer(raw,manifest,payloads)


def download_attribute_size(byte_count,key):
    return len('href="data:application/octet-stream;base64," download="'+Path(key).name+'"')+4*((byte_count+2)//3)


def required_payloads(manifest):
    """Exact-byte object identities and their reviewed source locations."""
    records=downloads(manifest)
    if not records:return {}
    if len(records)>MAX_ASSETS:raise AssetError('too many supplementary assets')
    policy=AssetPolicy(manifest['asset_origin']);entries={};total=0
    for record in records:
        digest,extension=policy.parse(record['source_url'])
        if (record.get('sha256')!=digest or type(record.get('byte_count')) is not int
                or not 0<=record['byte_count']<=MAX_BYTES
                or record.get('media_type')!=MIME.get(extension,'application/octet-stream')):
            raise AssetError('invalid supplementary asset identity')
        key='sha256/'+digest+'.'+extension
        if key in entries:raise AssetError('duplicate supplementary asset')
        entries[key]=record;total+=record['byte_count']
    if total>MAX_TOTAL_BYTES:raise AssetError('supplementary assets exceed total byte bound')
    return entries


def stage_supplements(raw_html,manifest,source):
    entries=required_payloads(manifest)
    if not entries:return {}
    content={}
    for key,record in entries.items():
        path=source/key
        if not path.is_file() or path.resolve()!=path.absolute():raise AssetError('missing supplementary source bytes')
        with path.open('rb') as stream:raw=stream.read(MAX_BYTES+1)
        if len(raw)!=record['byte_count'] or hashlib.sha256(raw).hexdigest()!=record['sha256']:
            raise AssetError('supplementary source bytes changed after verification')
        content[key]=raw
    manifest['supplementary_package']={'filename':bundle_name(manifest),'format':CURRENT_FORMAT}
    index=offline_html(raw_html,manifest,content)
    manifest['supplementary_package'].update(reading_copy_sha256=hashlib.sha256(index).hexdigest(),reading_copy_byte_count=len(index))
    return {'index.html':index,**content}


def checksums(manifest,content):
    names=package_names(manifest)[:2]
    if 'supplementary_package' in manifest:
        if bundle_format(manifest)==V1:names.append(bundle_name(manifest))
        else:names+=['index.html',*sorted(required_payloads(manifest))]
    return ''.join(hashlib.sha256(content[name]).hexdigest()+'  '+name+'\n' for name in names).encode('ascii')


def write_bundle(directory,manifest,content):
    path=directory/bundle_name(manifest)
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(content):
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.create_system=3;info.external_attr=0o100644<<16
            archive.writestr(info,content[name])
    check_bundle_size(manifest,path.stat().st_size)


def _limits(manifest):
    entries=required_payloads(manifest)
    limits={key:record['byte_count'] for key,record in entries.items()}
    limits['index.html']=MAX_HTML_BYTES
    if bundle_format(manifest) in COMPLETE_FORMATS:
        limits['index.html']=manifest['supplementary_package']['reading_copy_byte_count']
        limits.update({manifest['canonical_artifact']:MAX_HTML_BYTES,'provenance.json':MAX_RECORD_BYTES,'SHA256SUMS':MAX_SUMS_BYTES})
    return limits


def _read_file(path,limit):
    if path.is_symlink() or not path.is_file():raise AssetError('edition files must be regular files without symlinks')
    if path.stat().st_size>limit:raise AssetError('edition file exceeds its bounded size')
    with path.open('rb') as stream:raw=stream.read(limit+1)
    if len(raw)>limit:raise AssetError('edition file exceeds its bounded size')
    return raw


def read_bundle(path,manifest):
    package_names(manifest)
    if path.is_symlink() or not path.is_file():raise AssetError('supplementary ZIP must be a regular file')
    check_bundle_size(manifest,path.stat().st_size)
    limits=_limits(manifest)
    try:
        with zipfile.ZipFile(path) as archive:
            members=archive.infolist()
            if len(members)!=len(limits) or {m.filename for m in members}!=set(limits):
                raise AssetError('supplementary ZIP inventory differs from reviewed assets')
            content={}
            for member in members:
                size=limits[member.filename]
                if (member.compress_type!=zipfile.ZIP_STORED or member.flag_bits&1
                        or member.external_attr>>16&0o170000!=0o100000 or member.file_size>size):
                    raise AssetError('supplementary ZIP has unsafe or oversized members')
                with archive.open(member) as stream:content[member.filename]=stream.read(size+1)
                if len(content[member.filename])>size:raise AssetError('supplementary ZIP member exceeds size bound')
    except (zipfile.BadZipFile,RuntimeError,NotImplementedError) as exc:
        raise AssetError('invalid supplementary ZIP') from exc
    return content


def package_contents(directory,manifest):
    names=package_names(manifest);actual={p.name for p in directory.iterdir()}
    core_limits={names[0]:MAX_HTML_BYTES,'provenance.json':MAX_RECORD_BYTES if bundle_format(manifest) in COMPLETE_FORMATS else MAX_HTML_BYTES,'SHA256SUMS':MAX_SUMS_BYTES}
    if actual==set(names):
        content={name:_read_file(directory/name,limit) for name,limit in core_limits.items()}
        if 'supplementary_package' in manifest:
            path=directory/bundle_name(manifest)
            if bundle_format(manifest)==V1:content[path.name]=_read_file(path,bundle_limit(manifest))
            else:
                extra=read_bundle(path,manifest)
                if any(extra[name]!=content[name] for name in core_limits):raise AssetError('ZIP core files differ from release attachments')
                content.update(extra)
        return content
    if (bundle_format(manifest) in COMPLETE_FORMATS and 'supplementary_package' in manifest
            and actual==set(core_limits)|{'index.html','sha256'}):
        objects=directory/'sha256'
        if objects.is_symlink() or not objects.is_dir():raise AssetError('offline objects require a regular directory')
        expected={Path(key).name for key in required_payloads(manifest)}
        if {p.name for p in objects.iterdir()}!=expected:raise AssetError('offline object inventory differs from provenance')
        return {name:_read_file(directory/name,limit) for name,limit in _limits(manifest).items()}
    raise AssetError('edition must contain exactly HTML, provenance.json, SHA256SUMS and its declared supplements')


def verify_bundle(directory,raw_html,manifest,*,content=None):
    entries=required_payloads(manifest);bundle=manifest.get('supplementary_package');package_names(manifest)
    if bool(bundle)!=bool(entries):raise AssetError('every download requires an immutable supplementary package')
    if not bundle:return {}
    if content is None:content=package_contents(directory,manifest)
    if bundle_format(manifest)==V1:
        raw=content[bundle_name(manifest)]
        if len(raw)!=bundle['byte_count'] or hashlib.sha256(raw).hexdigest()!=bundle['sha256']:
            raise AssetError('supplementary package digest or size differs from provenance')
        content=read_bundle(directory/bundle_name(manifest),manifest)
    payloads={}
    for key,record in entries.items():
        raw=content[key]
        if len(raw)!=record['byte_count'] or hashlib.sha256(raw).hexdigest()!=record['sha256']:
            raise AssetError('supplementary bytes differ from reviewed asset')
        verify_bytes(raw,key.rsplit('.',1)[1],record['media_type']);payloads[key]=raw
    index=content['index.html']
    if bundle_format(manifest) in COMPLETE_FORMATS and (len(index)!=bundle['reading_copy_byte_count'] or hashlib.sha256(index).hexdigest()!=bundle['reading_copy_sha256']):
        raise AssetError('offline reading copy differs from provenance')
    if index!=offline_html(raw_html,manifest,payloads):raise AssetError('offline reading copy differs from canonical derivation')
    return payloads


@contextmanager
def edition_directory(path):
    """Open the complete ZIP as bounded data; never execute an archived builder."""
    if path.is_dir():
        yield path;return
    if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_BUNDLE_BYTES:
        raise AssetError('expected an edition directory or bounded complete offline ZIP')
    import json
    try:
        with zipfile.ZipFile(path) as archive:
            entry=archive.getinfo('provenance.json')
            if entry.file_size>MAX_RECORD_BYTES:raise AssetError('oversized offline provenance')
            with archive.open(entry) as stream:manifest=json.loads(stream.read(MAX_RECORD_BYTES+1))
    except (zipfile.BadZipFile,KeyError,RuntimeError) as exc:
        raise AssetError('complete offline ZIP requires its provenance') from exc
    package_names(manifest)
    if bundle_format(manifest) not in COMPLETE_FORMATS:raise AssetError('this offline ZIP requires the separately saved core release files')
    content=read_bundle(path,manifest)
    with tempfile.TemporaryDirectory(prefix='press-offline-') as temp:
        directory=Path(temp)
        for name,raw in content.items():
            target=directory/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        yield directory
