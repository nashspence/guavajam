"""Generate and verify the publisher's portable public edition and work records."""
import argparse
import hashlib
import json
import sys
from html import escape
from pathlib import Path
from urllib.parse import quote

from asset_policy import AssetError, bounded_record
from build_edition import git
from editorial_notices import EDITION_ID_LABELS, edition_fields, edition_notices, edition_status
from identifier_schema import LANGUAGE, TAG, doi_history, edition_origin, valid_record_url, valid_utc, version_key, work_citation_version
from issuance import check_authorization, read_registration
from verify_edition import verify_edition
from bibliography import author_text, check_optional_bibliography, ordered_authors


EDITION_RECORDS = Path("records/editions")
WORK_RECORDS = Path("records/works")
REGISTRATIONS = Path("registrations/editions")
# This standalone stylesheet mirrors edition-v1's reading palette deliberately.
# Work/edition records are regenerated from approved source and reviewed as
# exact bytes; they must not fetch CSS or inherit deployment styling.
PAGE_STYLE = """<style>
:root {
  color-scheme: light dark;
  --press-paper: #faf9f6;
  --press-ink: #222;
  --press-muted: #555d63;
  --press-link: #235b9d;
  --press-visited: #654d84;
  --press-border: #888;
  --press-tint: #f0eee9;
  --press-focus: #235b9d;
  font: 1.0625rem/1.65 Georgia, Cambria, 'Times New Roman', serif;
}
*, *::before, *::after { box-sizing: border-box; }
html { background: var(--press-paper); }
body {
  max-width: 70ch;
  margin: 0 auto;
  padding: 1.5rem clamp(1rem, 4vw, 2.5rem) 3rem;
  background: var(--press-paper);
  color: var(--press-ink);
  overflow-wrap: break-word;
}
h1, h2 { line-height: 1.25; text-wrap: balance; }
h1 { font-size: clamp(1.65rem, 4vw, 2.2rem); margin: 1.5rem 0 1rem; }
h2 { font-size: 1.35rem; margin: 2rem 0 .7rem; }
p { margin: 0 0 1.1em; }
a { color: var(--press-link); text-underline-offset: .12em; }
a:visited { color: var(--press-visited); }
a:focus-visible { outline: 3px solid var(--press-focus); outline-offset: 3px; }
li { margin: .4em 0; }
dt { font-weight: bold; margin-top: .7rem; }
dd { margin: 0 0 .6rem; overflow-wrap: anywhere; }
code { font: .92em/1.45 ui-monospace, Menlo, Consolas, monospace; overflow-wrap: anywhere; }
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --press-paper: #181a1c;
    --press-ink: #e4e4df;
    --press-muted: #b5b9b9;
    --press-link: #96c7ff;
    --press-visited: #d5b6ed;
    --press-border: #868b8e;
    --press-tint: #25292c;
    --press-focus: #a8d2ff;
  }
}
@media print {
  :root {
    color-scheme: light;
    --press-paper: #fff;
    --press-ink: #000;
    --press-muted: #333;
    --press-link: #164a7c;
    --press-visited: #164a7c;
  }
  body { max-width: none; padding: 0; }
}
</style>"""


def doi_link(doi):
    return f'<a href="https://doi.org/{escape(quote(doi, safe="/"))}">{escape(doi)}</a>'


def authored_title(manifest):
    """Mark only reviewed title text, leaving English publisher chrome local."""
    title = escape(manifest["title"])
    if "language" not in manifest:
        # Older verified packages did not capture this field. Preserve their
        # existing output rather than inventing a new language declaration.
        return title
    language = manifest["language"]
    if not isinstance(language, str) or not LANGUAGE.fullmatch(language):
        raise AssetError("public record requires a valid reviewed manuscript language")
    return title if language == "en" else f'<span lang="{escape(language)}">{title}</span>'


def dublin_core(manifest, published_at, identifiers, *, description=False):
    """Describe the identified resource, using its receipt rather than build time."""
    fields = [("DC.title", manifest["title"])]
    fields += [("DC.creator", a["name"]) for a in ordered_authors(manifest, allow_missing=True)]
    fields += [("DC.publisher", "Press"), ("DCTERMS.issued", published_at)]
    fields += [("DC.identifier", value) for value in identifiers]
    if "language" in manifest:
        fields.append(("DC.language", manifest["language"]))
    if description and "abstract" in manifest:
        fields.append(("DC.description", manifest["abstract"]))
    return ['<link rel="schema.DC" href="http://purl.org/dc/elements/1.1/">',
            '<link rel="schema.DCTERMS" href="http://purl.org/dc/terms/">'] + [
        f'<meta name="{name}" content="{escape(value)}">' for name, value in fields]


