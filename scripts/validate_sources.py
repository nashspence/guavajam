#!/usr/bin/env python3
"""Strict, offline, fail-closed validator for Press source format v1.

Pandoc is a required dependency: parser warnings and HTML render warnings fail CI.
Git tracks the allowed source set; a public branch must be protected separately.
"""

from __future__ import annotations

import html.parser
import json
import yaml
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlsplit

from editorial_notices import check_initial_notes, check_rights_statement
from identifier_schema import RIGHTS, SLUG, VERSION, WORK_IDS, check_identifier_map, edition_origin, identifier_error, validate_registrations
from asset_policy import AssetError, load_policy, svg_errors
from render_profile import TEMPLATE_PATH, plain_text, prepare_presentation_ast

FORMAT = "gfm+tex_math_dollars+raw_html+yaml_metadata_block"
SOURCE_EXTENSIONS = {".md", ".markdown"}
ANCHOR = re.compile(r'<a id="([a-z][a-z0-9]*(?:-[a-z0-9]+)*)"></a>\Z')
ATX = re.compile(r"^#{1,6}[ \t]+\S")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
FOOTNOTE = re.compile(r"\[\^([a-z0-9]+(?:-[a-z0-9]+)*)\]")
FOOTNOTE_DEF = re.compile(r"^\[\^([a-z0-9]+(?:-[a-z0-9]+)*)\]:[ \t]+")
META_REQUIRED = {"schema", "slug", "title", "author", "language", "rights", "status"}
META_OPTIONAL = {"author_orcid", "identifiers"}
LANGUAGE = re.compile(r"[a-z]{2,3}(?:-[A-Z]{2})?\Z")


def issue(path: Path, message: str, line: int | None = None) -> str:
    return f"{path}:{line}: {message}" if line else f"{path}: {message}"


def read_text(path: Path, relative: Path) -> tuple[str | None, list[str]]:
    errors: list[str] = []
    if path.is_symlink():
        return None, [issue(relative, "symlinks are prohibited")]
    try:
        text = path.read_bytes().decode("utf-8", "strict")
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
    if len([h for h in headings if h[1] == 1]) != 1:
        errors.append(issue(relative, "manuscript requires exactly one ATX H1 title"))
    if headings and headings[0][1] != 1:
        errors.append(issue(relative, "first heading must be H1", headings[0][0]))
    last = 0
    for number, level, _ in headings:
        if last and level > last + 1:
            errors.append(issue(relative, f"heading level skipped H{last} → H{level}", number))
        last = level
    return {"headings": headings, "anchors": set(anchors), "inline_math": inline_math_count,
            "display_math": display_math_count, "footnote_refs": len(footnote_refs)}, errors


def walk(value):
    if isinstance(value, dict):
        if isinstance(value.get("t"), str):
            yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def parse_document(path: Path, relative: Path) -> tuple[dict | None, list[str]]:
    result = subprocess.run(["pandoc", "--fail-if-warnings", "--from", FORMAT, "--to", "json", str(path)],
                            capture_output=True, text=True, check=False)
    if result.returncode:
        return None, [issue(relative, "Pandoc parse failed or warned: " + result.stderr.strip())]
    try:
        return json.loads(result.stdout), []
    except json.JSONDecodeError as exc:
        return None, [issue(relative, f"Pandoc returned invalid JSON: {exc}")]


