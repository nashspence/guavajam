"""Render a pinned Press-convention Git tree as a disposable, script-free view.

No GitHub API, Press source import, B2 credentials, runtime JavaScript, or
release identity is required. Callers explicitly supply an independently
trusted revision/signature policy, or request an unmistakable draft preview.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
from html import escape
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from urllib.parse import urlsplit

from .media import asset, declarations, MediaError, SLUG

READER = 'gfm+raw_html+tex_math_dollars+yaml_metadata_block'
REV = re.compile(r'[0-9a-f]{40}\Z')
RECORD = re.compile(r'works/[a-z0-9]+(?:-[a-z0-9]+)*/(?:manuscript\.md|supporting/[a-z0-9]+(?:-[a-z0-9]+)*\.md)\Z')
IMAGE = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'webp': 'image/webp', 'svg': 'image/svg+xml'}


class RenderError(ValueError):
    pass


def process(command, *, cwd=None, data=None, limit=16 * 1024 * 1024):
    try:
        result = subprocess.run(command, cwd=cwd, input=data, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=45, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RenderError('required Git/Pandoc command failed or timed out') from exc
    if result.returncode or len(result.stdout) > limit or len(result.stderr) > 65536:
        raise RenderError('Git/Pandoc rejected the pinned source or output budget')
    return result.stdout


def revision(repo, sha):
    if not REV.fullmatch(sha):
        raise RenderError('use a full 40-character lowercase SHA-1 commit, not a tag or branch')
    if process(['git', '--no-replace-objects', 'rev-parse', '--verify', sha + '^{commit}'], cwd=repo).strip().decode() != sha:
        raise RenderError('revision must resolve to itself')
    process(['git','--no-replace-objects','fsck','--strict','--no-reflogs','--no-dangling',sha],cwd=repo)
    return sha


def git_blob(repo, sha, path, *, maximum=8 * 1024 * 1024):
    if not RECORD.fullmatch(path):
        raise RenderError('source path is outside the Press manuscript/supporting convention')
    mode = process(['git', '--no-replace-objects', 'ls-tree', sha, '--', path], cwd=repo)
    if not mode.startswith(b'100644 blob ') and not mode.startswith(b'100755 blob '):
        raise RenderError('rendering requires committed regular files, never symlinks')
    return process(['git', '--no-replace-objects', 'show', sha + ':' + path], cwd=repo, limit=maximum)


def git_extra(repo, sha, path, *, maximum=10 * 1024 * 1024):
    if (not path.startswith('works/') or '..' in PurePosixPath(path).parts
            or not re.fullmatch(r'works/[a-z0-9-]+/assets/[a-z0-9._/-]+', path)):
        raise RenderError('local image must remain within a work assets directory')
    mode = process(['git', '--no-replace-objects', 'ls-tree', sha, '--', path], cwd=repo)
    if not mode.startswith(b'100644 blob ') and not mode.startswith(b'100755 blob '):
        raise RenderError('rendering requires committed regular files, never symlinks')
    return process(['git', '--no-replace-objects', 'show', sha + ':' + path], cwd=repo, limit=maximum)


def pandoc(ast_or_markdown: bytes, *, reader=READER, writer='json', id_prefix=None):
    return process(['pandoc', '--from=' + reader, '--to=' + writer, '--wrap=none', '--mathml', *(['--id-prefix='+id_prefix] if id_prefix else [])],
                   data=ast_or_markdown, limit=32 * 1024 * 1024)


def _walk(obj):
    if isinstance(obj, dict):
        if isinstance(obj.get('t'), str): yield obj
        for value in obj.values(): yield from _walk(value)
    elif isinstance(obj, list):
        for value in obj: yield from _walk(value)


def _text(nodes):
    bits = []
    for node in _walk(nodes):
        if node.get('t') == 'Str': bits.append(node['c'])
        elif node.get('t') in {'Space', 'SoftBreak', 'LineBreak'}: bits.append(' ')
    return ''.join(bits).strip()






def _make_media(item):
    kind = item['kind']; ident = escape(item['id'], quote=True)
    attrs = f'controls playsinline preload="metadata" aria-label="{escape(item['title'], quote=True)}"' if kind == 'video' else f'controls crossorigin="anonymous" preload="none" aria-label="{escape(item['title'], quote=True)}"'
    if kind == 'video':
        attrs += ' crossorigin="anonymous" poster="' + escape(item['poster'], quote=True) + '"'
    source = escape(item['src'], quote=True)
    mime = 'video/mp4' if kind == 'video' else 'audio/mpeg'
    out = ['<figure class="press-media">', f'<{kind} {attrs}>',
           f'<source src="{source}" type="{mime}">']
    if 'captions' in item:
        out.append('<track kind="captions" src="' + escape(item['captions'], quote=True) +
                   '" srclang="' + escape(item['language'], quote=True) +
                   '" label="' + escape(item['label'], quote=True) + '" default>')
    out += [f'</{kind}>']
    if kind == 'video':
        out.append('<img class="press-media-poster" src="' + escape(item['poster'], quote=True) + '" alt="">')
    out += [f'<figcaption>{escape(item["title"])} '
            f'<a href="{escape(item["transcript"], quote=True)}">Transcript</a> '
            f'<a href="{source}">Download {kind}</a></figcaption>', '</figure>']
    return '\n'.join(out)


def _href(url, *, kind, path, sha, repo, origin, supporting):
    parsed = urlsplit(url)
    if parsed.scheme:
        if parsed.scheme == 'mailto' and kind == 'Link' and parsed.netloc == '' and parsed.path and not parsed.query:
            return url
        if parsed.scheme != 'https' or parsed.username or parsed.password:
            raise RenderError('links must be HTTPS or mailto, without credentials')
        if kind == 'Image':
            asset(url, origin, set(IMAGE))
        return url
    if parsed.netloc or parsed.query or url.startswith(('/', '\\')) or '\\' in url:
        raise RenderError('absolute, ambiguous or query-string local links prohibited')
    if not parsed.path:
        if parsed.fragment and re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',parsed.fragment): return '#' + parsed.fragment
        raise RenderError('invalid local fragment')
    relative = PurePosixPath(path).parent
    resolved = relative.joinpath(parsed.path)
    # A lexical POSIX path resolver that rejects traversal outside the work.
    parts = []
    for component in resolved.parts:
        if component == '..':
            if not parts: raise RenderError('traversal in document links')
            parts.pop()
        elif component not in {'.', ''}: parts.append(component)
    dest = '/'.join(parts)
    if dest in supporting and kind == 'Link':
        if parsed.fragment and parsed.fragment not in supporting[dest]:raise RenderError('unresolved supporting source fragment')
        href = ('supporting/' if path.endswith('/manuscript.md') else '') + PurePosixPath(dest).stem + '.html'
        return href + (('#' + parsed.fragment) if parsed.fragment else '')
    if kind == 'Image' and dest.startswith('works/' + path.split('/')[1] + '/assets/'):
        ext = PurePosixPath(dest).suffix.removeprefix('.')
        if ext not in IMAGE: raise RenderError('only passive local PNG/JPEG/WebP images are supported')
        raw = git_extra(repo, sha, dest)
        if len(raw) > 10 * 1024 * 1024 or not raw: raise RenderError('local image too large')
        return 'data:' + IMAGE[ext] + ';base64,' + base64.b64encode(raw).decode('ascii')
    raise RenderError('unresolved local link; only committed supporting pages and images are rendered')


def _transform(ast, *, path, sha, repo, origin, supporting, media=True, source=None):
    from .source import validate_front_matter,validate_work_fields,validate_pandoc_front_matter,inspect_manuscript_syntax,walk,plain_text
    from .table_captions import normalize_table_captions
    from .presentation import prepare_presentation_ast
    if media:
        front,body,line,errors=validate_front_matter(Path(path),source)
        info,issues=inspect_manuscript_syntax(Path(path),body,line);errors.extend(issues)
        heads=[n for n in walk(ast['blocks']) if n['t']=='Header']
        if front is not None:
            errors.extend(validate_work_fields(Path(path),front,path.split('/')[1],heads[0]['c'][2] if heads else []))
            errors.extend(validate_pandoc_front_matter(Path(path),front,ast))
        if errors:raise RenderError('; '.join(errors[:8]))
        anchors=info['anchors'];source_title=front['title']
        raw=[n['c'][1] for n in walk(ast['blocks']) if n['t'] in {'RawInline','RawBlock'} and n['c'][0]=='html']
        expected=[part for anchor in anchors for part in ('<a id="'+anchor+'">','</a>')]
        if sorted(raw)!=sorted(expected):raise RenderError('unreviewed raw HTML in manuscript')
        ast,errors=normalize_table_captions(ast,source,Path(path),lambda text:json.loads(pandoc(text.encode())))
        if errors:raise RenderError('; '.join(errors))
        items=declarations(ast,origin,anchors)
        for block in ast['blocks']:
            if block.get('t')=='CodeBlock' and 'press-media' in block['c'][0][1]:
                from .media import decode
                item=decode(block['c'][1],origin);block.clear();block.update(t='RawBlock',c=['html',_make_media(item)])
        ast=prepare_presentation_ast(ast)
        from .bibliography import ordered_authors,author_text
        byline=author_text(ordered_authors(front),identifiers=True)
        title_index=next(i for i,b in enumerate(ast['blocks']) if b['t']=='Header' and b['c'][0]==1)
        ast['blocks'].insert(title_index+1,{'t':'Para','c':[{'t':'Span','c':[['',['press-edition-identity'],[]],[{'t':'Str','c':byline}]]}]})
        if front.get('abstract'):
            ast['blocks'].insert(title_index+2,{'t':'Div','c':[['',['guavajam-abstract'],[['role','group'],['aria-label','Abstract']]],[{'t':'Para','c':[{'t':'Strong','c':[{'t':'Str','c':'Abstract'}]}]},{'t':'Para','c':[{'t':'Str','c':front['abstract']}]}]]})
    else:
        from .supporting_gfm import parse_gfm_record,present
        from .asset_policy import AssetPolicy
        ast,positions=parse_gfm_record(source,policy=AssetPolicy(origin) if origin else None);anchors=set(positions);items=[]
        source_title=plain_text(ast['blocks'][0]['c'][2])
        ast=present(ast,positions)
    for node in _walk(ast['blocks']):
        if node['t'] in ('Link','Image'):
            url=node['c'][2][0]
            if url.startswith('#') and url[1:] not in anchors :raise RenderError('unresolved explicit source fragment')
            node['c'][2][0]=_href(url,kind=node['t'],path=path,sha=sha,repo=repo,origin=origin,supporting=supporting)
    return ast,items,anchors,source_title


def _html(*, title, language, revision_sha, path, body, css, origin, preview, facts=None):
    if not re.fullmatch(r'[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*', language):
        language = 'en'
    if '</style' in css.lower() or '@import' in css or re.search(r'url\s*\(', css, re.I):
        raise RenderError('presentation stylesheet requires static self-contained CSS')
    if origin and (not origin.startswith('https://') or any(c in origin for c in '\"<> ')):
        raise RenderError('invalid media origin')
    csp = ("default-src 'none'; style-src 'unsafe-inline'; "
           f"img-src data: {origin}; media-src {origin or chr(39)+"none"+chr(39)}; "
           "script-src 'none'; base-uri 'none'; form-action 'none'; object-src 'none'")
    facts=facts or {};head=[]
    if facts:
        from .bibliography import ordered_authors
        head.append('<meta name="citation_title" content="'+escape(title,quote=True)+'">')
        for author in ordered_authors(facts):
            head.append('<meta name="author" content="'+escape(author['name'],quote=True)+'">')
            head.append('<meta name="citation_author" content="'+escape(author['name'],quote=True)+'">')
        if facts.get('abstract'):head.append('<meta name="description" content="'+escape(facts['abstract'],quote=True)+'">')
    main_id='guavajam-main';number=1
    from .identities import element_ids
    used=set(element_ids(body))
    while main_id in used:number+=1;main_id='guavajam-main-'+str(number)
    semantic=' itemscope itemtype="https://schema.org/'+facts['schema_type']+'"' if facts.get('schema_type') else ''
    if semantic:body=body.replace('<h1>', '<h1 itemprop="headline">',1)
    rights='<p class="press-rights">Rights: '+escape(facts['rights'])+'</p>' if facts.get('rights') else ''
    canonical = ('Git SHA-1 ' + revision_sha + ' · ' + path)
    state = 'UNSIGNED PREVIEW — NOT A PUBLICATION' if preview else 'Signed Git publication'
    return ('<!doctype html>\n<html lang="' + escape(language, quote=True) + '"><head>\n'
            '<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">\n'
            '<meta http-equiv="Content-Security-Policy" content="' + escape(csp, quote=True) + '">\n'
            '<meta name="guavajam-viewer" content="guavajam.v1">\n'
            + '\n'.join(head) + '\n<title>' + escape(title) + ' — GuavaJam</title>\n<style>' + css + '</style></head><body>\n'
            '<a class="press-skip" href="#'+main_id+'">Skip to content</a>\n'
            '<header class="press-status"><span>' + escape(state) + '</span></header>\n'
            '<main id="'+main_id+'" tabindex="-1"><article class="press-edition"'+semantic+'>\n'
            '<div class="press-source-identity"><strong>Canonical source</strong>: <code>' +
            escape(canonical) + '</code></div>\n' + body +
            '\n</article></main><footer>'+rights+'<p>The Git revision is the edition identity. '
            'This HTML is an optional, disposable presentation, not a separately issued edition.</p></footer></body></html>\n')


def _raw_git_list(repo, sha, prefix):
    result = process(['git', '--no-replace-objects', 'ls-tree', '-r', '-z', '--name-only', sha, '--', prefix],cwd=repo)
    paths = [x.decode('utf-8', 'strict') for x in result.split(b'\x00') if x]
    if len(paths) > 128: raise RenderError('too many supporting documents')
    return sorted(p for p in paths if RECORD.fullmatch(p) and '/supporting/' in p)


def render(repo: Path, sha: str, work: str, output: Path, *, fingerprints=(), allowed_signers=None, preview=False):
    if not process(['pandoc','--version']).startswith(b'pandoc 3.12.1\n'):raise RenderError('Pandoc 3.12.1 is required')
    repo = Path(repo).resolve(); revision(repo, sha)
    from .identifiers import SLUG as WORK_SLUG
    if not WORK_SLUG.fullmatch(work): raise RenderError('work must be a Press-compatible slug')
    manuscript = f'works/{work}/manuscript.md'
    source = git_blob(repo, sha, manuscript)
    raw_commit = process(['git', '--no-replace-objects', 'cat-file', '-p', sha],cwd=repo)
    if preview:
        if fingerprints or allowed_signers:
            raise RenderError('preview cannot be combined with publication trust settings')
    else:
        if bool(fingerprints) == bool(allowed_signers):
            raise RenderError('signed rendering requires an external GPG fingerprint or SSH allowed signers')
        if allowed_signers:
            signers = Path(allowed_signers).expanduser().resolve(strict=True)
            if signers.is_relative_to(repo) or not signers.is_file() or signers.stat().st_size > 65536:
                raise RenderError('SSH allowed signers must be an external bounded authority file')
        from .publication_protocol import verify_shape
        try:
            declared = verify_shape(repo, sha, 'publication')
        except ValueError as exc:
            raise RenderError('invalid publication trailers/signature shape: ' + str(exc)) from exc
        if declared['manuscript'] != manuscript:
            raise RenderError('revision declares a different work')
        from .trusted_git import verifier_arguments
        if fingerprints:
            if any(not re.fullmatch(r'(?:[0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})',f) for f in fingerprints):
                raise RenderError('signed rendering requires one independently trusted GPG fingerprint')
            result = subprocess.run(['git','--no-replace-objects',*verifier_arguments(repo,'openpgp'),'verify-commit','--raw',sha],cwd=repo,capture_output=True)
            valid = re.findall(rb'\[GNUPG:\] VALIDSIG ([0-9A-Fa-f]{40,64})\b',result.stderr)
            if result.returncode or len(valid)!=1 or valid[0].decode().lower() not in {f.lower() for f in fingerprints}:
                raise RenderError('untrusted or invalid publication signature')
        else:
            signers = Path(allowed_signers).expanduser().resolve(strict=True)
            if signers.is_relative_to(repo) or not signers.is_file() or signers.stat().st_size > 65536:
                raise RenderError('SSH allowed signers must be an external bounded authority file')
            result = subprocess.run(['git','--no-replace-objects',*verifier_arguments(repo,'ssh'),
                                     '-c', 'gpg.ssh.allowedSignersFile=' + str(signers),
                                     'verify-commit', sha], cwd=repo, capture_output=True)
            if result.returncode:
                raise RenderError('untrusted or invalid SSH publication signature')
    policy_entry=process(['git','--no-replace-objects','ls-tree',sha,'--','asset-policy.json'],cwd=repo)
    if policy_entry:
        asset_policy=process(['git','--no-replace-objects','show',sha+':asset-policy.json'],cwd=repo,limit=32768)
        try:
            data=json.loads(asset_policy)
            if set(data)!={'origin'}:raise ValueError()
            from .asset_policy import AssetPolicy
            origin=AssetPolicy(data['origin']).origin
        except (ValueError,TypeError):raise RenderError('invalid canonical HTTPS asset policy') from None
    else:origin=''
    supporting_paths=_raw_git_list(repo,sha,'works/'+work+'/supporting')
    from .supporting_gfm import parse_gfm_record
    from .asset_policy import AssetPolicy
    supporting={};total_source=0
    for path in supporting_paths:
        raw=git_blob(repo,sha,path);total_source+=len(raw)
        if total_source>100*1024*1024:raise RenderError('supporting source inventory exceeds 100 MiB')
        _,positions=parse_gfm_record(raw.decode('utf-8'),policy=AssetPolicy(origin) if origin else None);supporting[path]=set(positions)
    if output.exists() and any(output.iterdir()):
        raise RenderError('destination must be a new or empty directory')
    all_files = {}
    documents = {}
    manuscript_facts = {}
    css = (Path(__file__).parent / 'styles/reading.css').read_text('utf-8')
    for path in [manuscript, *supporting]:
        source = git_blob(repo, sha, path)
        ast = json.loads(pandoc(source))
        from .source import validate_front_matter
        meta = validate_front_matter(Path(path),source.decode('utf-8'))[0] if path == manuscript else {}
        ast,items,anchors,source_title = _transform(ast,path=path,sha=sha,repo=repo,origin=origin,
                                      supporting=supporting,media=(path==manuscript),source=source.decode('utf-8'))
        from .identities import writer_prefix,restore_source_ids
        prefix=writer_prefix(anchors)
        body = pandoc(json.dumps(ast, ensure_ascii=False).encode('utf-8'),reader='json',writer='html5',id_prefix=prefix).decode('utf-8')
        body=restore_source_ids(body,anchors,prefix)
        from .presentation import prepare_code_focus
        body=prepare_code_focus(body)
        if path == manuscript:
            manuscript_facts = meta
            title = meta.get('title',work); language = meta.get('language','en'); dest = 'index.html'
        else:
            title=source_title;language='en';dest='supporting/'+Path(path).stem+'.html'
        documents[path] = {'title': title, 'language': language, 'anchors': sorted(anchors),
                           'view': dest, 'source_sha256': hashlib.sha256(source).hexdigest()}
        all_files[dest] = _html(title=title, language=language, revision_sha=sha,
                                path=path, body=body, css=css, origin=origin, preview=preview, facts=meta if path==manuscript else None)
    output.mkdir(parents=True, exist_ok=True)
    for name, html in all_files.items():
        target=output/name;target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html,encoding='utf-8')
    (output/'VIEW.json').write_text(json.dumps({
       'schema':'guavajam.view.v1','canonical':{'hash_algorithm':'sha1','revision':sha,
            'manuscript':manuscript}, 'renderer':'guavajam', 'preview':preview,
       'facts': manuscript_facts, 'documents': documents,
       'files': {name:hashlib.sha256(html.encode('utf-8')).hexdigest()
                 for name,html in sorted(all_files.items())}},indent=2,sort_keys=True)+'\n')
    return len(all_files)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ['update']:
        from .update import main as update_main
        return update_main(argv[1:])
    if argv[:1] == ['site']:
        from .site import main as site_main
        return site_main(argv[1:])
    if argv[:1] == ['render']:
        argv = argv[1:]
    p=argparse.ArgumentParser(description=__doc__, epilog="Use guavajam site --help for a publication catalog and DOI landing pages; the existing render invocation remains supported.")
    p.add_argument('--repo',required=True, type=Path)
    p.add_argument('--revision',required=True)
    p.add_argument('--work',required=True)
    p.add_argument('--output',required=True,type=Path)
    trust=p.add_mutually_exclusive_group(required=True)
    trust.add_argument('--trusted-gpg-fingerprint',action='append',default=[])
    trust.add_argument('--ssh-allowed-signers',type=Path)
    trust.add_argument('--unsigned-preview',action='store_true')
    args=p.parse_args(argv)
    try:
        number=render(args.repo,args.revision,args.work,args.output,
                      fingerprints=args.trusted_gpg_fingerprint,allowed_signers=args.ssh_allowed_signers,
                      preview=args.unsigned_preview)
    except (RenderError,MediaError,OSError,UnicodeError,ValueError) as exc:
        print('GuavaJam rendering failed: '+str(exc),file=sys.stderr);return 1
    print(f'Wrote {number} derived HTML page(s); Git revision remains the canonical edition.')
    return 0

if __name__ == '__main__':
    sys.exit(main())
