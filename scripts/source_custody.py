"""Recognize native conversation inputs that must remain outside publication custody."""
import io
import json
import re
import zipfile


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
                return any(re.fullmatch(r'(?:.*/)?(?:conversations(?:[-_]\d+)?|capture-manifest)\.json', info.filename)
                           for info in archive.infolist())
        except (zipfile.BadZipFile, ValueError):
            pass
    return False
