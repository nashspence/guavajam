#!/usr/bin/env python3
"""Strict, offline, fail-closed validator for GuavaJam source format v1.

Pandoc is a required dependency; parser warnings fail CI.
Git tracks the allowed source set; a public branch must be protected separately.
"""

from __future__ import annotations

from .repository import repository_root
import json
import yaml
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .identifiers import RIGHTS, WORK_IDS, check_identifier_map
from .asset_policy import AssetError, MAX_BYTES, MIME, load_policy, verify_bytes
from .source_ast import plain_text, walk
from .supporting_gfm import load_gfm_record, record_path
from .table_captions import normalize_table_captions
from .bibliography import check_optional_bibliography, ordered_authors, reviewed_language
from .subject_metadata import check_subject_metadata

FORMAT = "gfm+tex_math_dollars+raw_html+yaml_metadata_block"
SOURCE_EXTENSIONS = {".md", ".markdown"}
ANCHOR = re.compile(r'<a id="([a-z][a-z0-9]*(?:-[a-z0-9]+)*)"></a>\Z')
ATX = re.compile(r"^#{1,6}[ \t]+\S")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
FOOTNOTE = re.compile(r"\[\^([a-z0-9]+(?:-[a-z0-9]+)*)\]")
FOOTNOTE_DEF = re.compile(r"^\[\^([a-z0-9]+(?:-[a-z0-9]+)*)\]:[ \t]+")
META_REQUIRED = {"schema", "title", "language", "rights"}
META_OPTIONAL = {"author", "author_orcid", "authors", "identifiers", "abstract", "schema_type",
                 "keywords", "subjects"}


def issue(path: Path, message: str, line: int | None = None) -> str:
    return f"{path}:{line}: {message}" if line else f"{path}: {message}"


def read_text(path: Path, relative: Path, *, raw: bytes | None = None) -> tuple[str | None, list[str]]:
    errors: list[str] = []
    if path.is_symlink():
        return None, [issue(relative, "symlinks are prohibited")]
    try:
        if raw is None:
            with path.open('rb') as stream:
                raw = stream.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            return None, [issue(relative, 'Markdown source exceeds the 8-MiB individual limit')]
        text = raw.decode("utf-8", "strict")
    except (UnicodeDecodeError, OSError) as exc:
        return None, [issue(relative, f"not readable strict UTF-8: {exc}")]
    if text.startswith("\ufeff"):
        errors.append(issue(relative, "UTF-8 BOM is prohibited"))
    if "\r" in text:
        errors.append(issue(relative, "CR/CRLF prohibited; use LF"))
    if not text.endswith("\n"):
        errors.append(issue(relative, "exactly one final newline required"))
    elif text.endswith("\n\n"):
        errors.append(issue(relative, "use exactly one terminal newline (no extra blank line at EOF)"))
    if unicodedata.normalize("NFC", text) != text:
        errors.append(issue(relative, "non-NFC Unicode text"))
    for number, line in enumerate(text.splitlines(), 1):
        if any((ord(c) < 32 and c != "\t") or ord(c) == 127 for c in line):
            errors.append(issue(relative, "non-printable control character", number))
        if any(c in "\u200b\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069" for c in line):
            errors.append(issue(relative, "bidirectional override or zero-width space prohibited", number))
        if line.rstrip(" \t") != line:
            errors.append(issue(relative, "trailing whitespace", number))
    return text, errors


def visible_lines(source: str, first_line: int = 1) -> tuple[list[tuple[int, str]], list[str]]:
    """Hide fenced code, indented code and display math; retain source line numbers."""
    lines: list[tuple[int, str]] = []
    errors: list[str] = []
    fence: str | None = None
    fence_size = 0
    math_start: int | None = None
    for number, line in enumerate(source.splitlines(), first_line):
        if fence:
            if re.fullmatch(r" {0,3}" + re.escape(fence) + "{" + str(fence_size) + r",}[ \t]*", line):
                fence = None
            lines.append((number, ""))
            continue
        opening = FENCE.match(line)
        if opening and math_start is None:
            fence = opening.group(1)[0]
            fence_size = len(opening.group(1))
            lines.append((number, ""))
            continue
        if line.startswith("    ") and math_start is None:
            lines.append((number, ""))
            continue
        if line.strip() == "$$":
            math_start = None if math_start is not None else number
            lines.append((number, "$$"))
            continue
        if math_start is not None:
            lines.append((number, ""))
        else:
            lines.append((number, line))
    if fence:
        errors.append(f"unclosed fenced code block (at or before line {number})")
    if math_start is not None:
        errors.append(f"unclosed display math starting at line {math_start}")
    return lines, errors


