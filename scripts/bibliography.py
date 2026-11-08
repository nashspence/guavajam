"""The bounded bibliographic facts shared by source, edition and record checks."""
import unicodedata

from asset_policy import AssetError
from identifier_schema import LANGUAGE, identifier_error

MAX_AUTHORS = 32
MAX_ABSTRACT_BYTES = 4096
SCHEMA_TYPES = frozenset({"CreativeWork", "ScholarlyArticle"})


def plain_string(value):
    return (isinstance(value, str) and bool(value) and value.strip() == value
            and value.isprintable() and unicodedata.normalize("NFC", value) == value)


def ordered_authors(facts):
    """Normalize either supported authorship representation for consumption."""
    if "authors" in facts:
        if "author" in facts or "author_orcid" in facts:
            raise AssetError("use exactly one authorship representation: author or authors")
        authors = facts["authors"]
        if not isinstance(authors, list) or not 1 <= len(authors) <= MAX_AUTHORS:
            raise AssetError(f"authors must contain 1–{MAX_AUTHORS} ordered name/orcid mappings")
        for author in authors:
            if (not isinstance(author, dict) or "name" not in author or set(author) - {"name", "orcid"}
                    or not plain_string(author["name"]) or len(author["name"].encode("utf-8")) > 512):
                raise AssetError("each authors entry requires a plain name (at most 512 UTF-8 bytes) and optional orcid only")
    elif "author" in facts:
        if not plain_string(facts["author"]):
            raise AssetError("author must be a nonempty plain-text string")
        authors = [{"name": facts["author"]}]
        if "author_orcid" in facts:
            authors[0]["orcid"] = facts["author_orcid"]
    else:
        raise AssetError("schema keys mismatch: require exactly one of author or authors; author_orcid requires author")
    seen = set()
    for author in authors:
        if "orcid" in author:
            message = identifier_error("orcid", author["orcid"])
            if message:
                raise AssetError("author ORCID: " + message)
            if author["orcid"] in seen:
                raise AssetError("an ORCID may belong to only one ordered author")
            seen.add(author["orcid"])
    return authors


def reviewed_language(facts):
    language = facts.get("language")
    if not isinstance(language, str) or not LANGUAGE.fullmatch(language):
        raise AssetError("publication requires a valid reviewed manuscript language")
    return language


def check_optional_bibliography(facts):
    if "abstract" in facts and (not plain_string(facts["abstract"])
            or len(facts["abstract"].encode("utf-8")) > MAX_ABSTRACT_BYTES):
        raise AssetError("abstract must be one nonempty plain-text line of at most 4 KiB")
    if "schema_type" in facts and (not isinstance(facts["schema_type"], str)
                                  or facts["schema_type"] not in SCHEMA_TYPES):
        raise AssetError("schema_type must be CreativeWork or ScholarlyArticle when supplied")


def author_text(authors, *, identifiers=False):
    return "; ".join(author["name"] + (f' (ORCID: {author["orcid"]})'
                    if identifiers and "orcid" in author else "") for author in authors)
