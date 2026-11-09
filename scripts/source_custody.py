"""Recognize native conversation inputs that must remain outside publication custody."""
import io
import json
import re
import zipfile


def _native_members(archive):
    return any(re.fullmatch(r'(?:.*/)?(?:conversations(?:[-_]\d+)?|capture-manifest)\.json', info.filename)
               for info in archive.infolist())


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


def native_conversation(raw: bytes) -> bool:
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
    if raw.startswith(b'PK'):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                # Inspect names without extracting or decompressing account data.
                return _native_members(archive)
        except (zipfile.BadZipFile, ValueError):
            pass
    return False
