"""Generate and verify the publisher's portable public edition records."""
import argparse
import hashlib
import json
import sys
from html import escape
from pathlib import Path

from asset_policy import AssetError
from build_edition import git
from editorial_notices import EDITION_ID_LABELS, edition_fields, edition_notices
from identifier_schema import TAG, valid_record_url, valid_utc
from issuance import check_authorization, read_registration
from verify_edition import verify_edition


EDITION_RECORDS = Path("records/editions")
REGISTRATIONS = Path("registrations/editions")


def record_html(manifest, *, published_at, notices, identifiers) -> bytes:
    tag = manifest["tag"]
    match = TAG.fullmatch(tag)
    if not match or not valid_record_url(manifest["record_url"], tag) or not valid_utc(published_at):
        raise AssetError("public record requires an edition tag, its permanent address and publication timestamp")
    slug, version = match.groups()
    filename = f"{slug}-{version}.html"
    if manifest["canonical_artifact"] != filename:
        raise AssetError("public record requires the edition’s canonical filename")

    title = escape(manifest["title"])
    page = [
        "<!doctype html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{title} — {version} · Press</title>",
        "<style>body{max-width:48rem;margin:2rem auto;padding:0 1rem;color:#222;background:white;"
        "font:1.125rem/1.5 Georgia,serif;overflow-wrap:anywhere}dt{font-weight:bold}dd{margin:0 0 .6rem}"
        "a{color:#12436b}</style>", "</head>", "<body><main>", "<p>Press · Edition record</p>",
        f"<h1>{title}</h1>", f'<p>Published <time datetime="{published_at}">{published_at}</time></p>',
    ]
    if notices:
        page.append("<h2>Editorial notices</h2><ul>")
        page.extend(f"<li>{escape(item.removeprefix('- '))}</li>" for item in notices)
        page.append("</ul>")
    page.append(f'<p><a href="{escape(manifest["record_url"])}/read">Read this edition online</a></p>')
    page.append("<h2>Edition details</h2><dl>")
    attachments = {"Canonical artifact", "Provenance", "Checksums"}
    for label, value in edition_fields(manifest).items():
        value = escape(value)
        # Resolve next to the extensionless record URL, including on the recovery host.
        if label in attachments:
            value = f'<a href="{version}/{value}">{value}</a>'
        page.append(f"<dt>{escape(label)}</dt><dd>{value}</dd>")
    page.append("</dl>")
    if identifiers:
        page.append("<h2>Edition identifiers</h2><ul>")
        page.extend(f"<li>{EDITION_ID_LABELS[key]}: {escape(identifiers[key])}</li>" for key in sorted(identifiers))
        page.append("</ul>")
    if manifest.get("initial_note"):
        page.append("<h2>Initial editorial note</h2>")
        page.extend(f"<p>{escape(paragraph)}</p>" for paragraph in manifest["initial_note"].strip().split("\n\n") if paragraph)
    page.extend(["</main></body>", "</html>"])
    return ("\n".join(page) + "\n").encode("utf-8")


def projection(root, tag, *, package=None, write=False):
    match = TAG.fullmatch(tag)
    if not match:
        raise AssetError("invalid public record edition tag")
    slug, version = match.groups()
    receipt = read_registration(root, REGISTRATIONS / slug / (version + ".json"))
    if not valid_utc(receipt.get("published_at")):
        raise AssetError(f"{tag}: public records require a receipted edition")

    directory = root / EDITION_RECORDS / slug / version
    page, provenance = directory / "index.html", directory / "provenance.json"
    for path in (page, provenance):
        if path.absolute() != path.resolve() or (path.exists() and not path.is_file()):
            raise AssetError(f"{path}: public records require regular files without symlinks")

    if package is not None:
        manifest = verify_edition(package, tag=tag, commit=receipt["source_commit"])
        raw = (package / "provenance.json").read_bytes()
    else:
        raw = provenance.read_bytes()
        if hashlib.sha256(raw).hexdigest() != receipt["provenance_sha256"]:
            raise AssetError(f"{tag}: record provenance differs from the receipt")
        manifest = json.loads(raw)
    if manifest["tag"] != tag:
        raise AssetError(f"{tag}: record provenance names a different edition")
    check_authorization(receipt, package if package is not None else directory, manifest)
    html = record_html(manifest, published_at=receipt["published_at"], notices=edition_notices(root, tag),
                       identifiers=receipt.get("identifiers"))
    if write:
        directory.mkdir(parents=True, exist_ok=True)
        if package is not None:
            provenance.write_bytes(raw)
        page.write_bytes(html)
    elif page.read_bytes() != html:
        raise AssetError(f"{tag}: stale public record; regenerate it from the current receipt and notices")


def check_records(root):
    tracked = {Path(item.decode()) for item in git(root, "ls-files", "-z", "--", str(REGISTRATIONS),
                                                   str(EDITION_RECORDS), binary=True).split(b"\0") if item}
    expected = set()
    for path in sorted(tracked):
        if not path.is_relative_to(REGISTRATIONS):
            continue
        if len(path.parts) != 4 or path.suffix != ".json":
            raise AssetError(f"{path}: invalid edition registration path")
        if "published_at" in read_registration(root, path):
            slug, version = path.parent.name, path.stem
            projection(root, f"works/{slug}/{version}")
            expected.update(EDITION_RECORDS / slug / version / name for name in ("index.html", "provenance.json"))

    base = root / EDITION_RECORDS
    if base.absolute() != base.resolve() or (base.exists() and not base.is_dir()):
        raise AssetError(f"{base}: expected a public record directory without symlinks")
    present = {p.relative_to(root) for p in base.rglob("*") if p.is_symlink() or not p.is_dir()}
    indexed = {p for p in tracked if p.is_relative_to(EDITION_RECORDS)}
    inconsistent = (present ^ expected) | (indexed ^ expected)
    if inconsistent:
        raise AssetError("public records must contain exactly the tracked pages and provenance of receipted editions: "
                         + ", ".join(str(p) for p in sorted(inconsistent)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", metavar="TAG")
    action.add_argument("--check", action="store_true")
    parser.add_argument("--directory", type=Path, help="verified edition package for the initial record")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        if args.write:
            projection(root, args.write, package=args.directory, write=True)
        else:
            check_records(root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        sys.exit(f"Public record check failed: {exc}")
