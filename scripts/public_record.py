"""Generate and verify the publisher's portable public edition and work records."""
import argparse
import hashlib
import json
import sys
from html import escape
from pathlib import Path
from urllib.parse import quote

from asset_policy import AssetError
from build_edition import git
from editorial_notices import EDITION_ID_LABELS, edition_fields, edition_notices, edition_status
from identifier_schema import TAG, concept_doi_history, edition_origin, valid_record_url, valid_utc, version_key
from issuance import check_authorization, read_registration
from verify_edition import verify_edition


EDITION_RECORDS = Path("records/editions")
WORK_RECORDS = Path("records/works")
REGISTRATIONS = Path("registrations/editions")
PAGE_STYLE = (
    "<style>body{max-width:48rem;margin:2rem auto;padding:0 1rem;color:#222;background:white;"
    "font:1.125rem/1.5 Georgia,serif;overflow-wrap:anywhere}dt{font-weight:bold}dd{margin:0 0 .6rem}"
    "a{color:#12436b}</style>"
)


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
        PAGE_STYLE, "</head>", "<body><main>", "<p>Press · Edition record</p>",
        f"<h1>{title}</h1>", f'<p>Published <time datetime="{published_at}">{published_at}</time></p>',
    ]
    if notices:
        page.append("<h2>Editorial notices</h2><ul>")
        page.extend(f"<li>{escape(item.removeprefix('- '))}</li>" for item in notices)
        page.append("</ul>")
    page.append(f'<p><a id="press-record-read" href="{version}/read">Read this edition online</a></p>')
    page.append(f'<p><a href="../{slug}">All editions of this work</a></p>')
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


def regular_record(path):
    if path.absolute() != path.resolve() or (path.exists() and not path.is_file()):
        raise AssetError(f"{path}: public records require regular files without symlinks")


def saved_manifest(root, slug, version, registration):
    path = root / EDITION_RECORDS / slug / version / "provenance.json"
    regular_record(path)
    if not path.is_file():
        raise AssetError(f"{path}: claimed editions require their reviewed provenance")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != registration["provenance_sha256"]:
        raise AssetError(f"{path}: record provenance differs from the registration")
    manifest = json.loads(raw)
    if manifest["tag"] != f"works/{slug}/{version}":
        raise AssetError(f"{path}: record provenance names a different edition")
    return manifest


def edition_projection(root, slug, version, receipt, *, package=None, write=False):
    tag = f"works/{slug}/{version}"
    directory = root / EDITION_RECORDS / slug / version
    page, provenance = directory / "index.html", directory / "provenance.json"
    for path in (page, provenance):
        regular_record(path)

    if package is not None:
        manifest = verify_edition(package, tag=tag, commit=receipt["source_commit"])
        raw = (package / "provenance.json").read_bytes()
    else:
        manifest = saved_manifest(root, slug, version, receipt)
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


def tracked_paths(root, *paths):
    raw = git(root, "ls-files", "-z", "--", *map(str, paths), binary=True)
    return {Path(item.decode()) for item in raw.split(b"\0") if item}


def claimed_registrations(root, paths):
    for path in sorted(paths):
        tag = f"works/{path.parent.name}/{path.stem}"
        if len(path.parts) != 4 or path.suffix != ".json" or not TAG.fullmatch(tag):
            raise AssetError(f"{path}: invalid edition registration path")
        record = read_registration(root, path)
        if "published_at" in record and (record.get("issuing") is not True or not valid_utc(record["published_at"])):
            raise AssetError(f"{tag}: publication receipt requires an issuing claim and valid timestamp")
        if record.get("issuing") is True:
            yield path.parent.name, path.stem, record


