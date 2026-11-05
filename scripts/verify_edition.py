#!/usr/bin/env python3
"""Verify a packaged edition offline, including the image bytes inside its HTML."""
from __future__ import annotations

import argparse
import base64
import hashlib
import html.parser
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

from asset_policy import AssetError, AssetPolicy, MAX_ASSETS, MAX_TOTAL_BYTES, MIME, check_html_size, verify_bytes
from editorial_notices import check_initial_notes, check_rights_statement, reviewed_initial_body
from identifier_schema import WORK_IDS, check_identifier_map, identifier_error, valid_record_url, valid_utc
from render_profile import FORMAL_KINDS, profile_paths
from validate_sources import valid_edition_link


class EditionHTML(html.parser.HTMLParser):
    def __init__(self, profile: str):
        super().__init__(convert_charrefs=True)
        self.profile = profile
        self.images = {}
        self.errors = []
        self.style = False
        self.h1 = 0
        self.csp = False
        self.main_count = 0
        self.article_count = 0
        self.document_profile = False
        self.profile_meta = False
        self.style_count = 0
        self.ids = set()
        self.fragment_links = []
        self.record_links = []
        self.read_links = []
        self.status_links = []
        self.authors = []
        self.permalinks = []
        self.current_heading = None
        self.in_footer = False
        self.in_rights = False
        self.rights_text = None
        self.heading_ids = []
        self.formal_kinds = []
        self.viewport = False
        self.html_lang = ""

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if self.in_rights:
            self.errors.append("edition rights must contain only plain text")
        if tag == "footer":
            self.in_footer = True
        if attributes.get("data-press-field") == "rights":
            if self.rights_text is not None:
                self.errors.append("duplicate edition rights statement")
            if tag != "span" or not self.in_footer or attrs != [("data-press-field", "rights")]:
                self.errors.append("edition rights require a plain span in the footer")
            self.in_rights = True
            self.rights_text = []
        if tag in {"script", "iframe", "object", "embed", "audio", "video", "source", "link", "base"}:
            self.errors.append("HTML contains executable or external resource element")
        if any(key.startswith("on") or key in {"srcset", "poster", "background"} for key in attributes):
            self.errors.append("HTML contains active or external resource attribute")
        if "src" in attributes and tag != "img":
            self.errors.append("HTML contains an unexpected resource")
        if tag == "style":
            self.style = True
            self.style_count += 1
            if attributes.get("id") != "press-edition-css":
                self.errors.append("unexpected stylesheet element")
        if tag == "html":
            self.html_lang = attributes.get("lang", "").strip()
        if tag == "main":
            self.main_count += 1
        if tag == "article":
            self.article_count += 1
            self.document_profile = (attributes.get("data-press-profile") == self.profile
                                     and attributes.get("class") == "press-edition")
        if tag == "h1":
            self.h1 += 1
        if tag in {"h2", "h3", "h4", "h5", "h6"}:
            heading_id = attributes.get("id", "")
            if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", heading_id):
                self.errors.append("section heading requires an explicit stable ID")
            self.heading_ids.append(heading_id)
            self.current_heading = heading_id
            kind = attributes.get("data-press-kind")
            if kind is not None:
                classes = set(attributes.get("class", "").split())
                if (kind not in FORMAL_KINDS or "press-formal-heading" not in classes
                        or "press-" + kind not in classes):
                    self.errors.append("unrecognized formal heading semantics")
                self.formal_kinds.append(kind)
        identifier = attributes.get("id")
        if identifier:
            if identifier in self.ids:
                self.errors.append("duplicate HTML id: " + identifier)
            self.ids.add(identifier)
        if tag == "a" and "href" in attributes:
            target = attributes["href"] or ""
            if identifier == "press-edition-record":
                self.record_links.append(target)
            if identifier == "press-edition-read":
                self.read_links.append(target)
            if identifier == "press-edition-status":
                self.status_links.append(target)
                if self.article_count:
                    self.errors.append("edition status must precede the manuscript")
            if "press-permalink" in attributes.get("class", "").split():
                self.permalinks.append((self.current_heading, target))
                if not attributes.get("aria-label", "").strip() or "tabindex" in attributes:
                    self.errors.append("section permalink requires an accessible, keyboard-focusable link")
            if target.startswith("#"):
                if target[1:]:
                    self.fragment_links.append(unquote(target[1:]))
            elif not valid_edition_link(target):
                self.errors.append("standalone HTML link requires a fragment, HTTPS or mailto URL")
        if tag == "meta":
            if attributes.get("name") == "author":
                self.authors.append(attributes.get("content"))
            if attributes.get("name") == "press-presentation-profile":
                self.profile_meta = attributes.get("content") == self.profile
            if attributes.get("name") == "viewport":
                self.viewport = attributes.get("content") == "width=device-width, initial-scale=1"
            equiv = attributes.get("http-equiv", "").lower()
            if equiv == "refresh":
                self.errors.append("HTML refresh prohibited")
            if equiv == "content-security-policy":
                self.csp = "default-src 'none'" in attributes.get("content", "")
        if tag == "img":
            if not attributes.get("alt", "").strip():
                self.errors.append("image alt text required")
            match = re.fullmatch(r"data:(image/(?:png|jpeg|webp|svg\+xml));base64,([A-Za-z0-9+/=]+)",
                                 attributes.get("src", ""))
            if not match:
                self.errors.append("edition image is not embedded")
                return
            try:
                raw = base64.b64decode(match[2], validate=True)
            except ValueError:
                self.errors.append("invalid embedded image encoding")
                return
            digest = hashlib.sha256(raw).hexdigest()
            self.images[digest] = (raw, match[1])

    def handle_endtag(self, tag):
        if tag in {"h2", "h3", "h4", "h5", "h6"}:
            self.current_heading = None
        if tag == "style":
            self.style = False
        if tag == "footer":
            self.in_footer = False
        if self.in_rights:
            if tag != "span":
                self.errors.append("edition rights span is not properly closed")
            self.in_rights = False

    def handle_data(self, data):
        if self.in_rights:
            self.rights_text.append(data)
        if self.style and re.search(r"url\s*\(|@import|\\", data, re.I):
            self.errors.append("edition CSS may not load external resources")

    handle_startendtag = handle_starttag


