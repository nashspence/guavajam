"""Bounded-memory verification of large, content-addressed HTTPS media.

No upload, release artifact, B2 login, or publication mutation occurs here.
Online publication checks stream full objects and compare the SHA-256 that was
committed in GFM. The public origin is pinned against DNS rebinding by the
existing HTTPS verifier. A recording is validated at issuance, not per play.
"""
from __future__ import annotations
import hashlib
import io
from pathlib import Path

from asset_policy import AssetError, load_policy, public_response, response_chunks
from press_media import declarations, asset, MediaError
from validate_sources import parse_document

MIME = {'mp3': 'audio/mpeg', 'mp4': 'video/mp4', 'vtt': 'text/vtt',
        'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'webp': 'image/webp'}
MAX_MEDIA_BYTES = 20 * 1024 ** 3
MAX_MEDIA_TOTAL = 200 * 1024 ** 3
MAX_SMALL_BYTES = 10 * 1024 * 1024


def media_items(root: Path, relative: Path) -> list[dict]:
    policy = load_policy(root)
    ast, errors = parse_document(root / relative, relative)
    if errors or ast is None:
        raise AssetError('cannot parse media source: ' + '; '.join(errors))
    # Reuse source validator's syntax check as the source of declared anchors.
    from validate_sources import inspect_manuscript_syntax, validate_front_matter
    raw = (root / relative).read_text(encoding='utf-8')
    front, body, line, issues = validate_front_matter(relative, raw)
    info, syntax_issues = inspect_manuscript_syntax(relative, body, line)
    if issues or syntax_issues:
        raise AssetError('invalid media source anchors: ' + '; '.join(issues + syntax_issues))
    if policy is None:
        if any(n.get('t') == 'CodeBlock' and 'press-media' in n['c'][0][1]
               for n in _walk(ast.get('blocks', []))):
            raise AssetError('media requires asset policy')
        return []
    try:
        return declarations(ast, policy.origin, info['anchors'])
    except MediaError as exc:
        raise AssetError(str(exc)) from exc


def _walk(value):
    if isinstance(value, dict):
        if 't' in value: yield value
        for item in value.values(): yield from _walk(item)
    elif isinstance(value, list):
        for item in value: yield from _walk(item)


def references(items):
    for item in items:
        for name in ('src', 'poster', 'captions'):
            if name in item:
                yield item[name]


def verify_one(url, origin, *, response_factory=public_response, sound=None):
    digest, extension = asset(url, origin, set(MIME))
    ceiling = MAX_MEDIA_BYTES if extension in {'mp3', 'mp4'} else MAX_SMALL_BYTES
    small=io.BytesIO() if extension not in {'mp3','mp4'} else None
    with response_factory(url, MIME[extension], timeout=1800) as response:
        if response.status != 200:
            raise AssetError('media full-object verification requires HTTP 200')
        if response.getheader('Content-Type', '').partition(';')[0].strip().lower() != MIME[extension]:
            raise AssetError('media MIME differs from its approved extension')
        if extension in {'mp3', 'mp4'} and 'bytes' not in response.getheader('Accept-Ranges', '').lower():
            raise AssetError('media gateway must advertise HTTP byte ranges')
        count, hasher = 0, hashlib.sha256()
        for chunk in response_chunks(response, ceiling):
            count += len(chunk)
            hasher.update(chunk)
            if small is not None:small.write(chunk)
        if count == 0 or hasher.hexdigest() != digest:
            raise AssetError('media full-object SHA-256 does not match committed reference')
        from media_formats import inspect
        if small is not None:inspect(small,extension)
        else:
            from media_ranges import VerifiedRangeFile
            with VerifiedRangeFile(url,MIME[extension],count,response.getheader('X-Press-Asset-Version'),response_factory) as probe:
                inspect(probe,extension,sound=sound)
        return count


def verify_items(items, origin, *, verifier=verify_one):
    sizes, total, profiles = {}, 0, {}
    for item in items:
        if item.get('kind')=='video':
            profile=item.get('sound','present')
            if item['src'] in profiles and profiles[item['src']]!=profile:raise AssetError('the same recording has conflicting sound declarations')
            profiles[item['src']]=profile
    for url in references(items):
        if url not in sizes:
            size = verifier(url, origin, sound=profiles[url]) if url in profiles else verifier(url, origin)
            if type(size) is not int or size <= 0:
                raise AssetError('media verifier failed to establish object size')
            total += size
            if total > MAX_MEDIA_TOTAL:
                raise AssetError('edition exceeds media total byte budget')
            sizes[url] = size
    return sizes
