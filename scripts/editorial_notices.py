"""Read the ordered, dated public notices attached to an edition."""
import argparse
from datetime import date
from pathlib import Path
import re
import sys

from asset_policy import AssetError
from identifier_schema import EDITION_IDS, TAG, check_identifier_map


EDITION_ID_LABELS = {"version_doi": "Edition DOI", "arxiv_version": "arXiv version",
                     "isbn_epub": "EPUB ISBN", "isbn_pdf": "PDF ISBN", "isbn_print": "Print ISBN",
                     "oclc": "OCLC number"}


def markdown_text(value):
    """Display publisher and author strings literally in GitHub Markdown."""
    escaped = re.sub(r"([\\`*_{}\[\]()<>!|~&#])", r"\\\1", value)
    # GFM also interprets unescaped list markers at the start of a line. Keep
    # quoted editorial prose from becoming official notice or identifier items.
    return re.sub(r"(?m)^( {0,3})([-+]|[0-9]{1,9}\.)(?=\s)",
                  lambda match: match[1] + match[2][:-1] + "\\" + match[2][-1], escaped)


def edition_fields(manifest):
    fields = {"Work": manifest["title"], "Edition": manifest["tag"].rsplit("/", 1)[1],
              "Canonical artifact": manifest["canonical_artifact"], "Rights": manifest["rights"],
              "Source commit": manifest["source_commit"], "Provenance": "provenance.json",
              "Checksums": "SHA256SUMS"}
    if "rights_statement" in manifest:
        statement = manifest["rights_statement"]
        check_rights_statement(statement)
        fields["Edition rights"] = statement
    return fields


def check_rights_statement(text):
    if (not isinstance(text, str) or not text or text.strip() != text
            or not text.isprintable()
            or len(text.encode("utf-8")) > 4096):
        raise AssetError("edition rights must be one nonempty line of plain text of at most 4 KiB")


def check_prose_line(line):
    structural_markup = r"^(?:#{1,6}(?:\s|$)|`{3}|~{3}|=+\s*$|-+\s*$|>)|<[!?]|</?[A-Za-z][^<>]*>"
    if line and (not line.isprintable() or line.startswith("    ") or re.search(structural_markup, line.lstrip(" "))):
        raise AssetError("editorial prose requires plain paragraphs without headings, fences, indents or HTML")


def check_initial_notes(text):
    if (not isinstance(text, str) or not text.strip() or not text.endswith("\n")
            or "\r" in text or "\x00" in text or len(text.encode("utf-8")) > 32 * 1024):
        raise AssetError("initial notes require nonempty LF-terminated UTF-8 text of at most 32 KiB")
    for line in text.split("\n"):
        check_prose_line(line)


def render_initial_body(manifest, prose=None):
    """Called once by the builder. Published editions retain these exact UTF-8 bytes."""
    fields = edition_fields(manifest)
    body = "\n".join(f"{label}: {markdown_text(value)}" for label, value in fields.items())
    body += f"\n\n[Read this edition online]({manifest['record_url']}/read)"
    if prose is not None:
        check_initial_notes(prose)
        labels = tuple(label.casefold() + ":" for label in fields.keys() | {"Edition rights"})
        if any(line.lstrip().casefold().startswith(labels) for line in prose.splitlines()):
            raise AssetError("initial editorial prose duplicates a reserved release field")
        body += "\n\n## Initial editorial note\n\n" + markdown_text(prose.removesuffix("\n"))
    return body


def reviewed_initial_body(manifest):
    body = manifest.get("initial_release_body")
    if not isinstance(body, str) or not body.strip() or "\r" in body or "\x00" in body:
        raise AssetError("provenance requires the exact reviewed initial release body")
    return body


def release_notes(manifest, notices=(), *, identifiers=None):
    sections = []
    if notices:
        # Retain the list marker, escaping the text before GFM parses it. Later
        # official notices remain visible even if an older edition used rich prose.
        sections.append("## Editorial notices\n\n" + "\n".join("- " + markdown_text(n[2:]) for n in notices))
    if identifiers is not None:
        errors = check_identifier_map(identifiers, EDITION_IDS, manifest["tag"])
        if errors:
            raise AssetError("\n".join(errors))
        sections.append("## Edition identifiers\n\n" + "\n".join(
            f"- {EDITION_ID_LABELS[key]}: `{identifiers[key]}`" for key in sorted(identifiers)))
    return "\n\n".join([*sections, reviewed_initial_body(manifest)])


def edition_notices(root: Path, tag: str) -> list[str]:
    match = TAG.fullmatch(tag)
    if not match:
        raise AssetError("invalid notice edition tag")
    directory = root / "notices" / match[1] / match[2]
    if directory.resolve() != directory.absolute():
        raise AssetError("notice path must not traverse a symlink")
    if not directory.exists():
        return []
    result = []
    for index, path in enumerate(sorted(directory.iterdir()), 1):
        # Numbering gives deterministic order even for two notices on the same day.
        if path.name != f"{index:04}.md" or path.is_symlink() or not path.is_file():
            raise AssetError(f"unexpected notice file {path}")
        raw = path.read_bytes().decode("utf-8")
        line = re.fullmatch(r"(\d{4}-\d{2}-\d{2}) (Correction|Supersession|Withdrawal): ([^\r\n]+)\n", raw)
        if not line or line[3] != line[3].strip():
            raise AssetError(f"{path}: expected one dated correction, supersession, or withdrawal")
        check_prose_line(line[3])
        date.fromisoformat(line[1])
        result.append("- " + raw[:-1])
    return result


if __name__ == "__main__":
    from issuance import check_issuance
    from verify_edition import verify_edition

    parser = argparse.ArgumentParser(description="Print the exact approved public release body.")
    parser.add_argument("directory", type=Path, help="directory containing the edition attachments")
    parser.add_argument("--initial", action="store_true", help="render the original reviewed body before any identifiers or notices")
    args = parser.parse_args()
    try:
        manifest = verify_edition(args.directory)
        if args.initial:
            body = reviewed_initial_body(manifest)
        else:
            root = Path(__file__).resolve().parents[1]
            record = check_issuance(root, args.directory, manifest)
            body = release_notes(manifest, edition_notices(root, manifest["tag"]),
                                 identifiers=record.get("identifiers"))
        sys.stdout.write(body)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        sys.exit(str(exc))