def strip_inline_code(line: str) -> tuple[str, bool]:
    """Mask equal-length backtick code spans; unmatched runs are errors."""
    out = []
    cursor = 0
    unmatched = False
    while cursor < len(line):
        if line[cursor] != "`":
            out.append(line[cursor])
            cursor += 1
            continue
        end_run = cursor
        while end_run < len(line) and line[end_run] == "`":
            end_run += 1
        marker = line[cursor:end_run]
        match = re.search(r"(?<!`)" + re.escape(marker) + r"(?!`)", line[end_run:])
        if match is None:
            unmatched = True
            out.append(" " * len(marker))
            cursor = end_run
        else:
            close_end = end_run + match.end()
            out.append(" " * (close_end - cursor))
            cursor = close_end
    return "".join(out), unmatched


def unescaped_positions(line: str, character: str) -> list[int]:
    positions = []
    for i, char in enumerate(line):
        if char != character:
            continue
        slashes = 0
        j = i - 1
        while j >= 0 and line[j] == "\\":
            slashes += 1
            j -= 1
        if slashes % 2 == 0:
            positions.append(i)
    return positions


def inspect_manuscript_syntax(relative: Path, source: str, first_line: int = 1) -> tuple[dict, list[str]]:
    errors: list[str] = []
    lines, block_errors = visible_lines(source, first_line)
    errors.extend(issue(relative, message) for message in block_errors)
    headings: list[tuple[int, int, str]] = []
    anchors: dict[str, int] = {}
    attached: set[str] = set()
    footnote_defs: dict[str, int] = {}
    footnote_refs: list[tuple[str, int]] = []
    inline_math_count = 0
    display_math_count = 0
    in_display = False

    for index, (number, line) in enumerate(lines):
        if line.strip() == "$$":
            display_math_count += 1 if not in_display else 0
            in_display = not in_display
            continue
        if not line:
            continue
        masked, unmatched = strip_inline_code(line)
        if unmatched:
            errors.append(issue(relative, "unmatched backtick code span", number))
        if "\\(" in masked or "\\)" in masked or "\\[" in masked or "\\]" in masked:
            errors.append(issue(relative, "use $...$ or standalone $$ math, not backslash math delimiters", number))
        dollars = unescaped_positions(masked, "$")
        if len(dollars) % 2:
            errors.append(issue(relative, "unmatched inline $ (escape literal currency with \\$)", number))
        if any(dollars[j + 1] == dollars[j] + 1 for j in range(len(dollars) - 1)):
            errors.append(issue(relative, "display math $$ must be alone on its line", number))
        inline_math_count += len(dollars) // 2

        anchor = ANCHOR.fullmatch(line)
        if anchor:
            ident = anchor.group(1)
            if ident in anchors:
                errors.append(issue(relative, f"duplicate anchor #{ident}", number))
            anchors[ident] = number
            continue
        if line.startswith("<a ") and not anchor:
            errors.append(issue(relative, "only exact <a id=\"slug\"></a> anchors are allowed", number))

        if ATX.match(line):
            match = re.match(r"^(#{1,6})[ \t]+(.+)$", line)
            assert match is not None
            level = len(match.group(1))
            headings.append((number, level, match.group(2)))
            if level > 1:
                j = index - 1
                while j >= 0 and not lines[j][1].strip() and index - j <= 2:
                    j -= 1
                prev = ANCHOR.fullmatch(lines[j][1]) if j >= 0 else None
                if prev:
                    attached.add(prev.group(1))
                else:
                    errors.append(issue(relative, f"H{level} requires an explicit stable anchor immediately before it", number))

        definition = FOOTNOTE_DEF.match(masked)
        if definition:
            identifier = definition.group(1)
            if identifier in footnote_defs:
                errors.append(issue(relative, f"duplicate footnote [^{identifier}]", number))
            footnote_defs[identifier] = number
        for match in FOOTNOTE.finditer(masked):
            if definition and match.start() == 0:
                continue
            footnote_refs.append((match.group(1), number))

    for ident, number in anchors.items():
        if ident not in attached:
            errors.append(issue(relative, f"unattached anchor #{ident}", number))
    for ident, number in footnote_refs:
        if ident not in footnote_defs:
            errors.append(issue(relative, f"unresolved footnote [^{ident}]", number))
    for ident, number in footnote_defs.items():
        if not any(ref == ident for ref, _ in footnote_refs):
            errors.append(issue(relative, f"unused footnote definition [^{ident}]", number))
    for number, level, _ in headings:
        if level == 1:
            errors.append(issue(relative, "manuscript title belongs only in YAML metadata; body headings start at H2", number))
    last = 1
    for number, level, _ in headings:
        if last and level > last + 1:
            errors.append(issue(relative, f"heading level skipped H{last} → H{level}", number))
        last = level
    return {"headings": headings, "anchors": set(anchors), "inline_math": inline_math_count,
            "display_math": display_math_count, "footnote_refs": len(footnote_refs)}, errors