class HtmlCheck(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.h1 = 0
        self.math = 0
        self.scripts = 0
        self.bad_imgs = 0
        self.ids = set()
        self.duplicate_ids = set()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        identifier = attributes.get("id")
        if identifier:
            if identifier in self.ids:
                self.duplicate_ids.add(identifier)
            self.ids.add(identifier)
        if tag == "h1":
            self.h1 += 1
        if tag == "math":
            self.math += 1
        if tag == "script":
            self.scripts += 1
        if tag == "img" and not attributes.get("alt"):
            self.bad_imgs += 1


def render_html(ast: dict, relative: Path, expected_math: int, manuscript: bool) -> tuple[set[str], list[str]]:
    if manuscript:
        try:
            prepare_presentation_ast(ast)
        except AssetError as exc:
            return set(), [issue(relative, str(exc))]
        # Use the edition shell so its own IDs and Pandoc-generated footnotes are counted.
        template = Path(__file__).resolve().parents[1] / TEMPLATE_PATH
        args = ["--template=" + str(template)]
    else:
        args = ["--metadata=pagetitle:Press"]
    result = subprocess.run([
        "pandoc", "--fail-if-warnings", "--from=json", "--to=html5", "--standalone",
        "--mathml", *args], input=json.dumps(ast), capture_output=True, text=True, check=False)
    if result.returncode:
        return set(), [issue(relative, "Pandoc HTML5/MathML render failed or warned: " + result.stderr.strip())]
    parser = HtmlCheck()
    parser.feed(result.stdout)
    errors = []
    if manuscript and parser.h1 != 1:
        errors.append(issue(relative, f"rendered HTML must contain exactly one H1, found {parser.h1}"))
    if parser.scripts:
        errors.append(issue(relative, "rendered HTML contains executable script"))
    if parser.bad_imgs:
        errors.append(issue(relative, "rendered HTML contains image without alt text"))
    if manuscript and parser.math != expected_math:
        errors.append(issue(relative, f"rendered MathML count {parser.math} differs from source math count {expected_math}"))
    if manuscript and parser.duplicate_ids:
        errors.append(issue(relative, "duplicate published HTML IDs: " + ", ".join(sorted(parser.duplicate_ids))))
    return parser.ids, errors


def validate_front_matter(relative: Path, source: str) -> tuple[dict | None, str, int, list[str]]:
    """Parse a restricted front matter schema: text fields plus one identifiers map.

    The sole allowed nested mapping has a fixed namespace, two-space indent,
    string values, no aliases/merge keys/custom tags/flow syntax. Pandoc parity
    is independently verified after parsing.
    """
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

    in_ids = False
    for number, line in enumerate(lines[1:close], start=2):
        raw_line = line.removesuffix("\n")
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line == "identifiers:":
            in_ids = True
        elif re.fullmatch(r"[a-z][a-z0-9_]*:[ \t]+.+", raw_line):
            in_ids = False
        elif in_ids and re.fullmatch(r"  [a-z][a-z0-9_]*:[ \t]+.+", raw_line):
            continue
        else:
            errors.append(issue(relative, "front matter uses only top-level scalars and two-space identifiers entries", number))

    forbidden = (yaml.tokens.AnchorToken, yaml.tokens.AliasToken,
                 yaml.tokens.TagToken, yaml.tokens.DirectiveToken,
                 yaml.tokens.FlowMappingStartToken, yaml.tokens.FlowSequenceStartToken,
                 yaml.tokens.BlockSequenceStartToken)
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
        if key == "identifiers":
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


def validate_work_fields(relative: Path, data: dict, slug: str, title: str) -> list[str]:
    errors: list[str] = []
    unknown = set(data) - META_REQUIRED - META_OPTIONAL
    missing = META_REQUIRED - set(data)
    if unknown or missing:
        errors.append(issue(relative, f"schema keys mismatch: missing {sorted(missing)}, unknown {sorted(unknown)}"))
    for name in (META_REQUIRED | {"author_orcid"}) & set(data):
        if not isinstance(data[name], str) or not data[name].strip():
            errors.append(issue(relative, f"{name} must be a nonempty string"))
    if not META_REQUIRED <= set(data) or any(not isinstance(data[name], str) for name in META_REQUIRED):
        return errors
    if data["schema"] != "press.work.v1":
        errors.append(issue(relative, "schema must be press.work.v1"))
    if data["slug"] != slug:
        errors.append(issue(relative, "slug must match works/<slug>"))
    if data["title"] != title:
        errors.append(issue(relative, "title must exactly match the manuscript H1 plain text"))
    if not LANGUAGE.fullmatch(data["language"]):
        errors.append(issue(relative, "language must be lowercase ISO code optionally followed by region (e.g. en-CA)"))
    if data["rights"] not in RIGHTS:
        errors.append(issue(relative, "rights must be one of " + ", ".join(sorted(RIGHTS))))
    if data["status"] != "draft":
        errors.append(issue(relative, "source status must remain draft; publication status belongs to releases"))
    if "author_orcid" in data:
        message = identifier_error("orcid", data["author_orcid"])
        if message:
            errors.append(issue(relative, f"author_orcid {message}"))
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
        if key == "identifiers":
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


def validate_svg(path: Path, rel: Path) -> list[str]:
    return [issue(rel, message) for message in svg_errors(path.read_bytes())]


def validate_assets(root: Path, tracked: set[Path]) -> list[str]:
    errors: list[str] = []
    for rel in sorted(tracked):
        parts = rel.parts
        if len(parts) < 4 or parts[0] != "works" or parts[2] != "assets":
            continue
        if not SLUG.fullmatch(parts[1]):
            errors.append(issue(rel, "invalid work slug"))
        if not all(re.fullmatch(r"[a-z0-9][a-z0-9._-]*", part) for part in parts[3:]):
            errors.append(issue(rel, "asset names must use lowercase ASCII letters, digits, dot, dash, underscore"))
        path = root / rel
        if path.is_symlink() or not path.is_file():
            errors.append(issue(rel, "asset must be a regular file, not a symlink"))
            continue
        if path.stat().st_size > 10 * 1024 * 1024:
            errors.append(issue(rel, "asset exceeds 10 MiB limit"))
        ext = path.suffix
        header = path.read_bytes()[:16]
        if ext == ".svg":
            errors.extend(validate_svg(path, rel))
        elif ext == ".png" and not header.startswith(b"\x89PNG\r\n\x1a\n"):
            errors.append(issue(rel, "invalid PNG file header"))
        elif ext in {".jpg", ".jpeg"} and not header.startswith(b"\xff\xd8\xff"):
            errors.append(issue(rel, "invalid JPEG file header"))
        elif ext == ".webp" and not (header.startswith(b"RIFF") and header[8:12] == b"WEBP"):
            errors.append(issue(rel, "invalid WebP file header"))
        elif ext not in {".png", ".jpg", ".jpeg", ".webp", ".svg"}:
            errors.append(issue(rel, f"unsupported asset extension: {ext!r}"))
    return errors


def valid_edition_link(target: str) -> bool:
    if not target or target.startswith("#"):
        return True
    try:
        uri = urlsplit(target)
        return ((uri.scheme == "https" and bool(uri.hostname))
                or (uri.scheme == "mailto" and not uri.netloc and bool(uri.path)))
    except ValueError:
        return False


def validate_repository(root: Path, paths: list[Path], pandoc: str = "pandoc", *, enforce_version: bool = False) -> list[str]:
    """Validate the tracked source snapshot; never silently ignore malformed Markdown."""
    root = root.resolve()
    paths = [Path(p) for p in paths]
    tracked = set(paths)
    errors: list[str] = []
    if Path("edition-origin.txt") in tracked:
        try:
            edition_origin(root)
        except (OSError, ValueError) as exc:
            errors.append(issue(Path("edition-origin.txt"), str(exc)))
    try:
        asset_policy = load_policy(root)
    except (AssetError, TypeError) as exc:
        errors.append(issue(Path("asset-policy.json"), str(exc)))
        asset_policy = None
    front_matters: dict[Path, dict] = {}
    bodies: dict[Path, tuple[str, int]] = {}
    asts: dict[Path, dict] = {}
    ids: dict[Path, set[str]] = {}
    published_ids: dict[Path, set[str]] = {}
    manuscripts: set[Path] = set()

    try:
        version = subprocess.run([pandoc, "--version"], capture_output=True, text=True, check=False)
    except OSError:
        return ["pandoc: compiler not available"]
    if version.returncode:
        return ["pandoc: compiler not available"]
    expected = "3.12.1"
    if enforce_version and not version.stdout.startswith(f"pandoc {expected}\n"):
        return [f"Pandoc version mismatch: expected {expected}, got {version.stdout.splitlines()[0]}"]

    for rel in sorted(paths):
        in_editions = len(rel.parts) == 4 and rel.parts[0] == "works" and rel.parts[2] == "editions"
        edition_notes = in_editions and rel.suffix == ".md" and VERSION.fullmatch(rel.stem)
        edition_rights = (in_editions and rel.name.endswith(".rights.txt")
                          and VERSION.fullmatch(rel.name.removesuffix(".rights.txt")))
        if rel.parts and rel.parts[0] == "works":
            if len(rel.parts) == 2 and rel.name == "README.md":
                pass
            elif len(rel.parts) >= 3:
                if not SLUG.fullmatch(rel.parts[1]):
                    errors.append(issue(rel, "work directory must use a lowercase ASCII slug"))
                allowed_root = len(rel.parts) == 3 and rel.parts[2] in {"README.md", "manuscript.md"}
                allowed_asset = len(rel.parts) >= 4 and rel.parts[2] == "assets"
                if not (allowed_root or allowed_asset or edition_notes or edition_rights):
                    errors.append(issue(rel, "unsupported file path under works/ (requires policy change)"))
            else:
                errors.append(issue(rel, "unexpected source directly beneath works/"))
        if edition_rights:
            statement, e = read_text(root / rel, rel)
            errors.extend(e)
            if statement is not None:
                try:
                    check_rights_statement(statement.removesuffix("\n"))
                except AssetError as exc:
                    errors.append(issue(rel, str(exc)))
            continue
        if rel.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        path = root / rel
        if not path.is_file():
            errors.append(issue(rel, "tracked Markdown source is missing"))
            continue
        source, e = read_text(path, rel)
        errors.extend(e)
        if source is None:
            continue
        if edition_notes:
            try:
                check_initial_notes(source)
            except AssetError as exc:
                errors.append(issue(rel, str(exc)))
        manuscript = len(rel.parts) == 3 and rel.parts[0] == "works" and rel.name == "manuscript.md"
        info = None
        expected_math = 0
        if manuscript:
            manuscripts.add(rel)
            slug = rel.parts[1]
            if not SLUG.fullmatch(slug):
                errors.append(issue(rel, "invalid work slug"))
            front, body, body_line, e = validate_front_matter(rel, source)
            errors.extend(e)
            bodies[rel] = (body, body_line)
            if front is not None:
                front_matters[rel] = front
            info, e = inspect_manuscript_syntax(rel, body, body_line)
            errors.extend(e)
        ast, e = parse_document(path, rel)
        errors.extend(e)
        if ast is None:
            continue
        asts[rel] = ast
        headers = [node for node in walk(ast.get("blocks", [])) if node["t"] == "Header"]
        headings = [node["c"][1][0] for node in headers]
        if len(headings) != len(set(headings)):
            errors.append(issue(rel, "duplicate rendered heading IDs"))
        if info:
            explicit = set(info["anchors"])
        else:
            # Templates and ordinary Markdown can use the same explicit anchors.
            # Ignore anchor-shaped text inside code or display math blocks.
            visible, _ = visible_lines(source)
            anchor_names = [match.group(1) for _, line in visible
                            if (match := ANCHOR.fullmatch(line))]
            if len(anchor_names) != len(set(anchor_names)):
                errors.append(issue(rel, "duplicate stable anchors"))
            explicit = set(anchor_names)
            if explicit & set(headings):
                errors.append(issue(rel, "explicit stable anchors collide with rendered header IDs"))
        ids[rel] = set(headings) | explicit
        if manuscript:
            if len(headers) != len(info["headings"]):
                errors.append(issue(rel, "Pandoc headings differ from ATX source headings (Setext/nested headings prohibited)"))
            for (number, level, _), node in zip(info["headings"], headers):
                if node["c"][0] != level:
                    errors.append(issue(rel, "heading levels differ between source and AST", number))
            if rel in front_matters:
                front = front_matters[rel]
                title = plain_text(headers[0]["c"][2]) if headers else ""
                errors.extend(validate_work_fields(rel, front, rel.parts[1], title))
                errors.extend(validate_pandoc_front_matter(rel, front, ast))
            raw_nodes = [node["c"][1] for node in walk(ast.get("blocks", []))
                         if node.get("t") in ("RawInline", "RawBlock") and node.get("c", [None])[0] == "html"]
            expected_raw = []
            for _, line in visible_lines(*bodies[rel])[0]:
                match = ANCHOR.fullmatch(line)
                if match:
                    expected_raw.extend([f'<a id="{match.group(1)}">', '</a>'])
            if sorted(raw_nodes) != sorted(expected_raw):
                errors.append(issue(rel, "raw HTML is prohibited except standalone stable section anchors"))
            maths = [node for node in walk(ast.get("blocks", [])) if node["t"] == "Math"]
            found_inline = sum(1 for m in maths if m["c"][0]["t"] == "InlineMath")
            found_display = sum(1 for m in maths if m["c"][0]["t"] == "DisplayMath")
            if (found_inline, found_display) != (info["inline_math"], info["display_math"]):
                errors.append(issue(rel, f"math delimiters did not parse exactly: expected "
                                         f"{info['inline_math']} inline/{info['display_math']} display, "
                                         f"got {found_inline} inline/{found_display} display"))
            found_notes = sum(1 for node in walk(ast.get("blocks", [])) if node["t"] == "Note")
            if found_notes != info["footnote_refs"]:
                errors.append(issue(rel, "footnote markers did not parse as expected"))
            expected_math = len(maths)
        rendered_ids, render_errors = render_html(ast, rel, expected_math, manuscript)
        if manuscript:
            published_ids[rel] = rendered_ids
        errors.extend(render_errors)

    for rel, ast in asts.items():
        manuscript = rel in manuscripts
        for node in walk(ast.get("blocks", [])):
            if node.get("t") not in {"Link", "Image"}:
                continue
            kind = node["t"]
            data = node["c"]
            if len(data) < 3:
                continue
            target = data[2][0]
            if kind == "Image" and not plain_text(data[1]).strip():
                errors.append(issue(rel, "image alt text required"))
            if kind == "Image" and manuscript and (":" in target or target.startswith("//")):
                try:
                    if asset_policy is None:
                        raise AssetError("unsupported/external URI: no approved asset origin configured")
                    asset_policy.parse(target)
                except AssetError as exc:
                    errors.append(issue(rel, str(exc)))
                continue
            try:
                parsed = urlsplit(target)
            except ValueError:
                errors.append(issue(rel, "malformed URI"))
                continue
            if manuscript and kind == "Link" and not valid_edition_link(target):
                errors.append(issue(rel, f"unsupported/external URI: standalone manuscript links require a fragment, HTTPS or mailto URL: {target}"))
                continue
            if parsed.scheme or parsed.netloc:
                if parsed.scheme.lower() not in {"https", "mailto"}:
                    errors.append(issue(rel, f"unsupported/external URI in {kind}: {target}"))
                continue
            if target.startswith("/") or target.startswith("\\"):
                errors.append(issue(rel, f"root-absolute path prohibited: {target}"))
                continue
            if parsed.query:
                errors.append(issue(rel, f"local links may not have query strings: {target}"))
            if not parsed.path and not parsed.fragment:
                continue
            destination = ((root / rel.parent / unquote(parsed.path)) if parsed.path else (root / rel)).resolve()
            try:
                dest_rel = destination.relative_to(root)
            except ValueError:
                errors.append(issue(rel, f"link escapes repository: {target}"))
                continue
            if not destination.exists():
                errors.append(issue(rel, f"broken local link: {target}"))
                continue
            if dest_rel not in tracked and destination.is_file():
                errors.append(issue(rel, f"linked file is not tracked: {target}"))
            if kind == "Image" and manuscript:
                if len(dest_rel.parts) < 4 or dest_rel.parts[:3] != ("works", rel.parts[1], "assets"):
                    errors.append(issue(rel, f"manuscript images must be under works/{rel.parts[1]}/assets/: {target}"))
            # Links within a manuscript must survive publication. Repository documents
            # link to Markdown previews, which also expose Pandoc’s generated heading slugs.
            targets = published_ids if manuscript else ids
            if parsed.fragment and dest_rel in asts and unquote(parsed.fragment) not in targets.get(dest_rel, set()):
                errors.append(issue(rel, f"unresolved Markdown section link: {target}"))
            if parsed.fragment and dest_rel.suffix.lower() in SOURCE_EXTENSIONS and dest_rel not in asts:
                errors.append(issue(rel, f"cannot validate fragment in unparsable Markdown: {target}"))

    work_folders = {rel.parts[1] for rel in tracked if len(rel.parts) >= 3 and rel.parts[0] == "works"}
    for slug in sorted(work_folders):
        if Path("works") / slug / "manuscript.md" not in manuscripts:
            errors.append(issue(Path("works") / slug, "work directory requires manuscript.md"))
    errors.extend(validate_assets(root, tracked))
    errors.extend(validate_registrations(root, tracked, front_matters))
    return sorted(set(errors))


def git_sources(root: Path) -> list[Path]:
    result = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True)
    return [Path(b.decode("utf-8")) for b in result.stdout.split(b"\0") if b]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
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