def work_projection(root, slug, receipts, *, write=False):
    # The extensionless URL is a file to the browser. Include the slug in each
    # relative link so it also resolves at a recovery host below a path prefix.
    work_url = f"{edition_origin(root)}/works/{slug}"
    page = [
        "<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f'<link rel="canonical" href="{work_url}">',
        f"<title>{slug} — Editions · Press</title>", PAGE_STYLE, "</head><body><main>",
        "<p>Press · Work record</p>", f"<h1>Editions of {slug}</h1>",
        "<p>This address identifies the work across editions. To cite a passage, choose its edition below. "
        "Each edition record includes the reading copy and any correction, supersession or withdrawal notices.</p>",
    ]
    statuses = {version: edition_status(edition_notices(root, f"works/{slug}/{version}"), version, receipts)
                for version in receipts}
    checked = set()
    for version in statuses:
        chain = set()
        while version and version not in checked:
            if version in chain:
                raise AssetError(f"{slug}/{version}: replacement links form a cycle")
            chain.add(version)
            version = statuses[version][1]
        checked.update(chain)
    if all(status == "Withdrawn" for status, _ in statuses.values()):
        page.append("<p><strong>All editions of this work have been withdrawn.</strong></p>")
    registration = Path("registrations/works") / (slug + ".json")
    work = read_registration(root, registration) if registration in tracked_paths(root, registration) else {}
    history = concept_doi_history(work)
    doi = work.get("identifiers", {}).get("concept_doi")
    assigned = {change["from"] for change in history} | {doi}
    if doi:
        page.append(f'<p>Concept DOI: <a href="https://doi.org/{escape(quote(doi, safe="/"))}">{escape(doi)}</a></p>')
    if history:
        page.append("<h2>Concept DOI history</h2><ul>")
        for change in history:
            old, new = change["from"], change["to"]
            if old is None:
                decision = f"Assigned {new}."
            elif new is None:
                decision = f"Retired {old}."
            else:
                decision = f"Corrected {old} to {new}."
            page.append(f'<li>{escape(change["date"])} · {escape(decision)}</li>')
        page.append("</ul>")
    page.append("<ol>")
    for version in sorted(receipts, key=version_key, reverse=True):
        receipt = receipts[version]
        if receipt["record_url"] != f"{work_url}/{version}":
            raise AssetError(f"{slug}/{version}: receipt uses a different permanent work address")
        manifest = saved_manifest(root, slug, version, receipt)
        title = escape(manifest["title"])
        published = receipt["published_at"]
        captured = manifest.get("work_identifiers", {}).get("concept_doi")
        if captured and captured not in assigned:
            raise AssetError(f"{slug}/{version}: captured concept DOI has no matching work registration or history")
        status, successor = statuses[version]
        link = ""
        if successor:
            target_status = statuses[successor][0]
            label = f" ({target_status})" if target_status != "Published" else ""
            link = f' · <a href="{slug}/{successor}">Replacement {successor}{label}</a>'
        page.append(f'<li><a href="{slug}/{version}">{title} — {version}</a> · '
                    f'<time datetime="{published}">{published[:10]}</time> · <strong>{status}</strong>{link}</li>')
    page.extend(["</ol></main></body></html>", ""])
    html = "\n".join(page).encode("utf-8")
    path = root / WORK_RECORDS / slug / "index.html"
    regular_record(path)
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(html)
    elif path.read_bytes() != html:
        raise AssetError(f"{slug}: stale work record; regenerate it from all current receipts")


def write_records(root, tag, *, package=None):
    match = TAG.fullmatch(tag)
    if not match:
        raise AssetError("invalid public record edition tag")
    slug, version = match.groups()
    paths = tracked_paths(root, REGISTRATIONS / slug)
    receipts = {v: record for _, v, record in claimed_registrations(root, paths) if "published_at" in record}
    if version not in receipts:
        raise AssetError(f"{tag}: public records require a tracked, receipted edition")
    edition_projection(root, slug, version, receipts[version], package=package, write=True)
    work_projection(root, slug, receipts, write=True)


def check_records(root):
    tracked = tracked_paths(root, REGISTRATIONS, EDITION_RECORDS, WORK_RECORDS)
    paths = (path for path in tracked if path.is_relative_to(REGISTRATIONS))
    works = {}
    expected = set()
    for slug, version, record in claimed_registrations(root, paths):
        directory = EDITION_RECORDS / slug / version
        expected.add(directory / "provenance.json")
        if "published_at" in record:
            edition_projection(root, slug, version, record)
            expected.add(directory / "index.html")
            works.setdefault(slug, {})[version] = record
        else:
            saved_manifest(root, slug, version, record)
    for slug, receipts in works.items():
        work_projection(root, slug, receipts)
        expected.add(WORK_RECORDS / slug / "index.html")

    present = set()
    for directory in (EDITION_RECORDS, WORK_RECORDS):
        base = root / directory
        if base.absolute() != base.resolve() or (base.exists() and not base.is_dir()):
            raise AssetError(f"{base}: expected a public record directory without symlinks")
        present.update(p.relative_to(root) for p in base.rglob("*") if p.is_symlink() or not p.is_dir())
    indexed = {p for p in tracked if not p.is_relative_to(REGISTRATIONS)}
    inconsistent = (present ^ expected) | (indexed ^ expected)
    if inconsistent:
        raise AssetError("public records must contain exactly the tracked pages of receipted editions and the provenance of claimed editions: "
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
            write_records(root, args.write, package=args.directory)
        else:
            check_records(root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        sys.exit(f"Public record check failed: {exc}")
