"""Include reviewed, work-local text records selected by links in a manuscript."""

from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import date, datetime
from pathlib import Path

from asset_policy import AssetError
from identifier_schema import SLUG
from render_profile import walk

MAX_RECORD_BYTES = 8 * 1024 * 1024
SELECTOR = re.compile(rf"supporting/({SLUG.pattern})\.json(?:#({SLUG.pattern}))?")
# Date-only notes and offset-qualified instants. Never guess a timezone or a time.
TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"(?:T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?"
    r"(?:Z|[+-][0-9]{2}:[0-9]{2}))?\Z"
)
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def timestamp_label(value: str) -> str:
    """Display source precision, date, and explicit zone without conversion."""
    day = date.fromisoformat(value[:10])
    readable = f"{day.day} {MONTHS[day.month - 1]} {day.year}"
    if len(value) == 10:
        return readable
    zone_suffix = "Z" if value.endswith("Z") else value[-6:]
    time = value[11:-len(zone_suffix)]
    # Only omit an explicitly zero seconds field for legibility; the machine-
    # readable <time datetime> always retains its exact source value.
    if time.endswith(":00"):
        time = time[:-3]
    zone = "UTC" if zone_suffix == "Z" else "UTC" + zone_suffix
    return readable + " · " + time + " " + zone


def valid_timestamp(value) -> bool:
    if not isinstance(value, str) or not TIMESTAMP.fullmatch(value):
        return False
    try:
        if len(value) == 10:
            date.fromisoformat(value)
        else:
            instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
            # Real-world offsets run from −12 to +14; RFC 3339's −00:00
            # denotes an unknown offset, which must not be presented as known.
            seconds = instant.utcoffset().total_seconds()
            if value.endswith("-00:00") or seconds < -12 * 3600 or seconds > 14 * 3600:
                return False
        return True
    except ValueError:
        return False


def record_path(relative: Path) -> bool:
    parts = relative.parts
    return (len(parts) == 4 and parts[0] == "works" and bool(SLUG.fullmatch(parts[1]))
            and parts[2] == "supporting" and relative.suffix == ".json" and bool(SLUG.fullmatch(relative.stem)))


def _unique_keys(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate key {key!r}")
        obj[key] = value
    return obj


def _text(value, *, single_line=False):
    if not isinstance(value, str) or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]", value):
        return False
    return not single_line or (bool(value.strip()) and value.splitlines() == [value])


def validate_record(record: dict) -> None:
    if not isinstance(record, dict) or record.keys() != {"title", "scope", "passages"}:
        raise AssetError("supporting record must contain exactly title, scope and passages")
    for field in ("title", "scope"):
        if not _text(record[field], single_line=True):
            raise AssetError(f"supporting record {field} must be nonempty single-line text")
    if not isinstance(record["passages"], list) or not record["passages"]:
        raise AssetError("supporting record requires a nonempty array of passages")
    seen = set()
    for i, passage in enumerate(record["passages"], start=1):
        if (not isinstance(passage, dict) or
                not {"id", "label", "text"} <= passage.keys() or
                passage.keys() - {"id", "label", "text", "timestamp"}):
            raise AssetError(f"passage {i} must contain id, label, text and optional timestamp only")
        if "timestamp" in passage and not valid_timestamp(passage["timestamp"]):
            raise AssetError(f"passage {i} requires a valid date or offset-qualified timestamp")
        identifier = passage["id"]
        if not isinstance(identifier, str) or not SLUG.fullmatch(identifier) or identifier in seen:
            raise AssetError(f"passage {i} requires a unique slug id")
        seen.add(identifier)
        if not _text(passage["label"], single_line=True) or not _text(passage["text"]):
            raise AssetError(f"passage {i} requires a single-line label and literal text")


