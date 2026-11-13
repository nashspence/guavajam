"""Independent reader for the tested Press GFM source conventions."""
from pathlib import Path
from urllib.parse import urlsplit
import re,json,subprocess,unicodedata,yaml
from .asset_policy import AssetError
from .source_ast import plain_text,walk
from .identifiers import RIGHTS,SLUG,WORK_IDS,check_identifier_map
from .bibliography import ordered_authors,reviewed_language,check_optional_bibliography

FORMAT = "gfm+tex_math_dollars+raw_html+yaml_metadata_block"


SOURCE_EXTENSIONS = {".md", ".markdown"}


ANCHOR = re.compile(r'<a id="([a-z][a-z0-9]*(?:-[a-z0-9]+)*)"></a>\Z')


ATX = re.compile(r"^#{1,6}[ \t]+\S")


FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


FOOTNOTE = re.compile(r"\[\^([a-z0-9]+(?:-[a-z0-9]+)*)\]")


FOOTNOTE_DEF = re.compile(r"^\[\^([a-z0-9]+(?:-[a-z0-9]+)*)\]:[ \t]+")


META_REQUIRED = {"schema", "slug", "title", "language", "rights", "status"}


META_OPTIONAL = {"author", "author_orcid", "authors", "identifiers", "abstract", "schema_type"}


def issue(path: Path, message: str, line: int | None = None) -> str:
    return f"{path}:{line}: {message}" if line else f"{path}: {message}"


def read_text(path: Path, relative: Path, *, raw: bytes | None = None) -> tuple[str | None, list[str]]:
    errors: list[str] = []
    if path.is_symlink():
        return None, [issue(relative, "symlinks are prohibited")]
    try:
        text = (path.read_bytes() if raw is None else raw).decode("utf-8", "strict")
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


def parse_pandoc(source: str, *, reader: str = FORMAT) -> dict:
    result = subprocess.run(["pandoc", "--fail-if-warnings", "--from", reader, "--to", "json"],
                            input=source, capture_output=True, text=True, check=False)
    if result.returncode:
        raise ValueError("Pandoc parse failed or warned: " + result.stderr.strip())
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Pandoc returned invalid JSON: {exc}") from exc


def validate_front_matter(relative: Path, source: str) -> tuple[dict | None, str, int, list[str]]:
    """Parse scalar fields, the identifiers map and an ordered authors list only."""
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
        elif raw_line == "authors:":
            nested = "authors"
        elif re.fullmatch(r"[a-z][a-z0-9_]*:[ \t]+.+", raw_line):
            nested = None
        elif nested == "identifiers" and re.fullmatch(r"  [a-z][a-z0-9_]*:[ \t]+.+", raw_line):
            continue
        elif nested == "authors" and re.fullmatch(r"(?:  - name|    [a-z][a-z0-9_]*):[ \t]+.+", raw_line):
            continue
        else:
            errors.append(issue(relative, "front matter permits scalars, two-space identifiers entries and two/four-space authors entries only", number))

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
        if key == "authors":
            if not isinstance(value_node, yaml.nodes.SequenceNode):
                errors.append(issue(relative, "authors must be an ordered YAML list of name/orcid mappings"))
                continue
            authors = []
            for entry in value_node.value:
                if not isinstance(entry, yaml.nodes.MappingNode):
                    errors.append(issue(relative, "each authors entry must be a name/orcid mapping"))
                    continue
                author = {}
                for child_key_node, child_value_node in entry.value:
                    child_key = scalar(child_key_node, "author key")
                    if child_key is None:
                        continue
                    if child_key in author:
                        errors.append(issue(relative, f"duplicate YAML author key: {child_key}"))
                    value = scalar(child_value_node, f"authors.{child_key}")
                    if value is not None:
                        author[child_key] = value
                authors.append(author)
            data[key] = authors
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


def validate_work_fields(relative: Path, data: dict, slug: str, title_inlines: list) -> list[str]:
    errors: list[str] = []
    unknown = set(data) - META_REQUIRED - META_OPTIONAL
    missing = META_REQUIRED - set(data)
    if unknown or missing:
        errors.append(issue(relative, f"schema keys mismatch: missing {sorted(missing)}, unknown {sorted(unknown)}"))
    try:
        ordered_authors(data)
        reviewed_language(data)
        check_optional_bibliography(data)
    except AssetError as exc:
        errors.append(issue(relative, str(exc)))
    for name in (META_REQUIRED | {"author_orcid"}) & set(data):
        if not isinstance(data[name], str) or not data[name].strip():
            errors.append(issue(relative, f"{name} must be a nonempty string"))
    if not META_REQUIRED <= set(data) or any(not isinstance(data[name], str) for name in META_REQUIRED):
        return errors
    if data["schema"] != "press.work.v1":
        errors.append(issue(relative, "schema must be press.work.v1"))
    if data["slug"] != slug:
        errors.append(issue(relative, "slug must match works/<slug>"))
    if any(node["t"] == "Math" for node in walk(title_inlines)):
        if re.search(r"\\[A-Za-z]+", data["title"]):
            errors.append(issue(relative, "use words or Unicode in the bibliographic title for a mathematical H1"))
    elif data["title"] != plain_text(title_inlines):
        errors.append(issue(relative, "title must exactly match the manuscript H1 plain text"))
    if data["rights"] not in RIGHTS:
        errors.append(issue(relative, "rights must be one of " + ", ".join(sorted(RIGHTS))))
    if data["status"] != "draft":
        errors.append(issue(relative, "source status must remain draft; publication status comes from a signed publication commit"))
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
        if key in {"authors", "abstract"}:
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


def valid_source_link(target: str) -> bool:
    if not target or target.startswith("#"):
        return True
    try:
        uri = urlsplit(target)
        return ((uri.scheme == "https" and bool(uri.hostname))
                or (uri.scheme == "mailto" and not uri.netloc and bool(uri.path)))
    except ValueError:
        return False

