"""Portable, declarative GFM media contract, press.media.v1.

This module is deliberately independent of Press's rendering and B2 clients.
Its entire input is a Pandoc JSON CodeBlock plus the repository's canonical
asset origin; GuavaJam vendors the same contract as an independent consumer.
"""
from __future__ import annotations
import json
import re
from urllib.parse import urlsplit

SLUG = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
KEY = re.compile(r"/sha256/([0-9a-f]{64})\.(mp3|mp4|vtt|png|jpg|jpeg|webp)\Z")
LANGUAGE = re.compile(r"[a-zA-Z]{2,8}(?:-[a-zA-Z0-9]{1,8})*\Z")
REQUIRED = frozenset({"kind", "id", "src", "title", "transcript"})
VIDEO = frozenset({"poster", "captions", "language", "label", "sound"})
MAX_DECLARATIONS = 32

class MediaError(ValueError):
    pass


def _pairs(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise MediaError("duplicate media field")
        data[key] = value
    return data


def asset(url: str, origin: str, allowed: set[str]) -> tuple[str, str]:
    if not isinstance(url, str) or len(url) > 600:
        raise MediaError("media URL must be a bounded string")
    parsed = urlsplit(url)
    host = urlsplit(origin)
    if (parsed.scheme != "https" or parsed.netloc != host.netloc or parsed.username
            or parsed.password or parsed.fragment or parsed.query
            or origin != f"https://{host.netloc}" or not host.hostname
            or not re.fullmatch(r"[a-z0-9.-]+", host.hostname) or parsed.port is not None):
        raise MediaError("media must use the configured canonical HTTPS asset origin")
    match = KEY.fullmatch(parsed.path)
    if not match or match[2] not in allowed:
        raise MediaError("media URL must carry a SHA-256 digest and the expected extension")
    return match[1], match[2]


def decode(text: str, origin: str) -> dict:
    if not isinstance(text, str) or len(text.encode('utf-8')) > 8192:
        raise MediaError("media declaration must be at most 8 KiB")
    try:
        data = json.loads(text, object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(MediaError("invalid JSON number")))
    except (ValueError, UnicodeError) as exc:
        raise MediaError("media declaration must be strict JSON") from exc
    if not isinstance(data, dict) or set(data) - (REQUIRED | VIDEO) or REQUIRED - set(data):
        raise MediaError("unknown or missing media declaration fields")
    if any(not isinstance(value, str) or not value or value != value.strip()
           or any(ord(char) < 32 or ord(char) == 127 for char in value)
           for value in data.values()):
        raise MediaError("media fields must be nonempty single-line strings")
    if data['kind'] not in ('audio', 'video') or not SLUG.fullmatch(data['id']):
        raise MediaError("media kind/id invalid")
    if len(data['title']) > 300 or data['title'].startswith('<'):
        raise MediaError("media title invalid")
    if not data['transcript'].startswith('#') or not SLUG.fullmatch(data['transcript'][1:]):
        raise MediaError("transcript must reference a stable local section anchor")
    asset(data['src'], origin, {'mp3'} if data['kind'] == 'audio' else {'mp4'})
    if data['kind'] == 'audio':
        if set(data) & VIDEO:
            raise MediaError("audio must not include video-specific fields")
    else:
        if not data.get('poster') or data.get('sound', 'present') not in {'present', 'none'}:
            raise MediaError("video requires poster and an explicit sound profile")
        data.setdefault('sound', 'present')
        asset(data['poster'], origin, {'png', 'jpg', 'jpeg', 'webp'})
        tracks = {'captions', 'language', 'label'}
        if data['sound'] == 'present' and not tracks <= set(data):
            raise MediaError("video with sound requires captions, language and label")
        if data['sound'] == 'none' and tracks & set(data):
            raise MediaError("silent video must not declare audio captions")
        if 'captions' in data:
            asset(data['captions'], origin, {'vtt'})
            if not LANGUAGE.fullmatch(data['language']) or len(data['label']) > 80:
                raise MediaError("invalid caption language or label")
    return data


def candidates(blocks):
    """Return all media fences; nested fences are errors, never hidden bypasses."""
    def find(value, top=False):
        if isinstance(value, dict):
            if value.get('t') == 'CodeBlock' and 'press-media' in value['c'][0][1]:
                yield (value, top)
            else:
                for child in value.values():
                    yield from find(child)
        elif isinstance(value, list):
            for child in value:
                yield from find(child)
    for block in blocks:
        yield from find(block, True)


def _nodes(value):
    if isinstance(value, dict):
        if isinstance(value.get('t'), str):
            yield value
        for child in value.values():
            yield from _nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nodes(child)


def declarations(ast: dict, origin: str, anchors: set[str]) -> list[dict]:
    output = []
    seen = set()
    for node, top in candidates(ast.get('blocks', [])):
        if not top or node['c'][0] != ['', ['press-media'], []]:
            raise MediaError("media fences must be exact, top-level GFM code fences")
        spec = decode(node['c'][1], origin)
        if spec['id'] in seen or spec['id'] in anchors or spec['transcript'][1:] not in anchors:
            raise MediaError("duplicate media id or missing transcript anchor")
        # The transcript must be an ordinary, anchored visible prose section.
        transcript = spec['transcript'][1:]
        blocks = ast.get('blocks', [])
        found = False
        for index, block in enumerate(blocks[:-1]):
            raw = block.get('c', []) if isinstance(block, dict) else []
            if block.get('t') != 'Para' or not isinstance(raw, list):
                continue
            opening = any(n.get('t') == 'RawInline' and n['c'][0] == 'html'
                          and n['c'][1] == f'<a id="{transcript}">' for n in raw)
            if not opening or blocks[index + 1].get('t') != 'Header':
                continue
            level = blocks[index + 1]['c'][0]
            for later in blocks[index + 2:]:
                if later.get('t') == 'Header' and later['c'][0] <= level:
                    break
                if later.get('t') in {'Para', 'Plain'} and any(n.get('t') == 'Str' for n in _nodes(later)):
                    found = True; break
            break
        if not found:
            raise MediaError("transcript must be an anchored section with visible prose")
        seen.add(spec['id']); output.append(spec)
        if len(output) > MAX_DECLARATIONS:
            raise MediaError("too many media declarations")
    return output
