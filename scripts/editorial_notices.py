"""Read the ordered, dated public notices attached to an edition."""
import argparse
from datetime import date
from pathlib import Path
import re
import sys

from asset_policy import AssetError
from identifier_schema import EDITION_IDS, TAG, check_identifier_map


def edition_fields(manifest):
    fields = {"Work": manifest["title"], "Edition": manifest["tag"].rsplit("/", 1)[1],
              "Canonical artifact": manifest["canonical_artifact"], "Rights": manifest["rights"],
              "Source commit": manifest["source_commit"], "Provenance": "provenance.json",
              "Checksums": "SHA256SUMS"}
    if "rights_statement" in manifest:
        statement = manifest["rights_statement"]
        check_rights_statement(statement)
        # The statement is plain text even when the release body is rendered as GFM.
        fields["Edition rights"] = re.sub(r"([\\`*_{}\[\]()<>!|~&#])", r"\\\1", statement)
    return fields


def check_rights_statement(text):
    if (not isinstance(text, str) or not text or text.strip() != text
            or not text.isprintable()
            or len(text.encode("utf-8")) > 4096):
        raise AssetError("edition rights must be one nonempty line of plain text of at most 4 KiB")


def check_initial_notes(text):
    if (not isinstance(text, str) or not text.strip() or not text.endswith("\n")
            or "\r" in text or "\x00" in text or len(text.encode("utf-8")) > 32 * 1024):
        raise AssetError("initial notes require nonempty LF-terminated UTF-8 text of at most 32 KiB")


def release_notes(manifest, notices=(), *, identifiers=None):
    fields = edition_fields(manifest)
    body = "\n".join(f"{label}: {value}" for label, value in fields.items())
    if "initial_notes" in manifest:
        text = manifest["initial_notes"]
        check_initial_notes(text)
        labels = tuple(label + ":" for label in fields.keys() | {"Edition rights"})
        if any(line.startswith(labels) for line in text.splitlines()):
            raise AssetError("initial editorial prose duplicates a reserved release field")
        body += "\n\n" + text.removesuffix("\n")
    if identifiers is not None:
        errors = check_identifier_map(identifiers, EDITION_IDS, manifest["tag"])
        if errors:
            raise AssetError("\n".join(errors))
        labels = {"version_doi": "Edition DOI", "arxiv_version": "arXiv version",
                  "isbn_epub": "EPUB ISBN", "isbn_pdf": "PDF ISBN", "isbn_print": "Print ISBN",
                  "oclc": "OCLC number"}
        body += "\n\n## Edition identifiers\n\n" + "\n".join(
            f"- {labels[key]}: `{identifiers[key]}`" for key in sorted(identifiers))
    if notices:
        body += "\n\n## Editorial notices\n\n" + "\n".join(notices)
    return body


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
            body = release_notes(manifest)
        else:
            root = Path(__file__).resolve().parents[1]
            record = check_issuance(root, args.directory, manifest)
            body = release_notes(manifest, edition_notices(root, manifest["tag"]),
                                 identifiers=record.get("identifiers"))
        sys.stdout.write(body)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        sys.exit(str(exc))