def edition_citation(manifest, published_at, doi):
    """One visible citation, optionally annotated as static schema.org microdata."""
    schema_type = manifest.get("schema_type")
    scope = f' itemscope itemtype="https://schema.org/{schema_type}"' if schema_type else ""
    def prop(name):
        return f' itemprop="{name}"' if schema_type else ""
    authors = []
    for author in ordered_authors(manifest, allow_missing=True):
        name = escape(author["name"])
        if schema_type:
            name = '<span itemprop="author" itemscope itemtype="https://schema.org/Person">' + \
                   '<span itemprop="name">' + name + '</span>'
            if "orcid" in author:
                orcid = author["orcid"]
                name += f' (<a itemprop="sameAs" href="https://orcid.org/{orcid}">ORCID: {orcid}</a>)'
            name += '</span>'
        authors.append(name)
    attribution = "; ".join(authors)
    publisher = ('<span itemprop="publisher" itemscope itemtype="https://schema.org/Organization">'
                 '<span itemprop="name">Press</span></span>') if schema_type else "Press"
    issued = (f'<time itemprop="datePublished" datetime="{published_at}">{published_at[:10]}</time>'
              if schema_type else published_at[:10])
    doi_html = ("DOI: " + doi_link(doi).replace('<a ', '<a' + prop("identifier") + ' ', 1) + ". ") if doi else ""
    url = escape(manifest["record_url"])
    result = (f'<p{scope}>' + (attribution + ". " if attribution else "") +
              f'<cite{prop("name")}>{authored_title(manifest)}</cite>. {manifest["tag"].rsplit("/", 1)[1]}. '
              f'{publisher}, {issued}. {doi_html}<a{prop("url")} href="{url}">{url}</a>')
    if schema_type:
        for field, name in (("language", "inLanguage"), ("abstract", "description")):
            if field in manifest:
                result += f'<meta itemprop="{name}" content="{escape(manifest[field])}">'
    return result + '</p>'


def record_html(manifest, *, published_at, notices, identifiers) -> bytes:
    check_optional_bibliography(manifest)
    tag = manifest["tag"]
    match = TAG.fullmatch(tag)
    if not match or not valid_record_url(manifest["record_url"], tag) or not valid_utc(published_at):
        raise AssetError("public record requires an edition tag, its permanent address and publication timestamp")
    slug, version = match.groups()
    filename = f"{slug}-{version}.html"
    if manifest["canonical_artifact"] != filename:
        raise AssetError("public record requires the edition’s canonical filename")

    title = escape(manifest["title"])
    visible_title = authored_title(manifest)
    permanent = escape(manifest["record_url"])
    status, successor = edition_status(notices, version)
    version_doi = (identifiers or {}).get("version_doi")
    metadata_ids = [manifest["record_url"]] + (["https://doi.org/" + version_doi] if version_doi else [])
    page = [
        "<!doctype html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f'<link rel="canonical" href="{permanent}">',
        f"<title>{title} — {version} · Press</title>",
        *dublin_core(manifest, published_at, metadata_ids, description=True),
        PAGE_STYLE, "</head>", "<body><main>", "<p>Press · Edition record</p>",
        f"<h1>{visible_title}</h1>", f"<p>Edition {version} · <strong>{status}</strong></p>",
    ]
    if successor:
        page.append(f'<p>The editorial notice named {successor} as the replacement. '
                    f'<a href="{successor}">Check the status of {successor}</a> or '
                    f'<a href="../{slug}">view all editions of this work</a>.</p>')
    page.append(f'<p>First published <time datetime="{published_at}">{published_at}</time></p>')
    if "abstract" in manifest:
        language = f' lang="{escape(manifest["language"])}"' if "language" in manifest else ""
        page.extend(["<h2>Abstract</h2>", f'<p{language}>{escape(manifest["abstract"])}</p>'])
    if notices:
        page.append("<h2>Editorial notices</h2><ul>")
        page.extend(f"<li>{escape(item.removeprefix('- '))}</li>" for item in notices)
        page.append("</ul>")
    read_label = "Read this edition online" if status == "Published" else "Read the archived edition"
    size = f' ({manifest["html_byte_count"]:,} bytes)' if "html_byte_count" in manifest else ""
    page.append(f'<p><a id="press-record-read" href="{version}/read">{read_label}</a>{size}</p>')
    if not successor:
        page.append(f'<p><a href="../{slug}">All editions of this work</a></p>')
    page.append("<h2>Cite this edition</h2>")
    page.append(edition_citation(manifest, published_at, version_doi))
    page.append("<h2>Edition details</h2><dl>")
    attachments = {"Canonical artifact", "Provenance", "Checksums"}
    for label, value in edition_fields(manifest).items():
        value = authored_title(manifest) if label == "Work" else escape(value)
        # Resolve next to the extensionless record URL, including on the recovery host.
        if label in attachments:
            value = f'<a href="{version}/{value}">{value}</a>'
        if label == "Concept DOI":
            label = "Concept DOI at issuance"
            value += f' · <a href="../{slug}">Current work DOI and history</a>'
        page.append(f"<dt>{escape(label)}</dt><dd>{value}</dd>")
    page.append("</dl>")
    if identifiers:
        page.append("<h2>Edition identifiers</h2><ul>")
        for key in sorted(identifiers):
            value = doi_link(identifiers[key]) if key == "version_doi" else escape(identifiers[key])
            page.append(f"<li>{EDITION_ID_LABELS[key]}: {value}</li>")
        page.append("</ul>")
    if manifest.get("initial_note"):
        page.append("<h2>Initial editorial note</h2>")
        page.extend(f"<p>{escape(paragraph)}</p>" for paragraph in manifest["initial_note"].strip().split("\n\n") if paragraph)
    page.extend(["</main></body>", "</html>"])
    return bounded_record(("\n".join(page) + "\n").encode("utf-8"))


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