def verify_edition(directory: Path, *, commit: str | None = None, tag: str | None = None) -> dict:
    from build_edition import COMMIT, TAG
    manifest = json.loads((directory / "provenance.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != "press.edition.v1":
        raise AssetError("unsupported edition provenance schema")
    if "record_url" in manifest or "initial_release_body" in manifest:
        reviewed_initial_body(manifest)
    if "rights_statement" in manifest:
        check_rights_statement(manifest["rights_statement"])
    if "initial_note" in manifest:
        check_initial_notes(manifest["initial_note"])
    source_commit = manifest.get("source_commit", "")
    edition_tag = manifest.get("tag", "")
    tag_match = TAG.fullmatch(edition_tag)
    if not COMMIT.fullmatch(source_commit) or tag_match is None:
        raise AssetError("invalid edition commit or tag")
    if (commit is not None and source_commit != commit) or (tag is not None and edition_tag != tag):
        raise AssetError("edition provenance does not match reviewed commit/tag")
    filename = tag_match[1] + "-" + tag_match[2] + ".html"
    if manifest.get("canonical_artifact") != filename:
        raise AssetError("canonical filename does not match edition tag")
    names = {filename, "provenance.json", "SHA256SUMS"}
    if {path.name for path in directory.iterdir()} != names:
        raise AssetError("edition must contain exactly HTML, provenance.json and SHA256SUMS")
    if any((directory / name).is_symlink() or not (directory / name).is_file() for name in names):
        raise AssetError("edition files must be regular files")
    check_html_size((directory / filename).stat().st_size)
    checksums = "".join(hashlib.sha256((directory / name).read_bytes()).hexdigest() + "  " + name + "\n"
                        for name in [filename, "provenance.json"])
    if (directory / "SHA256SUMS").read_bytes() != checksums.encode("ascii"):
        raise AssetError("edition checksums do not match")
    raw = (directory / filename).read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest["html_sha256"] or len(raw) != manifest["html_byte_count"]:
        raise AssetError("canonical HTML digest or byte count does not match provenance")
    profile = manifest.get("presentation_profile")
    template_path, stylesheet_path = profile_paths(profile)
    parser = EditionHTML(profile)
    parser.feed(raw.decode("utf-8"))
    parser.close()
    statement = manifest.get("rights_statement")
    if parser.in_rights or (parser.rights_text is None) != (statement is None):
        raise AssetError("edition rights declaration differs between HTML and provenance")
    if statement is not None:
        # HTML collapses ASCII whitespace, including line breaks inserted by Pandoc.
        rendered = re.sub(r"[ \t\r\n\f]+", " ", "".join(parser.rights_text)).strip(" ")
        if rendered != re.sub(r" +", " ", statement):
            raise AssetError("edition rights text differs from provenance")
    if "record_url" in manifest or parser.record_links:
        if (not valid_record_url(manifest.get("record_url"), edition_tag)
                or parser.record_links != [manifest["record_url"]]):
            raise AssetError("edition record link differs from its permanent address in provenance")
    if "initial_release_body" in manifest:
        record_url = manifest["record_url"]
        if parser.read_links != [record_url + "/read"]:
            raise AssetError("edition reading link differs from its permanent address in provenance")
        if parser.status_links != [record_url]:
            raise AssetError("edition status link differs from its permanent record")
        if parser.permalinks != [(key, record_url + "/read#" + key) for key in parser.heading_ids]:
            parser.errors.append("every section requires its own canonical permalink")
        if (not manifest.get("author") or parser.authors != [manifest["author"]]
                or parser.html_lang != manifest.get("language")):
            raise AssetError("edition attribution or language differs from provenance")
        if "author_orcid" in manifest and identifier_error("orcid", manifest["author_orcid"]):
            raise AssetError("invalid edition author ORCID")
        if "work_identifiers" in manifest:
            errors = check_identifier_map(manifest["work_identifiers"], WORK_IDS, edition_tag)
            if errors:
                raise AssetError("\n".join(errors))
    if (not re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("builder_commit", "")))
            or manifest.get("template_path") != template_path
            or manifest.get("stylesheet_path") != stylesheet_path
            or not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get("template_sha256", "")))
            or not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get("stylesheet_sha256", "")))):
        raise AssetError("invalid versioned presentation provenance")
    # Verify the exact CSS bytes embedded in the issued artifact; no build
    # checkout or remote stylesheet is needed for offline verification.
    match = re.search(rb'<style id="press-edition-css">\n(.*?)</style>', raw, re.DOTALL)
    if (not match or len(re.findall(rb'<style\b', raw, re.IGNORECASE)) != 1
            or hashlib.sha256(match.group(1)).hexdigest() != manifest["stylesheet_sha256"]):
        raise AssetError("embedded CSS differs from stylesheet provenance")
    unresolved = set(parser.fragment_links) - parser.ids
    if unresolved:
        parser.errors.append("unresolved HTML fragment links: " + repr(sorted(unresolved)))
    if (parser.errors or parser.h1 != 1 or not parser.csp or not parser.viewport
            or not parser.html_lang or not parser.document_profile or not parser.profile_meta
            or parser.main_count != 1 or parser.article_count != 1 or parser.style_count != 1):
        raise AssetError("standalone HTML presentation verification failed: " + "; ".join(parser.errors))
    records = manifest.get("assets", [])
    if not isinstance(records, list) or len(records) > MAX_ASSETS:
        raise AssetError("invalid edition asset inventory")
    total = 0
    digests = set()
    policy = AssetPolicy(manifest["asset_origin"]) if manifest.get("asset_origin") else None
    for record in records:
        destination = record["destination"]
        match = re.fullmatch(r"verified/([0-9a-f]{64})\.(png|jpg|jpeg|webp|svg)", destination)
        if not match or record["sha256"] != match[1]:
            raise AssetError("invalid asset provenance destination")
        digest, extension = match.groups()
        if record["source_kind"] == "https":
            if policy is None or policy.parse(record["source_url"]) != (digest, extension):
                raise AssetError("asset provenance URL mismatch")
        elif record["source_kind"] == "git":
            prefix = f"git:{source_commit}:works/{tag_match[1]}/assets/"
            if not record["source_url"].startswith(prefix):
                raise AssetError("local asset provenance mismatch")
        else:
            raise AssetError("unsupported asset provenance source")
        if digest not in parser.images:
            raise AssetError("verified image is missing from canonical HTML")
        data, media_type = parser.images[digest]
        if len(data) != record["byte_count"] or media_type != record["media_type"]:
            raise AssetError("embedded image does not match provenance")
        verify_bytes(data, extension, media_type)
        digests.add(digest)
        total += len(data)
    if total > MAX_TOTAL_BYTES or set(parser.images) != digests:
        raise AssetError("HTML asset inventory differs from provenance or exceeds size limit")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--commit")
    parser.add_argument("--tag")
    parser.add_argument("--registration", type=Path, help="also compare with an independently obtained authorization")
    args = parser.parse_args()
    try:
        manifest = verify_edition(args.directory, commit=args.commit, tag=args.tag)
        result = "Package verified offline"
        if args.registration is not None:
            from issuance import check_authorization, read_registration
            record = read_registration(Path.cwd(), args.registration)
            check_authorization(record, args.directory, manifest)
            result = "Package matches the pending authorization"
            if "published_at" in record:
                if not valid_utc(record["published_at"]):
                    raise AssetError("invalid publication timestamp in trusted registration")
                result = "Package matches the publication receipt of " + record["published_at"]
    except (AssetError, OSError, ValueError, KeyError, TypeError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    print(result + ":", manifest["canonical_artifact"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
