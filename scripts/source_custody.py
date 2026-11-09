"""Recognize native conversation inputs that must remain outside publication custody."""
import io
import json
import re
import zipfile
from asset_policy import MAX_BYTES


def _native_members(archive):
    for info in archive.infolist():
        if not re.fullmatch(r'(?:.*/)?(?:conversations(?:[-_]\d+)?|capture-manifest)\.json', info.filename):
            continue
        if info.file_size > MAX_BYTES or info.flag_bits & 1:
            return True  # A suspected native input must be inspectable before publication.
        with archive.open(info) as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return True
        if _native_json(raw):
            return True
        try:
            metadata = json.loads(raw)
        except (ValueError, RecursionError):
            continue
        if isinstance(metadata, dict) and metadata.get('schema') == 'press-chatgpt-capture-reference/v1':
            return True
    return False


def native_path(path, max_json_bytes):
    with path.open('rb') as stream:
        signature = stream.read(4)
    if signature.startswith(b'PK'):
        try:
            with zipfile.ZipFile(path) as archive:
                return _native_members(archive)
        except (zipfile.BadZipFile, ValueError):
            return False
    return path.stat().st_size <= max_json_bytes and native_conversation(path.read_bytes())


def _native_json(raw: bytes) -> bool:
    if raw.lstrip(b'\xef\xbb\xbf \t\r\n').startswith((b'{', b'[')):
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError):
            return False
        items = data if isinstance(data, list) else [data]
        for item in items:
            if (isinstance(item, dict) and isinstance(item.get('mapping'), dict)
                    and ('current_node' in item or 'conversation_id' in item)
                    and any(isinstance(node, dict) and 'message' in node and 'parent' in node
                            for node in item['mapping'].values())):
                return True
    return False


def native_conversation(raw: bytes) -> bool:
    if _native_json(raw):
        return True
    if raw.startswith(b'PK'):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                # Inspect names without extracting or decompressing account data.
                return _native_members(archive)
        except (zipfile.BadZipFile, ValueError):
            pass
    return False
