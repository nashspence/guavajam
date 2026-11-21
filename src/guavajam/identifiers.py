"""Syntactic identifier and source metadata checks; no publication registry."""
import re

WORK_IDS = frozenset({"concept_doi", "arxiv", "openalex", "wikidata"})


RIGHTS = frozenset({"all-rights-reserved", "CC-BY-4.0", "CC-BY-ND-4.0", "CC-BY-NC-ND-4.0", "CC0-1.0"})


LANGUAGE = re.compile(r"[a-z]{2,3}(?:-[A-Z]{2})?\Z")


SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


DOI = re.compile(r"10\.[0-9]{4,9}/[a-z0-9][a-z0-9._;()/:+%-]*\Z")


ARXIV_NEW = re.compile(r"[0-9]{2}(?:0[1-9]|1[0-2])\.[0-9]{4,5}\Z")


ARXIV_OLD = re.compile(r"[a-z][a-z0-9.-]*/[0-9]{7}\Z")


def valid_orcid(value: str) -> bool:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9X]", value):
        return False
    digits = value.replace("-", "")
    total = 0
    for digit in digits[:-1]:
        total = (total + int(digit)) * 2
    check = (12 - total % 11) % 11
    return digits[-1] == ("X" if check == 10 else str(check))


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
    if kind == "concept_doi":
        return None if DOI.fullmatch(value) else "must be a lowercase bare DOI (10.prefix/suffix), not a URL"
    if kind == "arxiv":
        return None if valid_arxiv(value) else "must be an unversioned arXiv identifier, without arXiv:"
    if kind == "openalex":
        return None if re.fullmatch(r"W[1-9][0-9]*", value) else "must be a bare OpenAlex W-ID"
    if kind == "wikidata":
        return None if re.fullmatch(r"Q[1-9][0-9]*", value) else "must be a bare Wikidata Q-ID"
    if kind == "orcid":
        return None if valid_orcid(value) else "invalid ORCID iD/check digit; use XXXX-XXXX-XXXX-XXXX"
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

