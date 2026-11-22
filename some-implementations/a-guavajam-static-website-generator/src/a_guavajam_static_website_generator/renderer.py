"""Render a pinned GuavaJam-convention Git tree as a disposable, script-free view.

No GitHub API, source-program execution, B2 credentials, runtime JavaScript, or
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
from urllib.parse import unquote, urlsplit

from guavajam.media import asset, declarations, MediaError, SLUG
from guavajam.source_ast import walk
from .icons import finish_html, link_mime

READER = 'gfm+raw_html+tex_math_dollars+yaml_metadata_block'
REV = re.compile(r'[0-9a-f]{40}\Z')
RECORD = re.compile(r'content/(?:manuscript\.md|supporting/[a-z0-9]+(?:-[a-z0-9]+)*\.md)\Z')
IMAGE = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'webp': 'image/webp', 'svg': 'image/svg+xml'}


class RenderError(ValueError):
    pass


def process(command, *, cwd=None, data=None, limit=16 * 1024 * 1024):
    if command[0] == 'git':
        from guavajam.publication_protocol import run
        try:
            return run(cwd or '.', *command[1:], limit=limit)
        except (ValueError, OSError) as exc:
            raise RenderError(str(exc)) from exc
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
        raise RenderError('source path is outside the GuavaJam manuscript/supporting convention')
    mode = process(['git', '--no-replace-objects', 'ls-tree', sha, '--', path], cwd=repo)
    if not mode.startswith(b'100644 blob ') and not mode.startswith(b'100755 blob '):
        raise RenderError('rendering requires committed regular files, never symlinks')
    return process(['git', '--no-replace-objects', 'show', sha + ':' + path], cwd=repo, limit=maximum)


def git_extra(repo, sha, path, *, maximum=10 * 1024 * 1024):
    if (not path.startswith('content/') or '..' in PurePosixPath(path).parts
            or not re.fullmatch(r'content/assets/[a-z0-9._/-]+', path)):
        raise RenderError('local image must remain within a work assets directory')
    mode = process(['git', '--no-replace-objects', 'ls-tree', sha, '--', path], cwd=repo)
    if not mode.startswith(b'100644 blob ') and not mode.startswith(b'100755 blob '):
        raise RenderError('rendering requires committed regular files, never symlinks')
    return process(['git', '--no-replace-objects', 'show', sha + ':' + path], cwd=repo, limit=maximum)


def pandoc(ast_or_markdown: bytes, *, reader=READER, writer='json', id_prefix=None):
    return process(['pandoc', '--from=' + reader, '--to=' + writer, '--wrap=none', '--mathml', *(['--id-prefix='+id_prefix] if id_prefix else [])],
                   data=ast_or_markdown, limit=32 * 1024 * 1024)


def _make_media(item):
    kind = item['kind']; ident = escape(item['id'], quote=True)
    attrs = f'controls playsinline preload="metadata" aria-label="{escape(item['title'], quote=True)}"' if kind == 'video' else f'controls crossorigin="anonymous" preload="none" aria-label="{escape(item['title'], quote=True)}"'
    if kind == 'video':
        attrs += ' crossorigin="anonymous" poster="' + escape(item['poster'], quote=True) + '"'
    source = escape(item['src'], quote=True)
    mime = 'video/mp4' if kind == 'video' else 'audio/mpeg'
    out = ['<figure class="guavajam-media">', f'<{kind} {attrs} tabindex="0">',
           f'<source src="{source}" type="{mime}">']
    if 'captions' in item:
        out.append('<track kind="captions" src="' + escape(item['captions'], quote=True) +
                   '" srclang="' + escape(item['language'], quote=True) +
                   '" label="' + escape(item['label'], quote=True) + '" default>')
    out += [f'</{kind}>']
    if kind == 'video':
        out.append('<img class="guavajam-media-poster" src="' + escape(item['poster'], quote=True) + '" alt="">')
    out += [f'<figcaption>{escape(item["title"])} '
            f'<a href="{escape(item["transcript"], quote=True)}">Transcript</a> '
            f'<a href="{source}" download type="{mime}">Download {kind}</a></figcaption>', '</figure>']
    return '\n'.join(out)


def _href(url, *, kind, path, sha, repo, origin, supporting, downloads):
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
    local = unquote(parsed.path)
    fragment = unquote(parsed.fragment)
    if local.startswith(('/', '\\')) or '\\' in local or '\x00' in local:
        raise RenderError('ambiguous decoded local link')
    if not local:
        if fragment and re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',fragment): return '#' + fragment
        raise RenderError('invalid local fragment')
    relative = PurePosixPath(path).parent
    resolved = relative.joinpath(local)
    # A lexical POSIX path resolver that rejects traversal outside the work.
    parts = []
    for component in resolved.parts:
        if component == '..':
            if not parts: raise RenderError('traversal in document links')
            parts.pop()
        elif component not in {'.', ''}: parts.append(component)
    dest = '/'.join(parts)
    if dest in supporting and kind == 'Link':
        if fragment and fragment not in supporting[dest]:raise RenderError('unresolved source fragment')
        if dest == 'content/manuscript.md':
            href = ('../' if '/supporting/' in path else '') + 'index.html'
        else:
            href = ('supporting/' if path.endswith('/manuscript.md') else '') + PurePosixPath(dest).stem + '.html'
        return href + (('#' + fragment) if fragment else '')
    if kind == 'Image' and dest.startswith('content/assets/'):
        ext = PurePosixPath(dest).suffix.removeprefix('.')
        if ext not in IMAGE: raise RenderError('only passive local PNG/JPEG/WebP images are supported')
        raw = git_extra(repo, sha, dest)
        if len(raw) > 10 * 1024 * 1024 or not raw: raise RenderError('local image too large')
        return 'data:' + IMAGE[ext] + ';base64,' + base64.b64encode(raw).decode('ascii')
    if kind == 'Link' and dest.startswith('content/'):
        # Export the whole supplement; fragments belong to its native reader.
        from guavajam.publication_protocol import blob
        raw = blob(repo, sha, dest, maximum=20 * 1024 * 1024)
        name = hashlib.sha256(raw).hexdigest() + '.bin'
        downloads[name] = raw
        if len(downloads) > 256 or sum(len(value) for value in downloads.values()) > 256 * 1024 * 1024:
            raise RenderError('supplemental downloads exceed the output budget')
        return ('../' if '/supporting/' in path else '') + 'downloads/' + name
    raise RenderError('unresolved local link outside the committed publication content')


def _transform(ast, *, path, sha, repo, origin, supporting, downloads, media=True, source=None):
    from guavajam.validate_sources import validate_front_matter,validate_work_fields,validate_pandoc_front_matter,inspect_manuscript_syntax,walk
    from guavajam.table_captions import normalize_table_captions
    from .presentation import abstract_html, prepare_presentation_ast
    from .navigation import extract
    if media:
        front,body,line,errors=validate_front_matter(Path(path),source)
        info,issues=inspect_manuscript_syntax(Path(path),body,line);errors.extend(issues)
        if front is not None:
            errors.extend(validate_work_fields(Path(path),front))
            errors.extend(validate_pandoc_front_matter(Path(path),front,ast))
        if errors:raise RenderError('; '.join(errors[:8]))
        anchors=info['anchors']
        raw=[n['c'][1] for n in walk(ast['blocks']) if n['t'] in {'RawInline','RawBlock'} and n['c'][0]=='html']
        expected=[part for anchor in anchors for part in ('<a id="'+anchor+'">','</a>')]
        if sorted(raw)!=sorted(expected):raise RenderError('unreviewed raw HTML in manuscript')
        ast,errors=normalize_table_captions(ast,source,Path(path),lambda text:json.loads(pandoc(text.encode())))
        if errors:raise RenderError('; '.join(errors))
        items=declarations(ast,origin,anchors)
        navigation=extract(ast)
        for block in ast['blocks']:
            if block.get('t')=='CodeBlock' and 'guavajam-media' in block['c'][0][1]:
                from guavajam.media import decode
                item=decode(block['c'][1],origin)
                block.clear();block.update(t='RawBlock',c=['html',_make_media(item)])
        ast=prepare_presentation_ast(ast,navigation)
    else:
        from guavajam.supporting_gfm import parse_gfm_record
        from .supporting import present
        from guavajam.asset_policy import AssetPolicy
        ast,positions=parse_gfm_record(source,policy=AssetPolicy(origin) if origin else None);anchors=set(positions);items=[]
        from guavajam.supporting_metadata import split_frontmatter
        front, _, _ = split_frontmatter(source)
        navigation=extract(ast,positions)
        if front.get('abstract'):
            navigation['text'] = front['abstract'] + ' ' + navigation['text']
        ast=present(ast,positions,navigation)
    # Separate the metadata introduction from the body without adding source
    # passages or changing the navigation extracted above.
    ast['blocks'].insert(0, {'t': 'RawBlock', 'c': ['html',
        abstract_html(front.get('abstract')) + '<hr class="guavajam-document-divider">']})
    for node in walk(ast['blocks']):
        if node['t'] in ('Link','Image'):
            url=node['c'][2][0]
            if url.startswith('#') and unquote(url[1:]) not in anchors :raise RenderError('unresolved explicit source fragment')
            node['c'][2][0]=_href(url,kind=node['t'],path=path,sha=sha,repo=repo,origin=origin,supporting=supporting,downloads=downloads)
            if node['t'] == 'Link' and '/downloads/' in '/' + node['c'][2][0]:
                node['c'][0][2].append(['download', ''])
                if mime := link_mime(url): node['c'][0][2].append(['type', mime])
    return ast,items,anchors,front,navigation


def _html(*, title, language, revision_sha, path, body, css, origin, preview, facts=None, details=None, navigation=''):
    if not re.fullmatch(r'[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*', language):
        language = 'en'
    if preview:
        css += '\n' + (Path(__file__).parent / 'styles/preview.css').read_text('utf-8')
    if '</style' in css.lower() or '@import' in css or re.search(r'url\s*\(', css, re.I):
        raise RenderError('presentation stylesheet requires static self-contained CSS')
    if origin and (not origin.startswith('https://') or any(c in origin for c in '\"<> ')):
        raise RenderError('invalid media origin')
    csp = ("default-src 'none'; style-src 'unsafe-inline'; "
           f"img-src data: {origin}; media-src {origin or chr(39)+"none"+chr(39)}; "
           "script-src 'none'; base-uri 'none'; form-action 'none'; object-src 'none'")
    # Only issued source documents belong in search results. Keep directories
    # and unsigned previews crawlable so robots can read their noindex rule.
    robots = 'index, follow' if not preview and RECORD.fullmatch(path) else 'noindex, follow'
    facts=facts or {};head=['<meta name="robots" content="' + robots + '">']
    if facts:
        from guavajam.bibliography import ordered_authors
        head.append('<meta name="citation_title" content="'+escape(title,quote=True)+'">')
        for author in ordered_authors(facts):
            head.append('<meta name="author" content="'+escape(author['name'],quote=True)+'">')
            head.append('<meta name="citation_author" content="'+escape(author['name'],quote=True)+'">')
        if (details or facts).get('abstract'):head.append('<meta name="description" content="'+escape((details or facts)['abstract'],quote=True)+'">')
    if preview:
        head.append('<meta name="guavajam:preview" content="unsigned">')
    main_id='guavajam-main';number=1
    from .identities import element_ids
    used=set(element_ids(body))
    while main_id in used:number+=1;main_id='guavajam-main-'+str(number)
    semantic=' itemscope itemtype="https://schema.org/'+facts['schema_type']+'"' if path == 'content/manuscript.md' and facts.get('schema_type') else ''
    rights='<p class="guavajam-rights">Rights: '+escape(facts['rights'])+'</p>' if facts.get('rights') else ''
    canonical = ('Git SHA-1 ' + revision_sha + (' · ' + path if path else ''))
    from .citation import document_citation
    citation_html = document_citation(revision_sha, facts, source=path, details=details, preview=preview) if facts else ''
    status = ('' if preview else '<header class="guavajam-status"><span>Verified signed source</span></header>\n')
    accessible = ('<span class="guavajam-preview-accessible">Unsigned proposed edition, not issued.</span>'
                  if preview else '')
    root = '<html class="guavajam-preview-new-page"' if preview else '<html'
    return finish_html('<!doctype html>\n' + root + ' lang="' + escape(language, quote=True) + '"><head>\n'
            '<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">\n'
            '<meta http-equiv="Content-Security-Policy" content="' + escape(csp, quote=True) + '">\n'
            '<meta name="generator" content="a-guavajam-static-website-generator">\n'
            + '\n'.join(head) + '\n<title>' + escape(title) + ' — GuavaJam</title>\n<style>' + css + '</style></head><body>\n'
            '<a class="guavajam-skip" href="#'+main_id+'">Skip to content</a>\n'
            + status + navigation + '\n'
            '<main id="'+main_id+'" tabindex="-1"><article class="guavajam-edition"'+semantic+'>\n'
            + citation_html + accessible + '<div class="guavajam-source-identity"><strong>Canonical source</strong>: <code>' +
            escape(canonical) + '</code></div>\n' + body +
            '\n</article></main><footer>'+rights+'<p>' +
            ('The committed Git revision identifies the draft source. ' if preview
             else 'The Git revision is the edition identity. ') +
            'This HTML is an optional, disposable presentation, not a separately issued edition.</p></footer></body></html>\n')


def _raw_git_list(repo, sha, prefix):
    result = process(['git', '--no-replace-objects', 'ls-tree', '-r', '-z', '--name-only', sha, '--', prefix],cwd=repo)
    paths = [x.decode('utf-8', 'strict') for x in result.split(b'\x00') if x]
    if len(paths) > 128: raise RenderError('too many supporting documents')
    return sorted(p for p in paths if RECORD.fullmatch(p) and '/supporting/' in p)


def render(repo: Path, sha: str, output: Path, *, fingerprints=(), allowed_signers=None, preview=False):
    if not process(['pandoc','--version']).startswith(b'pandoc 3.12.1\n'):raise RenderError('Pandoc 3.12.1 is required')
    repo = Path(repo).resolve(); revision(repo, sha)
    manuscript = 'content/manuscript.md'
    source = git_blob(repo, sha, manuscript)
    if preview:
        if fingerprints or allowed_signers:
            raise RenderError('preview cannot be combined with publication trust settings')
    else:
        if bool(fingerprints) == bool(allowed_signers):
            raise RenderError('signed rendering requires an external GPG fingerprint or SSH allowed signers')
        from guavajam.publication_commit import verify
        try:
            verify(repo, sha, fingerprints=fingerprints, allowed_signers=allowed_signers, online_media=False)
        except (ValueError, OSError) as exc:
            raise RenderError(str(exc)) from exc
    policy_entry=process(['git','--no-replace-objects','ls-tree',sha,'--','content/asset-policy.json'],cwd=repo)
    if policy_entry:
        asset_policy=process(['git','--no-replace-objects','show',sha+':content/asset-policy.json'],cwd=repo,limit=32768)
        try:
            data=json.loads(asset_policy)
            if set(data)!={'origin'}:raise ValueError()
            from guavajam.asset_policy import AssetPolicy
            origin=AssetPolicy(data['origin']).origin
        except (ValueError,TypeError):raise RenderError('invalid canonical HTTPS asset policy') from None
    else:origin=''
    supporting_paths=_raw_git_list(repo,sha,'content/supporting')
    from guavajam.supporting_gfm import parse_gfm_record
    from guavajam.asset_policy import AssetPolicy
    supporting={};total_source=0
    for path in supporting_paths:
        raw=git_blob(repo,sha,path);total_source+=len(raw)
        if total_source>100*1024*1024:raise RenderError('supporting source inventory exceeds 100 MiB')
        _,positions=parse_gfm_record(raw.decode('utf-8'),policy=AssetPolicy(origin) if origin else None);supporting[path]=set(positions)
    from guavajam.validate_sources import validate_front_matter, inspect_manuscript_syntax
    _, body, line, _ = validate_front_matter(Path(manuscript), source.decode('utf-8'))
    info, _ = inspect_manuscript_syntax(Path(manuscript), body, line)
    supporting[manuscript] = info['anchors']
    if output.exists() and any(output.iterdir()):
        raise RenderError('destination must be a new or empty directory')
    all_files = {}
    downloads = {}
    documents = {}
    navigation_documents = {}
    manuscript_facts = {}
    citations = {}
    css = (Path(__file__).parent / 'styles/reading.css').read_text('utf-8')
    for path in [manuscript, *supporting_paths]:
        source = git_blob(repo, sha, path)
        ast = json.loads(pandoc(source)) if path == manuscript else None
        ast,items,anchors,meta,navigation = _transform(ast,path=path,sha=sha,repo=repo,origin=origin,
                                      supporting=supporting,downloads=downloads,media=(path==manuscript),source=source.decode('utf-8'))
        from .identities import writer_prefix,restore_source_ids
        prefix=writer_prefix(anchors)
        body = pandoc(json.dumps(ast, ensure_ascii=False).encode('utf-8'),reader='json',writer='html5',id_prefix=prefix).decode('utf-8')
        body=restore_source_ids(body,anchors,prefix)
        from .presentation import prepare_code_focus
        body=prepare_code_focus(body)
        if path == manuscript:
            manuscript_facts = meta
            title = meta['title']; language = meta.get('language','en'); dest = 'index.html'
        else:
            title=meta['title'];language=manuscript_facts.get('language','en');dest='supporting/'+Path(path).stem+'.html'
        documents[path] = {'title': title, 'language': language, 'anchors': sorted(anchors),
                           'view': dest, 'source_sha256': hashlib.sha256(source).hexdigest(),
                           **({'abstract': meta['abstract']} if meta.get('abstract') else {})}
        navigation_documents[path] = {**documents[path], **navigation}
        from .navigation import reader_navigation
        all_files[dest] = _html(title=title, language=language, revision_sha=sha,
                                path=path, body=body, css=css, origin=origin, preview=preview, facts=manuscript_facts, details=documents[path],
                                navigation=reader_navigation(path))
        from .citation_routes import source_download
        from guavajam.catalog import encoded
        from guavajam.publication_commit import citation
        record = {'schema': 'guavajam.source-citation.v1', 'hash_algorithm': 'sha1',
                  'revision': sha, 'manuscript': manuscript, 'documents': supporting}
        download = source_download(dest)
        citations[download] = source
        citations[str(PurePosixPath(download).with_suffix('.citation.json'))] = encoded(citation(record, document=path))
    canonical = {'hash_algorithm':'sha1','revision':sha,'manuscript':manuscript}
    navigation = {'schema':'guavajam.navigation.v1','canonical':canonical,
                  'preview':preview,'documents':navigation_documents}
    from guavajam.catalog import encoded
    from .navigation import document_directory
    navigation_bytes = encoded(navigation)
    all_files['documents.html'] = _html(title='Documents in this edition',language='en',revision_sha=sha,
        path='',body='<h1>Documents in this edition</h1>' + document_directory(navigation),
        css=css,origin=origin,preview=preview,
        navigation='<nav class="guavajam-document-nav" aria-label="Edition documents"><a href="index.html">Manuscript</a></nav>')
    output.mkdir(parents=True, exist_ok=True)
    for name, html in all_files.items():
        target=output/name;target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html,encoding='utf-8')
    for name, raw in downloads.items():
        (output / 'downloads').mkdir(exist_ok=True)
        (output / 'downloads' / name).write_bytes(raw)
    for name, raw in citations.items():
        (output / name).write_bytes(raw)
    (output/'NAV.json').write_bytes(navigation_bytes)
    (output/'VIEW.json').write_text(json.dumps({
       'schema':'guavajam.view.v1','canonical':canonical, 'renderer':'a-guavajam-static-website-generator', 'preview':preview,
       'facts': manuscript_facts, 'documents': documents,
       'files': {**{name:hashlib.sha256(html.encode('utf-8')).hexdigest()
                 for name,html in sorted(all_files.items())},
                 **{'downloads/' + name:hashlib.sha256(raw).hexdigest() for name,raw in sorted(downloads.items())},
                 'NAV.json':hashlib.sha256(navigation_bytes).hexdigest(),
                 **{name:hashlib.sha256(raw).hexdigest() for name,raw in sorted(citations.items())}}},indent=2,sort_keys=True)+'\n')
    return len(documents)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ['init']:
        from .bootstrap import main as init_main
        return init_main(argv[1:])
    if argv[:1] == ['preview-docker']:
        from .preview_docker import main as docker_main
        return docker_main(argv[1:])
    if argv[:1] == ['index']:
        from .index import main as index_main
        return index_main(argv[1:])
    if argv[:1] == ['preview']:
        from .preview import main as preview_main
        return preview_main(argv[1:])
    if argv[:1] == ['update']:
        from .update import main as update_main
        return update_main(argv[1:])
    if argv[:1] == ['site']:
        from .site import main as site_main
        return site_main(argv[1:])
    if argv[:1] == ['render']:
        argv = argv[1:]
    p=argparse.ArgumentParser(description=__doc__, epilog="Use a-guavajam-static-website-generator site --help for one work and a-guavajam-static-website-generator index --help for a collection.")
    p.add_argument('--repo',required=True, type=Path)
    p.add_argument('--revision',required=True)
    p.add_argument('--output',required=True,type=Path)
    trust=p.add_mutually_exclusive_group(required=True)
    trust.add_argument('--trusted-gpg-fingerprint',action='append',default=[])
    trust.add_argument('--ssh-allowed-signers',type=Path)
    trust.add_argument('--unsigned-preview',action='store_true')
    args=p.parse_args(argv)
    try:
        number=render(args.repo,args.revision,args.output,
                      fingerprints=args.trusted_gpg_fingerprint,allowed_signers=args.ssh_allowed_signers,
                      preview=args.unsigned_preview)
    except (RenderError,MediaError,OSError,UnicodeError,ValueError) as exc:
        print('GuavaJam rendering failed: '+str(exc),file=sys.stderr);return 1
    print(f'Wrote {number} source document view(s) and their navigation; Git revision remains the canonical edition.')
    return 0

if __name__ == '__main__':
    sys.exit(main())
