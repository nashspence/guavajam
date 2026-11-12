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
RECORD = re.compile(r'works/[a-z][a-z0-9]*(?:-[a-z0-9]+)*/(?:manuscript\.md|supporting/[a-z][a-z0-9]*(?:-[a-z0-9]+)*\.md)\Z')
ANCHOR_OPEN = re.compile(r'<a (?:id|name)="([a-z][a-z0-9]*(?:-[a-z0-9]+)*)"(?: data-press-speaker="(?:assistant|responder)" data-press-attribution="[A-Za-z0-9 ._:+/-]{1,128}")?>\Z')
ANCHOR_CLOSE = re.compile(r'</a>\Z')
SAFE_RAW = re.compile(r'</?(?:span|time)(?: (?:title|datetime)="[^"<>\x00-\x1f]{1,256}")?>\Z')
IMAGE = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'webp': 'image/webp'}


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


def pandoc(ast_or_markdown: bytes, *, reader=READER, writer='json'):
    return process(['pandoc', '--from=' + reader, '--to=' + writer, '--wrap=none', '--mathml'],
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


def _front(ast):
    meta = ast['meta']
    fields = {}
    for name in ('title', 'author', 'slug', 'language', 'rights', 'status', 'schema'):
        node = meta.get(name)
        if node is None: continue
        if node['t'] == 'MetaString': value = node['c']
        elif node['t'] == 'MetaInlines': value = _text(node['c'])
        else: raise RenderError('metadata must be scalar GFM text')
        if not value or len(value) > 512: raise RenderError('invalid metadata value')
        fields[name] = value
    if fields.get('schema') != 'press.work.v1' and 'schema' in fields:
        raise RenderError('unsupported Press metadata schema')
    return fields


def _anchors(ast):
    anchors = []
    for node in _walk(ast.get('blocks', [])):
        if node['t'] in ('RawInline', 'RawBlock') and node['c'][0] == 'html':
            raw = node['c'][1]
            opening = ANCHOR_OPEN.fullmatch(raw)
            if opening:
                anchors.append(opening[1])
            elif not ANCHOR_CLOSE.fullmatch(raw) and not SAFE_RAW.fullmatch(raw):
                raise RenderError('unreviewed raw HTML is prohibited in the optional viewer')
    if len(anchors) != len(set(anchors)):
        raise RenderError('duplicate source anchor')
    return set(anchors)


def _make_media(item):
    kind = item['kind']; ident = escape(item['id'], quote=True)
    attrs = f'controls playsinline preload="metadata" aria-labelledby="{ident}-caption"' if kind == 'video' else f'controls preload="none" aria-labelledby="{ident}-caption"'
    if kind == 'video':
        attrs += ' crossorigin="anonymous" poster="' + escape(item['poster'], quote=True) + '"'
    source = escape(item['src'], quote=True)
    mime = 'video/mp4' if kind == 'video' else 'audio/mpeg'
    out = [f'<figure class="press-media" id="{ident}">', f'<{kind} {attrs}>',
           f'<source src="{source}" type="{mime}">']
    if 'captions' in item:
        out.append('<track kind="captions" src="' + escape(item['captions'], quote=True) +
                   '" srclang="' + escape(item['language'], quote=True) +
                   '" label="' + escape(item['label'], quote=True) + '" default>')
    out += [f'</{kind}>']
    if kind == 'video':
        out.append('<img class="press-media-poster" src="' + escape(item['poster'], quote=True) + '" alt="">')
    out += [f'<figcaption id="{ident}-caption">{escape(item["title"])} '
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
        if parsed.fragment and SLUG.fullmatch(parsed.fragment): return '#' + parsed.fragment
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
        href = ('supporting/' if path.endswith('/manuscript.md') else '') + PurePosixPath(dest).stem + '.html'
        return href + (('#' + parsed.fragment) if parsed.fragment else '')
    if kind == 'Image' and dest.startswith('works/' + path.split('/')[1] + '/assets/'):
        ext = PurePosixPath(dest).suffix.removeprefix('.')
        if ext not in IMAGE: raise RenderError('only passive local PNG/JPEG/WebP images are supported')
        raw = git_extra(repo, sha, dest)
        if len(raw) > 10 * 1024 * 1024 or not raw: raise RenderError('local image too large')
        return 'data:' + IMAGE[ext] + ';base64,' + base64.b64encode(raw).decode('ascii')
    raise RenderError('unresolved local link; only committed supporting pages and images are rendered')


def _transform(ast, *, path, sha, repo, origin, supporting, media=True):
    anchors = _anchors(ast)
    if media:
        items = declarations(ast, origin, anchors)
    else:
        items = []
        if any(n.get('t') == 'CodeBlock' and 'press-media' in n['c'][0][1] for n in _walk(ast['blocks'])):
            raise RenderError('media declarations belong in the manuscript')
    # Press supporting GFM uses <a name="...">; canonicalize its fragment
    # target for modern HTML without inventing an independent citation key.
    for node in _walk(ast['blocks']):
        if node.get('t') in ('RawInline', 'RawBlock') and node['c'][0] == 'html':
            if ANCHOR_OPEN.fullmatch(node['c'][1]):
                node['c'][1] = node['c'][1].replace('<a name=', '<a id=', 1)
    for block in ast.get('blocks', []):
        if block.get('t') == 'CodeBlock' and 'press-media' in block['c'][0][1]:
            from .media import decode
            item = decode(block['c'][1], origin)
            block.clear();block.update(t='RawBlock',c=['html',_make_media(item)])
    # Pandoc GFM assigns a following heading the same ID as a preceding
    # standalone <a id="...">. Retain exactly ONE element with that identity.
    blocks = ast['blocks']
    index = 0
    while index + 1 < len(blocks):
        current, following = blocks[index:index+2]
        if current.get('t') == 'Para' and following.get('t') == 'Header':
            raw = current.get('c', [])
            if (isinstance(raw, list) and len(raw) == 2 and
                    all(n.get('t') == 'RawInline' and n['c'][0] == 'html' for n in raw)):
                opening = ANCHOR_OPEN.fullmatch(raw[0]['c'][1])
                if opening and raw[1]['c'][1] == '</a>':
                    following['c'][1][0] = opening[1]
                    del blocks[index]
                    continue
        index += 1
    for node in _walk(ast['blocks']):
        if node['t'] in ('Link', 'Image'):
            url = node['c'][2][0]
            node['c'][2][0] = _href(url, kind=node['t'], path=path, sha=sha, repo=repo,
                                  origin=origin, supporting=supporting)
    return items, anchors


def _html(*, title, language, revision_sha, path, body, css, origin, preview):
    if not re.fullmatch(r'[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*', language):
        language = 'en'
    if '</style' in css.lower() or '@import' in css or re.search(r'url\s*\(', css, re.I):
        raise RenderError('presentation stylesheet requires static self-contained CSS')
    if origin and (not origin.startswith('https://') or any(c in origin for c in '\"<> ')):
        raise RenderError('invalid media origin')
    csp = ("default-src 'none'; style-src 'unsafe-inline'; "
           f"img-src data: {origin}; media-src {origin}; "
           "script-src 'none'; base-uri 'none'; form-action 'none'; object-src 'none'")
    canonical = ('Git SHA-1 ' + revision_sha + ' · ' + path)
    state = 'UNSIGNED PREVIEW — NOT A PUBLICATION' if preview else 'Signed Git publication'
    return ('<!doctype html>\n<html lang="' + escape(language, quote=True) + '"><head>\n'
            '<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">\n'
            '<meta http-equiv="Content-Security-Policy" content="' + escape(csp, quote=True) + '">\n'
            '<meta name="guavajam-viewer" content="guavajam.v1">\n'
            '<title>' + escape(title) + ' — GuavaJam</title>\n<style>' + css + '</style></head><body>\n'
            '<a class="press-skip" href="#press-main">Skip to content</a>\n'
            '<header class="press-status"><span>' + escape(state) + '</span></header>\n'
            '<main id="press-main"><article class="press-edition">\n'
            '<div class="press-source-identity"><strong>Canonical source</strong>: <code>' +
            escape(canonical) + '</code></div>\n' + body +
            '\n</article></main><footer><p>The Git revision is the edition identity. '
            'This HTML is an optional, disposable presentation, not a separately issued edition.</p></footer></body></html>\n')


def _raw_git_list(repo, sha, prefix):
    result = process(['git', '--no-replace-objects', 'ls-tree', '-r', '-z', '--name-only', sha, '--', prefix],cwd=repo)
    paths = [x.decode('utf-8', 'strict') for x in result.split(b'\x00') if x]
    if len(paths) > 128: raise RenderError('too many supporting documents')
    return sorted(p for p in paths if RECORD.fullmatch(p) and '/supporting/' in p)


def render(repo: Path, sha: str, work: str, output: Path, *, fingerprints=(), preview=False):
    repo = Path(repo).resolve(); revision(repo, sha)
    if not SLUG.fullmatch(work): raise RenderError('work must be a Press-compatible slug')
    manuscript = f'works/{work}/manuscript.md'
    source = git_blob(repo, sha, manuscript)
    raw_commit = process(['git', '--no-replace-objects', 'cat-file', '-p', sha],cwd=repo)
    if not preview:
        if len(fingerprints) != 1 or not re.fullmatch('[0-9A-Fa-f]{40,64}', fingerprints[0]):
            raise RenderError('signed rendering requires one independently trusted GPG fingerprint')
        message = raw_commit.split(b'\n\n',1)[-1].decode('utf-8', 'strict').strip().splitlines()
        if message[-2:] != ['Press-Publication: 1', 'Press-Work: ' + manuscript]:
            raise RenderError('revision lacks the signed Press publication trailers for this work')
        result = subprocess.run(['git', '--no-replace-objects','-c','gpg.format=openpgp',
                                 'verify-commit','--raw',sha],cwd=repo,capture_output=True)
        valid = re.findall(rb'\[GNUPG:\] VALIDSIG ([0-9A-Fa-f]{40,64})\b',result.stderr)
        if result.returncode or len(valid)!=1 or valid[0].decode().lower()!=fingerprints[0].lower():
            raise RenderError('untrusted or invalid publication signature')
    asset_policy = process(['git','--no-replace-objects','show',sha+':asset-policy.json'],cwd=repo,limit=32768)
    try:
        data = json.loads(asset_policy)
        if set(data) != {'origin'}: raise ValueError()
        origin = data['origin']
        if not isinstance(origin,str) or not re.fullmatch(r'https://[a-z0-9.-]+',origin): raise ValueError()
    except (ValueError,TypeError):
        raise RenderError('Press-compatible asset-policy.json requires one canonical HTTPS origin') from None
    supporting = _raw_git_list(repo,sha,'works/'+work+'/supporting')
    if output.exists() and any(output.iterdir()):
        raise RenderError('destination must be a new or empty directory')
    all_files = {}
    css = (Path(__file__).parents[1] / 'styles/press-reading.css').read_text('utf-8')
    for path in [manuscript, *supporting]:
        source = git_blob(repo, sha, path)
        ast = json.loads(pandoc(source))
        meta = _front(ast) if path == manuscript else {}
        if path == manuscript and (meta.get('slug') != work or meta.get('schema') != 'press.work.v1'
                                   or not {'title','language','rights','status','author'} <= set(meta)):
            raise RenderError('work metadata differs from Press manuscript conventions')
        items, anchors = _transform(ast, path=path, sha=sha, repo=repo, origin=origin,
                                    supporting=set(supporting), media=(path==manuscript))
        body = pandoc(json.dumps(ast, ensure_ascii=False).encode('utf-8'),reader='json',writer='html5').decode('utf-8')
        if path == manuscript:
            title = meta.get('title',work); language = meta.get('language','en'); dest = 'index.html'
        else:
            title = path.split('/')[-1]; language='en'; dest='supporting/'+Path(path).stem+'.html'
        all_files[dest] = _html(title=title, language=language, revision_sha=sha,
                                path=path, body=body, css=css, origin=origin, preview=preview)
    output.mkdir(parents=True, exist_ok=True)
    for name, html in all_files.items():
        target=output/name;target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html,encoding='utf-8')
    (output/'VIEW.json').write_text(json.dumps({
       'schema':'guavajam.view.v1','canonical':{'hash_algorithm':'sha1','revision':sha,
            'manuscript':manuscript}, 'renderer':'guavajam', 'preview':preview,
       'files': {name:hashlib.sha256(html.encode('utf-8')).hexdigest()
                 for name,html in sorted(all_files.items())}},indent=2,sort_keys=True)+'\n')
    return len(all_files)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',required=True, type=Path)
    p.add_argument('--revision',required=True)
    p.add_argument('--work',required=True)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--trusted-gpg-fingerprint',action='append',default=[])
    p.add_argument('--unsigned-preview',action='store_true')
    args=p.parse_args(argv)
    try:
        number=render(args.repo,args.revision,args.work,args.output,
                      fingerprints=args.trusted_gpg_fingerprint,preview=args.unsigned_preview)
    except (RenderError,MediaError,OSError,UnicodeError,ValueError) as exc:
        print('GuavaJam rendering failed: '+str(exc),file=sys.stderr);return 1
    print(f'Wrote {number} derived HTML page(s); Git revision remains the canonical edition.')
    return 0

if __name__ == '__main__':
    sys.exit(main())
