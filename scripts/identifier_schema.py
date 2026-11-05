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

from asset_policy import AssetError, HOST

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
STAGING_RUN_ID = re.compile(r"[1-9][0-9]*")
DOI = re.compile(r"10\.[0-9]{4,9}/[a-z0-9][a-z0-9._;()/:+%-]*\Z")
ARXIV_NEW = re.compile(r"[0-9]{2}(?:0[1-9]|1[0-2])\.[0-9]{4,5}\Z")
ARXIV_OLD = re.compile(r"[a-z][a-z0-9.-]*/[0-9]{7}\Z")


def version_key(version: str) -> tuple[int, ...]:
    return tuple(map(int, version[1:].split(".")))


def valid_origin(value: str) -> bool:
    return value.startswith("https://") and bool(HOST.fullmatch(value[8:]))


def edition_origin(root: Path) -> str:
    path = root / "edition-origin.txt"
    if path.is_symlink() or not path.is_file():
        raise AssetError("configure edition-origin.txt in the source commit before staging an edition")
    raw = path.read_bytes()
    origin = raw.decode("ascii").removesuffix("\n")
    if not raw.endswith(b"\n") or not valid_origin(origin):
        raise AssetError("edition-origin.txt requires one canonical HTTPS origin and a final newline")
    return origin


def valid_record_url(url: object, tag: str) -> bool:
    suffix = "/" + tag
    return (isinstance(url, str) and TAG.fullmatch(tag) is not None and url.endswith(suffix)
            and valid_origin(url.removesuffix(suffix)))


def edition_record_url(root: Path, tag: str) -> str:
    if not TAG.fullmatch(tag):
        raise AssetError("invalid edition tag")
    return edition_origin(root) + "/" + tag


def require_current_edition_origin(root: Path, manifest: dict) -> None:
    if manifest["record_url"] != edition_record_url(root, manifest["tag"]):
        raise AssetError(f"{manifest['tag']}: staged proof uses a different permanent record origin")

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


def concept_doi_history(record):
    """Check the ordered transitions against the current assignment."""
    if "concept_doi_history" not in record:
        return []
    history = record["concept_doi_history"]
    if not isinstance(history, list) or not history:
        raise AssetError("concept_doi_history must be a nonempty list of dated changes")
    previous_date, retired = "", set()
    for index, change in enumerate(history):
        if not isinstance(change, dict) or set(change) != {"date", "from", "to"}:
            raise AssetError("each concept DOI change requires exactly date, from and to")
        day, old, new = change["date"], change["from"], change["to"]
        if not isinstance(day, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day):
            raise AssetError("concept DOI change requires YYYY-MM-DD")
        try:
            dt.date.fromisoformat(day)
        except ValueError as exc:
            raise AssetError("concept DOI change requires a valid date") from exc
        if day < previous_date:
            raise AssetError("concept DOI change dates must be in order")
        for value in (old, new):
            if value is not None and identifier_error("concept_doi", value):
                raise AssetError("concept DOI change requires a bare DOI or null")
        if index == 0 and old is None:
            raise AssetError("the first concept DOI change must name the initial assignment")
        if old == new or (index and old != history[index - 1]["to"]) or new in retired:
            raise AssetError("concept DOI changes must form a chain without reassigning a retired DOI")
        if old is not None:
            retired.add(old)
        previous_date = day
    identifiers = record.get("identifiers")
    if not isinstance(identifiers, dict) or history[-1]["to"] != identifiers.get("concept_doi"):
        raise AssetError("concept DOI history must end at the current assignment")
    return history


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
            errors.extend(exact_keys(doc, {"schema", "work", "identifiers"}, {"concept_doi_history"}, context))
            if doc.get("schema") != REGISTRY_SCHEMAS["work"] or doc.get("work") != slug:
                errors.append(f"{path}: incorrect work registration schema or work")
            ids = doc.get("identifiers")
            try:
                history = concept_doi_history(doc)
            except AssetError as exc:
                errors.append(f"{path}: {exc}")
                history = []
            if not (ids == {} and history):
                errors.extend(check_identifier_map(ids, WORK_IDS, context))
            for change in history:
                register_identifiers("work", {"concept_doi": change["from"]}, Path(f"works/{slug}/manuscript.md"))
            front_doi = source_ids.get(slug, {}).get("concept_doi")
            if history and front_doi and front_doi != ids.get("concept_doi"):
                errors.append(f"{path}: manuscript concept DOI conflicts with the current assignment")
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
            required = {"schema", "work", "version", "record_url", "canonical_artifact", "rights",
                        "artifact_sha256", "source_commit", "provenance_sha256", "initial_notes_sha256"}
            errors.extend(exact_keys(doc, required, {"staged_run_id", "identifiers", "issuing", "published_at", "cancellation_reason"}, context))
            if "staged_run_id" in doc and (not isinstance(doc["staged_run_id"], str)
                                          or not STAGING_RUN_ID.fullmatch(doc["staged_run_id"])):
                errors.append(f"{path}: staged_run_id must be a positive decimal run ID string")
            if doc.get("schema") != REGISTRY_SCHEMAS["edition"] or doc.get("work") != slug or doc.get("version") != version:
                errors.append(f"{path}: edition schema, work and version must match path")
            if not valid_record_url(doc.get("record_url"), f"works/{slug}/{version}"):
                errors.append(f"{path}: record_url must identify this edition at a canonical HTTPS origin")
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
            if "issuing" in doc and doc["issuing"] is not True:
                errors.append(f"{path}: issuing must be true when present")
            if "published_at" in doc and doc.get("issuing") is not True:
                errors.append(f"{path}: a publication receipt requires an issuing claim")
            if "published_at" in doc and not valid_utc(doc["published_at"]):
                errors.append(f"{path}: published_at must be a valid UTC timestamp (YYYY-MM-DDTHH:MM:SSZ)")
            if "cancellation_reason" in doc:
                reason = doc["cancellation_reason"]
                if (not isinstance(reason, str) or not reason or reason.strip() != reason
                        or not reason.isprintable() or len(reason) > 500):
                    errors.append(f"{path}: cancellation_reason requires a single printable line of at most 500 characters")
                if "issuing" in doc or "published_at" in doc:
                    errors.append(f"{path}: a claimed or published edition cannot be cancelled")
            rights = doc.get("rights")
            if not isinstance(rights, str) or rights not in RIGHTS:
                errors.append(f"{path}: rights must be an approved rights identifier")
            if "identifiers" in doc:
                if "published_at" not in doc:
                    errors.append(f"{path}: edition identifiers require a publication receipt")
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
