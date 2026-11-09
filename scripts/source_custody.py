"""Bounded recognition of native inputs that must stay outside publication custody."""
from dataclasses import dataclass
import io
import json
from pathlib import Path
import zipfile
import zlib

from asset_policy import AssetError, MAX_ASSETS, MAX_BYTES, MAX_TOTAL_BYTES

MAX_ARCHIVE_BYTES = MAX_BYTES
MAX_ARCHIVE_MEMBER_BYTES = MAX_BYTES
MAX_ARCHIVE_TOTAL_BYTES = MAX_TOTAL_BYTES
MAX_ARCHIVE_MEMBERS = MAX_ASSETS
MAX_ARCHIVE_DEPTH = 3


@dataclass
class _Budget:
    members: int = 0
    expanded_bytes: int = 0


def _native_json(raw):
    try:
        data = json.loads(raw)  # Also recognizes JSON's UTF-16/32 encodings.
    except (ValueError, UnicodeError):
        return False
    except RecursionError:
        raise AssetError('JSON exceeds the native-input inspection depth') from None
    items = data if isinstance(data, list) else [data]
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get('schema') == 'press-chatgpt-capture-reference/v1':
            return True
        if (isinstance(item.get('mapping'), dict) and ('current_node' in item or 'conversation_id' in item)
                and any(isinstance(node, dict) and 'message' in node and 'parent' in node
                        for node in item['mapping'].values())):
            return True
    return False


def _is_archive(source):
    source.seek(0)
    signature = source.read(4)
    # Keep damaged ZIP headers fail-closed; is_zipfile also finds ZIPs with stubs.
    result = signature in {b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08', b'PK\x06\x06'} or zipfile.is_zipfile(source)
    source.seek(0)
    return result


def _archive(source, budget, depth):
    if depth >= MAX_ARCHIVE_DEPTH:
        raise AssetError('archive exceeds native-input inspection nesting limit')
    try:
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            if budget.members + len(members) > MAX_ARCHIVE_MEMBERS:
                raise AssetError('archive exceeds native-input inspection member count')
            budget.members += len(members)
            for member in members:
                if member.flag_bits & 1:
                    raise AssetError('encrypted archive cannot complete native-input inspection')
                if member.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                    raise AssetError('archive member exceeds native-input inspection byte limit')
                budget.expanded_bytes += member.file_size
                if budget.expanded_bytes > MAX_ARCHIVE_TOTAL_BYTES:
                    raise AssetError('archive exceeds native-input inspection expanded-byte limit')
                with archive.open(member) as stream:
                    raw = stream.read(MAX_ARCHIVE_MEMBER_BYTES + 1)
                if len(raw) != member.file_size:
                    raise AssetError('archive member differs from its inspected size')
                if _native_json(raw):
                    return True
                nested = io.BytesIO(raw)
                if _is_archive(nested) and _archive(nested, budget, depth + 1):
                    return True
    except AssetError:
        raise
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, OSError, ValueError, zlib.error):
        # Neither paths nor contents from a private input belong in diagnostics.
        raise AssetError('archive cannot complete native-input inspection') from None
    return False


def native_path(path: Path, max_json_bytes: int):
    size = path.stat().st_size
    with path.open('rb') as stream:
        if _is_archive(stream):
            if size > MAX_ARCHIVE_BYTES:
                raise AssetError('archive exceeds native-input inspection byte limit')
            return _archive(stream, _Budget(), 0)
        raw = stream.read(max_json_bytes + 1)
    if len(raw) > max_json_bytes:
        if path.suffix.lower() == '.json' or raw.lstrip(b'\xef\xbb\xbf \t\r\n').startswith((b'{', b'[')):
            raise AssetError('JSON source exceeds native-input inspection byte limit')
        return False
    return _native_json(raw)


def native_conversation(raw: bytes) -> bool:
    source = io.BytesIO(raw)
    if _is_archive(source):
        if len(raw) > MAX_ARCHIVE_BYTES:
            raise AssetError('archive exceeds native-input inspection byte limit')
        return _archive(source, _Budget(), 0)
    return _native_json(raw)