def work_html(origin, slug, receipts, manifests, notices, work) -> bytes:
    """Render supplied edition metadata; callers establish its authority."""
    # The extensionless URL is a file to the browser. Include the slug in each
    # relative link so it also resolves at a recovery host below a path prefix.
    work_url = f"{origin}/works/{slug}"
    if not receipts or any(not valid_utc(r.get("published_at")) for r in receipts.values()):
        raise AssetError("work citation requires published edition receipts")
    first = min(receipts, key=lambda v: (receipts[v]["published_at"], version_key(v)))
    selected = work_citation_version(work, receipts) or first
    basis = manifests[selected]
    published_at = receipts[first]["published_at"]
    doi = work.get("identifiers", {}).get("concept_doi")
    identifier = "https://doi.org/" + doi if doi else work_url
    page = [
        "<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f'<link rel="canonical" href="{work_url}">',
        f'<title>{escape(basis["title"])} — Editions · Press</title>',
        *dublin_core(basis, published_at, [identifier]), PAGE_STYLE, "</head><body><main>",
        "<p>Press · Work record</p>", f"<h1>Editions of {slug}</h1>",
        "<p>This address identifies the work across editions. To cite a passage, choose its edition below. "
        "Each edition record includes its current status, reading copy and editorial notices.</p>",
        f'<p>Permanent work address: <a href="{work_url}">{work_url}</a></p>',
    ]
    statuses = {version: edition_status(notices[version], version, receipts)
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
    states = {status for status, _ in statuses.values()}
    if states == {"Withdrawn"}:
        page.append("<p><strong>All editions of this work have been withdrawn.</strong></p>")
    elif "Published" not in states:
        page.append("<p><strong>This work has no current edition.</strong></p>")
    history = doi_history(work, "concept_doi")
    assigned = {change["from"] for change in history} | {doi}
    if doi:
        page.append(f"<p>Current concept DOI: {doi_link(doi)}</p>")
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
    attribution = escape(author_text(ordered_authors(basis, allow_missing=True)))
    citation_id = doi_link(doi) if doi else f'<a href="{work_url}">{work_url}</a>'
    page.extend(["<h2>Cite this work</h2>", '<p>' + (attribution + ". " if attribution else "") +
                 f'<cite>{authored_title(basis)}</cite>. Press, {published_at[:4]}. {citation_id}</p>'])
    page.append("<ol>")
    for version in sorted(receipts, key=version_key, reverse=True):
        receipt = receipts[version]
        if receipt["record_url"] != f"{work_url}/{version}":
            raise AssetError(f"{slug}/{version}: receipt uses a different permanent work address")
        manifest = manifests[version]
        title = authored_title(manifest)
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
    return bounded_record("\n".join(page).encode("utf-8"))


def work_projection(root, slug, receipts, *, write=False):
    registration = Path("registrations/works") / (slug + ".json")
    work = read_registration(root, registration) if registration in tracked_paths(root, registration) else {}
    manifests = {version: saved_manifest(root, slug, version, receipt) for version, receipt in receipts.items()}
    notices = {version: edition_notices(root, f"works/{slug}/{version}") for version in receipts}
    html = work_html(edition_origin(root), slug, receipts, manifests, notices, work)
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
