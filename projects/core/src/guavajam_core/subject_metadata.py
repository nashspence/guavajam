"""Bounded author declarations, with no prescribed taxonomy or authority lookup.

The subject record is a local spelling of DataCite 4.7 Subject:
https://datacite-metadata-schema.readthedocs.io/en/4.7/properties/subject/
subject_scheme / scheme_uri / value_uri / classification_code / language map
to subjectScheme / schemeURI / valueURI / classificationCode / xml:lang.
Free-text keywords also map to DataCite Subject. This is an authoring profile,
not a complete DataCite record or a claim that a declared term is authoritative.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from .asset_policy import AssetError
from .bibliography import plain_string

# Operational bounds for GuavaJam; these are not vocabulary restrictions.
MAX_TERMS = 32
MAX_TERM_BYTES = 512
MAX_SUBJECT_URI_BYTES = 2048
SUBJECT_FIELDS = frozenset({"subject", "subject_scheme", "scheme_uri", "value_uri",
                            "classification_code", "language"})
SUBJECT_LANGUAGE = re.compile(r"[A-Za-z]{1,8}(?:-[A-Za-z0-9]{1,8})*\Z")
URI = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+\Z")
# RFC 3986 components: percent escapes are checked separately above parsing.
# https://www.rfc-editor.org/rfc/rfc3986#appendix-A
REG_NAME = r"[A-Za-z0-9._~!$&'()*+,;=%-]*"
USER_INFO = re.compile(r"[A-Za-z0-9._~!$&'()*+,;=:%-]*\Z")
URI_PATH = re.compile(r"[A-Za-z0-9._~!$&'()*+,;=:@/%-]*\Z")
URI_QUERY = re.compile(r"[A-Za-z0-9._~!$&'()*+,;=:@/?%-]*\Z")
HOST_PORT = re.compile(r"(?:" + REG_NAME + r"|\[[^\[\]]+\])(?::[0-9]*)?\Z")


def absolute_subject_uri(value: str) -> bool:
    """Check identifier syntax offline; URNs and other absolute URIs are valid.

This does not make the identifier a safe clickable web link. Consumers must
apply their own presentation policy and must not dereference it implicitly.
"""
    if not URI.fullmatch(value) or re.search(r"%(?![0-9A-Fa-f]{2})", value):
        return False
    try:
        parsed = urlsplit(value)
        if (not URI_PATH.fullmatch(parsed.path) or not URI_QUERY.fullmatch(parsed.query)
                or not URI_QUERY.fullmatch(parsed.fragment)):
            return False
        authority = parsed.netloc
        if '@' in authority:
            user, authority = authority.rsplit('@', 1)
            if not USER_INFO.fullmatch(user):
                return False
        # urlsplit checks bracketed IP literals; this rejects stray characters
        # after a bracketed host, extra userinfo separators and nonnumeric ports.
        if not HOST_PORT.fullmatch(authority):
            return False
        if parsed.scheme in {"http", "https"} and not parsed.hostname:
            return False
        return bool(parsed.scheme)
    except ValueError:
        return False


def check_subject_metadata(facts: dict) -> None:
    """Validate literal values while retaining spelling, order and provenance."""
    for field in ("keywords", "subjects"):
        if field not in facts:
            continue
        values = facts[field]
        if not isinstance(values, list) or not 1 <= len(values) <= MAX_TERMS:
            raise AssetError(f"{field} must contain 1–{MAX_TERMS} entries when supplied")
        for value in values:
            if field == "keywords":
                if not plain_string(value) or len(value.encode("utf-8")) > MAX_TERM_BYTES:
                    raise AssetError(f"each keyword must be a nonempty literal line of at most {MAX_TERM_BYTES} UTF-8 bytes")
                continue
            if not isinstance(value, dict) or "subject" not in value or set(value) - SUBJECT_FIELDS:
                raise AssetError("each subjects entry requires subject and only optional subject_scheme, scheme_uri, value_uri, classification_code, language")
            for name, text in value.items():
                limit = MAX_SUBJECT_URI_BYTES if name in {"scheme_uri", "value_uri"} else MAX_TERM_BYTES
                if not plain_string(text) or len(text.encode("utf-8")) > limit:
                    raise AssetError(f"subjects.{name} must be a nonempty literal line of at most {limit} UTF-8 bytes")
                if name in {"scheme_uri", "value_uri"} and not absolute_subject_uri(text):
                    raise AssetError(f"subjects.{name} must be an absolute URI with valid escaping")
                if name == "language" and not SUBJECT_LANGUAGE.fullmatch(text):
                    raise AssetError("subjects.language must follow XML language-tag syntax")
