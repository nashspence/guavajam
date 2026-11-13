#!/usr/bin/env python3
"""Convert one selected ChatGPT export branch to private GFM for review and redaction."""
import argparse
import hashlib
import json
import math
import os
import re
import sys
import zipfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from chat_gfm import ATTRIBUTION_TOKEN, review_gfm
from supporting_gfm import MAX_RECORD_BYTES


def conversations(source):
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            members = [entry for entry in archive.infolist()
                       if re.fullmatch(r"conversations(?:[-_]\d+)?\.json", entry.filename)]
            if not members:
                raise ValueError("no conversations JSON at the root of the export ZIP")
            for entry in members:
                with archive.open(entry) as stream:
                    yield from collection(json.load(stream))
    else:
        with source.open(encoding="utf-8-sig") as stream:
            yield from collection(json.load(stream))


def collection(data):
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ValueError("expected a JSON array of conversations")
    return data


def branch_messages(conversation, tip):
    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict) or not isinstance(tip, str) or not tip:
        raise ValueError("missing conversation mapping or branch tip")
    messages = []
    seen = set()
    while tip is not None:
        if not isinstance(tip, str) or tip in seen:
            raise ValueError("invalid or cyclic parent chain")
        node = mapping.get(tip)
        if not isinstance(node, dict) or "parent" not in node or "message" not in node:
            raise ValueError("missing or malformed branch node")
        seen.add(tip)
        if node["message"] is not None:
            messages.append((tip, node["message"]))
        tip = node["parent"]
    return reversed(messages)


def export_timestamp(value):
    """Copy an export's numeric message creation time to canonical UTC.

    Preserve the source's fractional seconds (up to nanoseconds), rather than
    inventing precision or mistaking update_time for message creation time.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid message create_time")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("invalid message create_time")
    source = Decimal(str(value))
    if source < 0 or source.as_tuple().exponent < -9:
        raise ValueError("invalid message create_time precision")
    whole = int(source)
    try:
        utc = datetime.fromtimestamp(whole, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError("message create_time out of range") from exc
    text = utc.strftime("%Y-%m-%dT%H:%M:%S")
    frac = source - whole
    if frac:
        text += format(frac, "f")[1:].rstrip("0")
    return text + "Z"


def transcript(conversation, tip):
    title = conversation.get("title") or "Untitled conversation"
    if not isinstance(title, str):
        raise ValueError("invalid conversation title")
    passages = []
    for key, msg in branch_messages(conversation, tip):
        if not isinstance(msg, dict) or not isinstance(msg.get("author"), dict):
            raise ValueError("invalid message or author")
        role = msg["author"].get("role")
        if role not in ("user", "assistant"):
            continue
        metadata = msg.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("invalid message metadata")
        if (metadata.get("is_visually_hidden_from_conversation") or metadata.get("is_user_system_message") or
                msg.get("recipient", "all") != "all" or
                msg.get("channel") not in (None, "final", "commentary")):
            continue
        content = msg.get("content")
        if not isinstance(content, dict):
            raise ValueError("invalid message content")
        if content.get("content_type") in ("thoughts", "reasoning_recap"):
            continue
        parts = content.get("parts")
        if content.get("content_type") in ("text", "multimodal_text") and isinstance(parts, list):
            text = "\n\n".join(part if isinstance(part, str) else "[Non-text part omitted]"
                               for part in parts)
        else:
            text = "[Unsupported message content omitted]"
        # Node IDs survive branch selection and redaction. Avoid exposing arbitrary export keys.
        if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", key):
            anchor = "m-" + key
        else:
            anchor = "m-" + hashlib.sha256(key.encode()).hexdigest()
        passage = {"id": anchor, "label": role.title(), "text": text, "speaker": role}
        if role == "assistant":
            model = metadata.get("resolved_model_slug") or metadata.get("model_slug")
            for field, value in (("model", model), ("thinking_effort", metadata.get("thinking_effort"))):
                if isinstance(value, str) and ATTRIBUTION_TOKEN.fullmatch(value):
                    passage[field] = value
        if msg.get("create_time") is not None:
            passage["timestamp"] = export_timestamp(msg["create_time"])
        passages.append(passage)
    if not passages:
        raise ValueError("no eligible messages on the selected branch")
    return {
        "title": title,
        "scope": "Selected user and assistant text from one branch of a ChatGPT capture/export; "
                 "this document does not establish conversation completeness. "
                 "Other branches, hidden messages, internal reasoning, tool traffic and attachments are omitted. "
                 "Unsupported content in included messages is marked inline.",
        "passages": passages,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="private native capture/export ZIP or conversations JSON file")
    select = parser.add_mutually_exclusive_group(required=True)
    select.add_argument("--list", action="store_true", help="print IDs and titles locally as JSON lines")
    select.add_argument("--conversation", help="exact conversation ID")
    parser.add_argument("--tip", help="mapping node to use in place of current_node")
    parser.add_argument("--output", type=Path, help="new review GFM .md file outside the Press checkout")
    parser.add_argument("--metadata", type=Path, help="optional local YAML mapping of submitter-defined document metadata")
    args = parser.parse_args()
    if args.list and (args.tip or args.output or args.metadata) or not args.list and not args.output:
        parser.error("use --list alone, or --conversation with --output and an optional --tip")
    try:
        selected = None
        for item in conversations(args.source):
            identifier = item.get("conversation_id") or item.get("id")
            if args.list and isinstance(identifier, str):
                print(json.dumps([identifier, item.get("title")]))
            elif identifier == args.conversation and not args.list:
                if selected is not None:
                    raise ValueError("conversation ID appears more than once")
                selected = item
        if args.list:
            return
        if selected is None:
            raise ValueError("conversation ID not found")
        record = transcript(selected, args.tip if args.tip is not None else selected.get("current_node"))
        from chat_references import reference_index
        references = reference_index(msg for _, msg in branch_messages(
            selected, args.tip if args.tip is not None else selected.get("current_node")))
        from supporting_metadata import read_metadata
        metadata = read_metadata(args.metadata) if args.metadata else None
        data, diagnostics = review_gfm(record, identity=args.conversation, references=references, metadata=metadata)
        if len(data) > MAX_RECORD_BYTES:
            raise ValueError(f"extracted supporting record exceeds {MAX_RECORD_BYTES} bytes")
        output = args.output.parent.resolve(strict=True) / args.output.name
        if output.is_relative_to(Path(__file__).resolve().parents[1]):
            raise ValueError("private review output must be outside the Press checkout")
        if output.suffix != ".md":
            raise ValueError("review output must be a GFM .md file")
        with open(output, "xb", opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
            stream.write(data)
        print('Review required; GFM diagnostics: '+json.dumps(diagnostics,sort_keys=True),file=sys.stderr)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"{parser.prog}: {exc}\n")


if __name__ == "__main__":
    main()
