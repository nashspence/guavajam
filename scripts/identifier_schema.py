#!/usr/bin/env python3
"""Offline identifier and registration validation for Press schema v1.

Validates *syntax*, basic checksums, uniqueness, scope and canonical JSON only.
It cannot prove that a registry actually issued an identifier or that a release
was officially published. No network lookups are made.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata
from pathlib import Path

WORK_IDS = frozenset({"concept_doi", "arxiv", "openalex", "wikidata"})
EDITION_IDS = frozenset({"version_doi", "arxiv_version", "isbn_epub", "isbn_pdf", "isbn_print", "oclc"})
AUTHOR_IDS = frozenset({"orcid", "isni", "wikidata", "openalex_author"})
IMPRINT_IDS = frozenset({"isni", "wikidata", "openalex_publisher"})
SERIES_IDS = frozenset({"issn", "issn_l", "wikidata"})
REGISTRY_SCHEMAS = {
    "work": "press.work-registration.v1",
    "edition": "press.edition-registration.v1",
    "identity": "press.identity.v1",
    "series": "press.series.v1",
}
RIGHTS = frozenset({"all-rights-reserved", "CC-BY-4.0", "CC-BY-ND-4.0", "CC-BY-NC-ND-4.0", "CC0-1.0"})
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
VERSION = re.compile(r"v(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){2}")
TAG = re.compile(rf"works/({SLUG.pattern})/({VERSION.pattern})")
DOI = re.compile(r"10\.[0-9]{4,9}/[a-z0-9][a-z0-9._;()/:+%-]*\Z")
ARXIV_NEW = re.compile(r"[0-9]{2}(?:0[1-9]|1[0-2])\.[0-9]{4,5}\Z")
ARXIV_OLD = re.compile(r"[a-z][a-z0-9.-]*/[0-9]{7}\Z")


def edition_release_url(tag: str) -> str:
    """Canonical public record for an edition of this imprint."""
    return f"https://github.com/nashspence/press/releases/tag/{tag}"


def valid_orcid(value: str) -> bool:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9X]", value):
        return False
    digits = value.replace("-", "")
    total = 0
    for digit in digits[:-1]:
        total = (total + int(digit)) * 2
    check = (12 - total % 11) % 11
    return digits[-1] == ("X" if check == 10 else str(check))


def valid_isni(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9]{4} [0-9]{4} [0-9]{4} [0-9]{3}[0-9X]", value)) and valid_orcid(value.replace(" ", "-"))


def valid_issn(value: str) -> bool:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{3}[0-9X]", value):
        return False
    digits = value.replace("-", "")
    checksum = (11 - sum(int(d) * (8-i) for i, d in enumerate(digits[:7])) % 11) % 11
    return digits[-1] == ("X" if checksum == 10 else str(checksum))


def valid_isbn(value: str) -> bool:
    if not re.fullmatch(r"(?:978|979)[0-9]{10}", value):
        return False
    checksum = (10 - sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(value[:12])) % 10) % 10
    return int(value[-1]) == checksum


def valid_arxiv(value: str) -> bool:
    if ARXIV_OLD.fullmatch(value):
        return True
    if not ARXIV_NEW.fullmatch(value):
        return False
    # Four digits through 2014; five digits from 2015 onward.
    yr = int(value[:2])
    mo = int(value[2:4])
    return (yr == 7 and mo >= 4 and len(value.split(".")[1]) == 4) or (8 <= yr <= 14 and len(value.split(".")[1]) == 4) or (yr >= 15 and len(value.split(".")[1]) == 5)


def identifier_error(kind: str, value: object) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip() or "\n" in value:
        return "must be a nonempty, single-line string without surrounding whitespace"
    if kind in {"concept_doi", "version_doi"}:
        return None if DOI.fullmatch(value) else "must be a lowercase bare DOI (10.prefix/suffix), not a URL"
    if kind == "arxiv":
        return None if valid_arxiv(value) else "must be an unversioned arXiv identifier, without arXiv:"
    if kind == "arxiv_version":
        match = re.fullmatch(r"(.+)v([1-9][0-9]*)", value)
        return None if match and valid_arxiv(match.group(1)) else "must be an arXiv identifier suffixed with v1, v2, ..."
    if kind in {"openalex", "openalex_author", "openalex_publisher"}:
        prefix = {"openalex": "W", "openalex_author": "A", "openalex_publisher": "P"}[kind]
        return None if re.fullmatch(prefix + r"[1-9][0-9]*", value) else f"must be a bare OpenAlex {prefix}-ID"
    if kind == "wikidata":
        return None if re.fullmatch(r"Q[1-9][0-9]*", value) else "must be a bare Wikidata Q-ID"
    if kind == "orcid":
        return None if valid_orcid(value) else "invalid ORCID iD/check digit; use XXXX-XXXX-XXXX-XXXX"
    if kind == "isni":
        return None if valid_isni(value) else "invalid ISNI/check digit; use XXXX XXXX XXXX XXXX"
    if kind in {"issn", "issn_l"}:
        return None if valid_issn(value) else "invalid ISSN/check digit; use XXXX-XXXX"
    if kind.startswith("isbn_"):
        return None if valid_isbn(value) else "invalid ISBN-13/check digit; use 13 digits without hyphens"
    if kind == "oclc":
        return None if re.fullmatch(r"[1-9][0-9]*", value) else "OCLC bibliographic number must use decimal digits"
    return "unsupported identifier namespace"


def check_identifier_map(identifiers: object, allowed: frozenset[str], context: str) -> list[str]:
    errors = []
    if not isinstance(identifiers, dict) or not identifiers:
        return [f"{context}: identifiers must be a nonempty mapping when present"]
    extra = set(identifiers) - allowed
    if extra:
        errors.append(f"{context}: unknown identifier keys: {sorted(extra)}")
    for key in sorted(set(identifiers) & allowed):
        msg = identifier_error(key, identifiers[key])
        if msg:
            errors.append(f"{context}: identifiers.{key} {msg}")
    return errors


def strict_json(raw: bytes, relative: Path) -> tuple[dict | None, list[str]]:
    errors = []
    try:
        source = raw.decode("utf-8", "strict")
    except UnicodeDecodeError:
        return None, [f"{relative}: must be UTF-8"]
    if source.startswith("\ufeff") or "\r" in source or unicodedata.normalize("NFC", source) != source:
        errors.append(f"{relative}: must be UTF-8 NFC, LF, no BOM")
    def pairs(v):
        obj = {}
        for k, value in v:
            if k in obj:
                raise ValueError("duplicate key " + k)
            obj[k] = value
        return obj
    try:
        doc = json.loads(source, object_pairs_hook=pairs, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except (ValueError, json.JSONDecodeError) as exc:
        return None, errors + [f"{relative}: invalid or duplicate-key JSON: {exc}"]
    if not isinstance(doc, dict):
        return None, errors + [f"{relative}: JSON root must be an object"]
    canonical = json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if source != canonical:
        errors.append(f"{relative}: canonical JSON required (sorted keys, indent=2, final newline)")
    return doc, errors


def exact_keys(doc: dict, required: set[str], optional: set[str], context: str) -> list[str]:
    missing, unknown = required - set(doc), set(doc) - required - optional
    return [f"{context}: invalid fields; missing={sorted(missing)}, unknown={sorted(unknown)}"] if missing or unknown else []


def valid_utc(value: object) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value):
        return False
    try:
        dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        return True
    except ValueError:
        return False


def validate_registrations(root: Path, paths: set[Path], work_frontmatter: dict[Path, dict]) -> list[str]:
    """Offline verification of optional registration records in tracked tree."""
    errors = []
    paths = set(paths)
    seen: dict[tuple[str, str], tuple[str, str]] = {}
    has_work = {p.parts[1] for p in paths if len(p.parts) == 3 and p.parts[0] == "works" and p.name == "manuscript.md"}

    def register_identifiers(scope: str, identifiers: dict, where: Path):
        for key, value in identifiers.items():
            if not isinstance(value, str) or identifier_error(key, value):
                continue
            namespace = ("isbn" if key.startswith("isbn_") else "doi" if key.endswith("_doi") else "arxiv" if key.startswith("arxiv") else key)
            key_value = (namespace, value.lower() if namespace == "doi" else value)
            prev = seen.get(key_value)
            owner = (str(where), key)
            if prev and prev != owner:
                errors.append(f"{where}: identifier {key} reused across records/formats; first found at {prev[0]} ({prev[1]})")
            else:
                seen[key_value] = owner

    source_ids = {}
    for rel, data in work_frontmatter.items():
        slug = rel.parts[1]
        source_ids[slug] = data.get("identifiers") if isinstance(data.get("identifiers"), dict) else {}
        register_identifiers("work", source_ids[slug], rel)

    for path in sorted(paths):
        if not path.parts or path.parts[0] != "registrations":
            continue
        if path == Path("registrations/README.md"):
            continue
        if path.suffix != ".json" or len(path.parts) not in (3, 4):
            errors.append(f"{path}: unsupported registration file location")
            continue
        category = path.parts[1]
        if category not in {"works", "editions", "identities", "series"}:
            errors.append(f"{path}: unsupported registration record category")
            continue
        file_path = root / path
        if not file_path.is_file() or file_path.is_symlink():
            errors.append(f"{path}: registry record must be a tracked regular file, not a symlink")
            continue
        try:
            raw = file_path.read_bytes()
        except OSError as exc:
            errors.append(f"{path}: registry record unreadable: {exc}")
            continue
        doc, e = strict_json(raw, path)
        errors.extend(e)
        if doc is None:
            continue
        context = str(path)
        if category == "works":
            slug = path.stem
            if len(path.parts) != 3 or not SLUG.fullmatch(slug) or slug not in has_work:
                errors.append(f"{path}: work registration must point to an existing work slug")
            errors.extend(exact_keys(doc, {"schema", "work", "identifiers"}, set(), context))
            if doc.get("schema") != REGISTRY_SCHEMAS["work"] or doc.get("work") != slug:
                errors.append(f"{path}: incorrect work registration schema or work")
            ids = doc.get("identifiers")
            errors.extend(check_identifier_map(ids, WORK_IDS, context))
            if isinstance(ids, dict):
                for key, value in ids.items():
                    if key in source_ids.get(slug, {}) and source_ids[slug][key] != value:
                        errors.append(f"{path}: identifier {key} conflicts with manuscript front matter")
                register_identifiers("work", ids, Path(f"works/{slug}/manuscript.md"))
        elif category == "editions":
            if len(path.parts) != 4:
                errors.append(f"{path}: expected registrations/editions/<slug>/vX.Y.Z.json")
                continue
            slug, version = path.parts[2], path.stem
            if not SLUG.fullmatch(slug) or slug not in has_work or not VERSION.fullmatch(version):
                errors.append(f"{path}: invalid or unknown edition slug/version")
            required = {"schema", "work", "version", "release_url", "canonical_artifact", "rights",
                        "artifact_sha256", "source_commit", "provenance_sha256", "initial_notes_sha256"}
            errors.extend(exact_keys(doc, required, {"identifiers", "published_at"}, context))
            if doc.get("schema") != REGISTRY_SCHEMAS["edition"] or doc.get("work") != slug or doc.get("version") != version:
                errors.append(f"{path}: edition schema, work and version must match path")
            if doc.get("release_url") != edition_release_url(f"works/{slug}/{version}"):
                errors.append(f"{path}: release_url must identify the matching GitHub Release tag")
            asset = doc.get("canonical_artifact")
            if asset != f"{slug}-{version}.html":
                errors.append(f"{path}: canonical_artifact must name the matching edition HTML")
            commit = doc.get("source_commit")
            if not isinstance(commit, str) or not re.fullmatch(r"[a-f0-9]{40}", commit):
                errors.append(f"{path}: source_commit must be a full lowercase Git commit SHA")
            for field in ("artifact_sha256", "provenance_sha256", "initial_notes_sha256"):
                digest = doc.get(field)
                if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                    errors.append(f"{path}: {field} must be lowercase hexadecimal SHA-256")
            if "published_at" in doc and not valid_utc(doc["published_at"]):
                errors.append(f"{path}: published_at must be a valid UTC timestamp (YYYY-MM-DDTHH:MM:SSZ)")
            rights = doc.get("rights")
            if not isinstance(rights, str) or rights not in RIGHTS:
                errors.append(f"{path}: rights must be an approved rights identifier")
            if "identifiers" in doc:
                errors.extend(check_identifier_map(doc["identifiers"], EDITION_IDS, context))
                if isinstance(doc["identifiers"], dict):
                    register_identifiers("edition", doc["identifiers"], path)
                    concept = source_ids.get(slug, {}).get("concept_doi")
                    if concept and doc["identifiers"].get("version_doi") == concept:
                        errors.append(f"{path}: version_doi must differ from concept_doi")
        elif category == "identities":
            identity_type = path.stem
            if len(path.parts) != 3 or identity_type not in {"author", "imprint"}:
                errors.append(f"{path}: identities only permit author.json or imprint.json")
                continue
            errors.extend(exact_keys(doc, {"schema", "type", "name", "identifiers"}, set(), context))
            if doc.get("schema") != REGISTRY_SCHEMAS["identity"] or doc.get("type") != identity_type:
                errors.append(f"{path}: identity schema/type mismatch")
            if not isinstance(doc.get("name"), str) or not doc["name"].strip():
                errors.append(f"{path}: identity name must be nonempty")
            identifiers = doc.get("identifiers")
            errors.extend(check_identifier_map(identifiers, AUTHOR_IDS if identity_type == "author" else IMPRINT_IDS, context))
            if isinstance(identifiers, dict):
                register_identifiers(identity_type, identifiers, path)
        elif category == "series":
            slug = path.stem
            if len(path.parts) != 3 or not SLUG.fullmatch(slug):
                errors.append(f"{path}: series path must use a slug")
            errors.extend(exact_keys(doc, {"schema", "slug", "title", "identifiers"}, set(), context))
            if doc.get("schema") != REGISTRY_SCHEMAS["series"] or doc.get("slug") != slug:
                errors.append(f"{path}: series schema/slug mismatch")
            if not isinstance(doc.get("title"), str) or not doc["title"].strip():
                errors.append(f"{path}: series title must be nonempty")
            identifiers = doc.get("identifiers")
            errors.extend(check_identifier_map(identifiers, SERIES_IDS, context))
            if isinstance(identifiers, dict):
                register_identifiers("series", identifiers, path)
    return errors