def _read_record(path: Path) -> tuple[dict, str]:
    try:
        if not path.is_file() or path.resolve() != path.absolute():
            raise AssetError("supporting record must be a regular file, not a symlink")
        with path.open("rb") as stream:
            raw = stream.read(MAX_RECORD_BYTES + 1)
        if len(raw) > MAX_RECORD_BYTES:
            raise AssetError("supporting record exceeds 8 MiB")
        record = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_keys)
        validate_record(record)
    except (OSError, ValueError, RecursionError) as exc:
        raise AssetError(f"{path}: {exc}") from exc
    return record, hashlib.sha256(raw).hexdigest()


def load_record(path: Path) -> dict:
    return _read_record(path)[0]


def _heading(level: int, anchor: str, title: str, cls: str) -> list[dict]:
    return [
        {"t": "RawBlock", "c": ["html", f'<a id="{anchor}"></a>']},
        {"t": "Header", "c": [level, ["", [cls], []], [{"t": "Str", "c": title}]]},
    ]


def _provenance(scope: str, path: str, sha256: str) -> dict:
    # A definition list makes the reviewed source and full digest easy to find,
    # select, and print without making a raw SHA dominate the reading experience.
    return {"t": "RawBlock", "c": ["html", (
        '<div class="press-record-intro">'
        '<p class="press-record-scope">' + html.escape(scope) + '</p>'
        '<dl class="press-record-provenance">'
        '<dt>Source</dt><dd><code>' + html.escape(path) + '</code></dd>'
        '<dt>SHA-256</dt><dd><code>' + sha256 + '</code></dd>'
        '</dl></div>'
    )]}


def _timestamp(value: str) -> dict:
    # Render a semantic time element in a quiet separate line: long speaker labels
    # never compete with dates for space on a 320px phone.
    return {"t": "RawBlock", "c": ["html", (
        '<p class="press-passage-datetime"><time datetime="' + html.escape(value) + '">'
        + html.escape(timestamp_label(value)) + '</time></p>'
    )]}


def append_supporting_records(ast: dict, root: Path, manuscript: Path, tracked: set[Path]) -> None:
    """Rewrite citations and append each complete record once, in first-citation order."""
    passages_by_record = {}
    appendix = []
    for node in walk(ast["blocks"]):
        if node.get("t") != "Link":
            continue
        link = node["c"][2]
        if not link[0].startswith("supporting/"):
            continue
        match = SELECTOR.fullmatch(link[0])
        if not match:
            raise AssetError(f"invalid supporting record link: {link[0]}")
        slug, fragment = match.groups()
        record_id = f"record-{slug}"
        if slug not in passages_by_record:
            relative = manuscript.parent / "supporting" / (slug + ".json")
            if relative not in tracked:
                raise AssetError(f"supporting record is missing or untracked: {relative}")
            record, digest = _read_record(root / relative)
            appendix.extend(_heading(2, record_id, "Supporting record — " + record["title"],
                                   "press-record-heading"))
            appendix.append(_provenance(record["scope"], relative.as_posix(), digest))
            anchors = {}
            for number, item in enumerate(record["passages"], start=1):
                # The length makes dashed record/passage pairs unambiguous.
                anchor = f"passage-{len(slug)}-{slug}-{item['id']}"
                anchors[item["id"]] = anchor
                appendix.extend(_heading(3, anchor, f"Passage {number} — {item['label']}",
                                       "press-passage-heading"))
                if "timestamp" in item:
                    appendix.append(_timestamp(item["timestamp"]))
                # A div keeps an initial newline, which a pre element would swallow.
                # Character references preserve carriage returns through HTML parsing.
                literal = html.escape(item["text"], quote=False).replace("\r", "&#13;")
                appendix.append({"t": "RawBlock", "c": [
                    "html", '<div class="press-supporting-passage">'
                    + literal + "</div>",
                ]})
            passages_by_record[slug] = anchors
        if fragment and fragment not in passages_by_record[slug]:
            raise AssetError(f"unknown passage in supporting record link: {link[0]}")
        link[0] = "#" + (passages_by_record[slug][fragment] if fragment else record_id)
    ast["blocks"].extend(appendix)