def parse_pandoc(source: str, *, reader: str = FORMAT) -> dict:
    result = subprocess.run(["pandoc", "--fail-if-warnings", "--from", reader, "--to", "json"],
                            input=source, capture_output=True, text=True, check=False)
    if result.returncode:
        raise ValueError("Pandoc parse failed or warned: " + result.stderr.strip())
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Pandoc returned invalid JSON: {exc}") from exc


def parse_document(path: Path, relative: Path) -> tuple[dict | None, list[str]]:
    try:
        source = path.read_bytes().decode("utf-8", "strict")
        ast = parse_pandoc(source)
    except (OSError, UnicodeError, ValueError) as exc:
        return None, [issue(relative, str(exc))]
    if relative == Path("content/manuscript.md"):
        return normalize_table_captions(ast, source, relative, parse_pandoc)
    return ast, []



def validate_front_matter(relative: Path, source: str) -> tuple[dict | None, str, int, list[str]]:
    """Parse bounded scalar fields and the explicitly supported block collections."""
    errors: list[str] = []
    if not source.startswith("---\n"):
        return None, source, 1, [issue(relative, "manuscript requires YAML front matter beginning with --- on line 1")]
    lines = source.splitlines(keepends=True)
    close = next((i for i in range(1, len(lines)) if lines[i] == "---\n"), None)
    if close is None:
        return None, source, 1, [issue(relative, "YAML front matter missing closing --- delimiter")]
    if close > 128:
        errors.append(issue(relative, "front matter exceeds 128 lines"))
    metadata_source = "".join(lines[1:close])
    body = "".join(lines[close + 1:])
    body_line = close + 2
    if not body.startswith("\n"):
        errors.append(issue(relative, "require a blank line after closing front matter delimiter", close + 1))
    elif body.startswith("\n\n"):
        errors.append(issue(relative, "require exactly one blank line after closing front matter delimiter", close + 1))
    if not body.strip():
        errors.append(issue(relative, "manuscript body cannot be empty"))

    nested = None
    for number, line in enumerate(lines[1:close], start=2):
        raw_line = line.removesuffix("\n")
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line == "identifiers:":
            nested = "identifiers"
        elif raw_line in {"authors:", "keywords:", "subjects:"}:
            nested = raw_line[:-1]
        elif re.fullmatch(r"[a-z][a-z0-9_]*:[ \t]+.+", raw_line):
            nested = None
        elif nested == "identifiers" and re.fullmatch(r"  [a-z][a-z0-9_]*:[ \t]+.+", raw_line):
            continue
        elif nested == "authors" and re.fullmatch(r"(?:  - name|    [a-z][a-z0-9_]*):[ \t]+.+", raw_line):
            continue
        elif nested == "keywords" and re.fullmatch(r"  - .+", raw_line):
            continue
        elif nested == "subjects" and re.fullmatch(r"(?:  - subject|    [a-z][a-z0-9_]*):[ \t]+.+", raw_line):
            continue
        else:
            errors.append(issue(relative, "front matter permits scalars, two-space identifiers or keywords entries and two/four-space authors or subjects entries only", number))

    forbidden = (yaml.tokens.AnchorToken, yaml.tokens.AliasToken,
                 yaml.tokens.TagToken, yaml.tokens.DirectiveToken,
                 yaml.tokens.FlowMappingStartToken, yaml.tokens.FlowSequenceStartToken)
    try:
        tokens = list(yaml.scan(metadata_source))
        for token in tokens:
            if isinstance(token, forbidden):
                errors.append(issue(relative, f"YAML aliases, anchors, tags, directives and flow/sequence collections prohibited: {type(token).__name__}"))
            if isinstance(token, yaml.tokens.ScalarToken) and token.style in ("|", ">"):
                errors.append(issue(relative, "YAML folded and literal multiline scalars prohibited"))
        root = yaml.compose(metadata_source, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        return None, body, body_line, errors + [issue(relative, f"invalid YAML front matter: {exc}")]
    if not isinstance(root, yaml.nodes.MappingNode):
        return None, body, body_line, errors + [issue(relative, "front matter must contain one mapping")]

    data: dict[str, object] = {}
    string_tag = "tag:yaml.org,2002:str"

    def scalar(node, label: str):
        if not isinstance(node, yaml.nodes.ScalarNode) or node.tag != string_tag:
            errors.append(issue(relative, f"{label} must be a YAML string scalar; quote ambiguous booleans, dates, and numbers"))
            return None
        if "\n" in node.value or "\r" in node.value:
            errors.append(issue(relative, f"{label} must be a single-line string"))
        return node.value

    for key_node, value_node in root.value:
        key = scalar(key_node, "front matter key")
        if key is None:
            continue
        if key in data:
            errors.append(issue(relative, f"duplicate YAML key: {key}"))
        if key == "keywords":
            if not isinstance(value_node, yaml.nodes.SequenceNode):
                errors.append(issue(relative, "keywords must be a YAML block list of literal strings"))
                continue
            keywords = []
            for entry in value_node.value:
                value = scalar(entry, "keyword")
                if value is not None:
                    keywords.append(value)
            data[key] = keywords
        elif key in {"authors", "subjects"}:
            singular = "author" if key == "authors" else "subject"
            description = "name/orcid" if key == "authors" else "subject metadata"
            if not isinstance(value_node, yaml.nodes.SequenceNode):
                errors.append(issue(relative, f"{key} must be an ordered YAML list of {description} mappings"))
                continue
            records = []
            for entry in value_node.value:
                if not isinstance(entry, yaml.nodes.MappingNode):
                    errors.append(issue(relative, f"each {key} entry must be a {description} mapping"))
                    continue
                record = {}
                for child_key_node, child_value_node in entry.value:
                    child_key = scalar(child_key_node, f"{singular} key")
                    if child_key is None:
                        continue
                    if child_key in record:
                        errors.append(issue(relative, f"duplicate YAML {singular} key: {child_key}"))
                    value = scalar(child_value_node, f"{key}.{child_key}")
                    if value is not None:
                        record[child_key] = value
                records.append(record)
            data[key] = records
        elif key == "identifiers":
            if not isinstance(value_node, yaml.nodes.MappingNode):
                errors.append(issue(relative, "identifiers must be a nested YAML mapping"))
                continue
            ids: dict[str, str] = {}
            for child_key_node, child_value_node in value_node.value:
                child_key = scalar(child_key_node, "identifier key")
                if child_key is None:
                    continue
                if child_key in ids:
                    errors.append(issue(relative, f"duplicate YAML identifier key: {child_key}"))
                value = scalar(child_value_node, f"identifiers.{child_key}")
                if value is not None:
                    ids[child_key] = value
            data[key] = ids
        else:
            val = scalar(value_node, key)
            if val is not None:
                data[key] = val
    return data, body, body_line, errors


def validate_work_fields(relative: Path, data: dict) -> list[str]:
    errors: list[str] = []
    unknown = set(data) - META_REQUIRED - META_OPTIONAL
    missing = META_REQUIRED - set(data)
    if unknown or missing:
        errors.append(issue(relative, f"schema keys mismatch: missing {sorted(missing)}, unknown {sorted(unknown)}"))
    try:
        ordered_authors(data)
        reviewed_language(data)
        check_optional_bibliography(data)
        check_subject_metadata(data)
    except AssetError as exc:
        errors.append(issue(relative, str(exc)))
    for name in (META_REQUIRED | {"author_orcid"}) & set(data):
        if not isinstance(data[name], str) or not data[name].strip():
            errors.append(issue(relative, f"{name} must be a nonempty string"))
    if not META_REQUIRED <= set(data) or any(not isinstance(data[name], str) for name in META_REQUIRED):
        return errors
    if data["schema"] != "guavajam.work.v1":
        errors.append(issue(relative, "schema must be guavajam.work.v1"))
    if data["rights"] not in RIGHTS:
        errors.append(issue(relative, "rights must be one of " + ", ".join(sorted(RIGHTS))))
    if "identifiers" in data:
        errors.extend(issue(relative, message) for message in check_identifier_map(data["identifiers"], WORK_IDS, str(relative)))
    return errors


def validate_pandoc_front_matter(relative: Path, data: dict, ast: dict) -> list[str]:
    """Reject disagreements between metadata read by YAML and Pandoc."""
    errors: list[str] = []
    meta = ast.get("meta", {})
    if set(meta) != set(data):
        errors.append(issue(relative, "Pandoc YAML metadata keys differ from source front matter"))
    for key in set(meta) & set(data):
        node = meta[key]
        if key in {"keywords", "subjects"}:
            # Like authors and abstract, these are literal source declarations.
            # Pandoc can interpret punctuation as Markdown, so check collection
            # structure but keep original YAML scalar values in derived facts.
            if node.get("t") != "MetaList" or not isinstance(data[key], list) or len(node.get("c", [])) != len(data[key]):
                errors.append(issue(relative, f"Pandoc interpreted {key} differently from the YAML list"))
                continue
            for actual, expected in zip(node["c"], data[key]):
                if key == "subjects":
                    if actual.get("t") != "MetaMap" or not isinstance(expected, dict) or set(actual.get("c", {})) != set(expected):
                        errors.append(issue(relative, "Pandoc subject keys differ from YAML front matter"))
                        continue
                    leaves = actual["c"].values()
                else:
                    leaves = (actual,)
                if any(leaf.get("t") not in {"MetaInlines", "MetaString", "MetaBlocks"} for leaf in leaves):
                    errors.append(issue(relative, f"Pandoc interpreted {key} scalar as non-text metadata"))
        elif key in {"authors", "abstract"}:
            # These new fields are literal YAML text, not Markdown. The builder
            # consumes the validated source values and supplies MetaStrings.
            continue
        elif key == "identifiers":
            if node.get("t") != "MetaMap" or not isinstance(data[key], dict):
                errors.append(issue(relative, "Pandoc interpreted identifiers differently from the YAML mapping"))
                continue
            pandoc_ids = node.get("c", {})
            if set(pandoc_ids) != set(data[key]):
                errors.append(issue(relative, "Pandoc identifier keys differ from YAML front matter"))
            for child_key in set(pandoc_ids) & set(data[key]):
                sub = pandoc_ids[child_key]
                if sub.get("t") == "MetaInlines":
                    rendered = plain_text(sub.get("c", []))
                elif sub.get("t") == "MetaString":
                    rendered = sub.get("c", "")
                else:
                    errors.append(issue(relative, f"Pandoc identifier {child_key} is not a text value"))
                    continue
                if rendered != data[key][child_key]:
                    errors.append(issue(relative, f"Pandoc metadata differs from YAML scalar for identifiers.{child_key}"))
        else:
            if node.get("t") == "MetaInlines":
                rendered = plain_text(node.get("c", []))
            elif node.get("t") == "MetaString":
                rendered = node.get("c", "")
            else:
                errors.append(issue(relative, f"Pandoc interpreted YAML field {key} as non-text metadata"))
                continue
            if rendered != data[key]:
                errors.append(issue(relative, f"Pandoc metadata differs from YAML scalar for field {key}"))
    return errors


def local_asset_size(path: Path) -> int:
    if not path.is_file() or path.resolve() != path.absolute():
        raise AssetError("asset must be a regular file, not a symlink")
    extension = path.suffix[1:]
    if extension not in MIME:
        raise AssetError(f"unsupported asset extension: {path.suffix!r}")
    with path.open("rb") as source:
        raw = source.read(MAX_BYTES + 1)
    verify_bytes(raw, extension, MIME[extension])
    return len(raw)


def validate_assets(root: Path, tracked: set[Path]) -> list[str]:
    errors: list[str] = []
    for rel in sorted(tracked):
        parts = rel.parts
        if len(parts) < 3 or parts[:2] != ("content", "assets"):
            continue
        if not all(re.fullmatch(r"[a-z0-9][a-z0-9._-]*", part) for part in parts[2:]):
            errors.append(issue(rel, "asset names must use lowercase ASCII letters, digits, dot, dash, underscore"))
        try:
            local_asset_size(root / rel)
        except (AssetError, OSError) as exc:
            errors.append(issue(rel, str(exc)))
    return errors


def valid_source_link(target: str) -> bool:
    if not target or target.startswith("#"):
        return True
    try:
        uri = urlsplit(target)
        return ((uri.scheme == "https" and bool(uri.hostname))
                or (uri.scheme == "mailto" and not uri.netloc and bool(uri.path)))
    except ValueError:
        return False


def validate_repository(root: Path, paths: list[Path], pandoc: str = "pandoc", *, enforce_version: bool = False) -> list[str]:
    """Validate incrementally using an on-disk link/anchor inventory."""
    from .source_inventory import source_inventory
    with source_inventory() as inventory:
        return _validate_repository(root, paths, pandoc, enforce_version=enforce_version, inventory=inventory)


def _validate_repository(root, paths, pandoc, *, enforce_version, inventory):
    """Validate source objects and explicit citation targets without an HTML edition."""
    root = root.resolve()
    tracked = {Path(path) for path in paths if Path(path).parts[:1] == ("content",)}
    errors, manuscripts = [], set()
    try:
        version = subprocess.run([pandoc, "--version"], capture_output=True, text=True, check=False)
        if version.returncode:
            return ["pandoc: compiler not available"]
        if enforce_version and not version.stdout.startswith("pandoc 3.12.1\n"):
            return [f"Pandoc version mismatch: expected 3.12.1, got {version.stdout.splitlines()[0]}"]
    except OSError:
        return ["pandoc: compiler not available"]
    try:
        policy = load_policy(root)
    except (AssetError, TypeError) as exc:
        errors.append(issue(Path("content/asset-policy.json"), str(exc))); policy = None
    for rel in sorted(tracked):
        path = root / rel
        if path.is_symlink() or path.resolve() != path.absolute():
            errors.append(issue(rel, "symlinks are prohibited")); continue
        if path.is_file():
            from .source_custody import native_path
            try:
                if native_path(path, MAX_BYTES):
                    errors.append(issue(rel, 'native conversation JSON and archives must remain outside tracked publication sources'))
            except (OSError, AssetError) as exc:
                errors.append(issue(rel, str(exc)))
        supporting = record_path(rel)
        manuscript = rel == Path("content/manuscript.md")
        if not path.is_file():
            errors.append(issue(rel, "publication sources must be regular files")); continue
        if rel.parts[:2] == ("content", "supporting") and rel.suffix.lower() in SOURCE_EXTENSIONS and not supporting:
            errors.append(issue(rel, "supporting Markdown requires content/supporting/<slug>.md"))
        if rel.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        source, issues = read_text(path, rel); errors.extend(issues)
        if source is None: continue
        if supporting:
            try:
                ast, positions, *_ = load_gfm_record(path, root=root)
                inventory.add(rel, ast, positions)
            except (OSError, ValueError) as exc:
                errors.append(issue(rel, str(exc)))
            continue
        if manuscript:
            _, _, _, issues=validate_front_matter(rel,source);errors.extend(issues)
        parsed, issues = parse_document(path, rel);errors.extend(issues)
        if parsed is None: continue
        headers = [n for n in walk(parsed['blocks']) if n['t'] == 'Header']
        heading_ids = [n['c'][1][0] for n in headers]
        if len(heading_ids) != len(set(heading_ids)):
            errors.append(issue(rel, "duplicate rendered heading IDs"))
        if manuscript:
            manuscripts.add(rel)
            front, body, line, issues = validate_front_matter(rel, source);errors.extend(issues)
            info, issues = inspect_manuscript_syntax(rel, body, line);errors.extend(issues)
            explicit = set(info['anchors'])
            if len(headers) != len(info['headings']):
                errors.append(issue(rel, "Pandoc headings differ from ATX source headings (Setext/nested headings prohibited)"))
            if [n['c'][0] for n in headers] != [h[1] for h in info['headings']]:
                errors.append(issue(rel, "heading levels differ between source and AST"))
            if front is not None:
                errors.extend(validate_work_fields(rel, front))
                errors.extend(validate_pandoc_front_matter(rel, front, parsed))
            raw = [n['c'][1] for n in walk(parsed['blocks']) if n['t'] in {'RawInline','RawBlock'} and n['c'][0]=='html']
            expected = []
            for _, text in visible_lines(body, line)[0]:
                if match := ANCHOR.fullmatch(text): expected.extend([f'<a id="{match[1]}">','</a>'])
            if sorted(raw) != sorted(expected):
                errors.append(issue(rel, "raw HTML is prohibited except standalone stable section anchors"))
            maths = [n for n in walk(parsed['blocks']) if n['t']=='Math']
            counts = (sum(n['c'][0]['t']=='InlineMath' for n in maths),sum(n['c'][0]['t']=='DisplayMath' for n in maths))
            if counts != (info['inline_math'],info['display_math']):
                errors.append(issue(rel, "math delimiters did not parse exactly"))
            for node in maths:
                if any(token in (r'\tag',r'\label') for token in re.findall(r'%[^\n]*|\\[A-Za-z]+|\\.',node['c'][1])):
                    errors.append(issue(rel, "\\tag or \\label cannot produce a published equation label; use a stable heading anchor"))
            if sum(n['t']=='Note' for n in walk(parsed['blocks'])) != info['footnote_refs']:
                errors.append(issue(rel, "footnote markers did not parse as expected"))
            try:
                from .media import declarations, candidates
                if policy is None:
                    if list(candidates(parsed['blocks'])): raise AssetError('media requires content/asset-policy.json')
                else: declarations(parsed,policy.origin,explicit)
            except ValueError as exc:errors.append(issue(rel,str(exc)))
        else:
            from .supporting_gfm import ANCHOR as SUPPORT_ANCHOR
            visible, _ = visible_lines(source)
            explicit = [m[1] for _, text in visible if (m := SUPPORT_ANCHOR.fullmatch(text))]
            if len(explicit) != len(set(explicit)):errors.append(issue(rel,'duplicate stable anchors'))
            if set(explicit) & set(heading_ids):errors.append(issue(rel,'explicit stable anchors collide with rendered header IDs'))
            explicit = set(explicit) | set(heading_ids)
        inventory.add(rel, parsed, explicit, manuscript=manuscript)
    for rel, manuscript, kind, target, has_alt in inventory.references():
        if kind=='Image' and not has_alt:errors.append(issue(rel,'image alt text required'))
        if policy and policy.manages(target):
            try:(policy.parse_image if kind=='Image' else policy.parse)(target)
            except AssetError as exc:errors.append(issue(rel,str(exc)))
            continue
        try:uri=urlsplit(target)
        except ValueError:errors.append(issue(rel,'malformed URI'));continue
        if uri.scheme or uri.netloc:
            if kind=='Image' or not valid_source_link(target):errors.append(issue(rel,'unsupported/external URI: '+target))
            continue
        if target.startswith(('/', '\\')) or '\\' in target or uri.query:
            errors.append(issue(rel,'unsupported/external URI: '+target));continue
        dest=(root/rel.parent/unquote(uri.path)).resolve() if uri.path else root/rel
        if not dest.is_relative_to(root/'content'):errors.append(issue(rel,'link escapes content/: '+target));continue
        relative=dest.relative_to(root)
        if not dest.exists():errors.append(issue(rel,'broken local link: '+target));continue
        if dest.is_file() and relative not in tracked:errors.append(issue(rel,'linked file is not tracked: '+target))
        if manuscript and kind=='Image' and not relative.is_relative_to(rel.parent/'assets'):
            errors.append(issue(rel,'manuscript images must be under '+str(rel.parent/'assets')))
        if uri.fragment and inventory.contains(relative) and not inventory.has_anchor(relative, unquote(uri.fragment), stable_only=manuscript):
            errors.append(issue(rel,'unresolved Markdown section link: '+target))
    if Path('content/manuscript.md') not in manuscripts:
        errors.append(issue(Path('content'), 'work directory requires manuscript.md'))
    errors.extend(validate_assets(root,tracked))
    return sorted(set(errors))


def git_sources(root: Path) -> list[Path]:
    result = subprocess.run(["git", "ls-files", "-z", "--", "content/"], cwd=root, capture_output=True, check=True)
    return [Path(b.decode("utf-8")) for b in result.stdout.split(b"\0") if b]


def main() -> int:
    root = repository_root()
    paths = git_sources(root)
    if not paths:
        print("FAIL: no tracked sources", file=sys.stderr)
        return 1
    errors = validate_repository(root, paths, enforce_version=True)
    if errors:
        for error in errors:
            print("ERROR:", error, file=sys.stderr)
        print(f"Source policy FAILED: {len(errors)} violation(s)", file=sys.stderr)
        return 1
    print(f"Source policy passed: {len(paths)} tracked paths checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
