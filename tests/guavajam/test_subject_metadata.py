"""Subject declarations stay literal, optional and independent of any taxonomy."""
import copy
from pathlib import Path
import unittest

import yaml

from guavajam.asset_policy import AssetError
from guavajam.subject_metadata import (
    MAX_SUBJECT_URI_BYTES, MAX_TERM_BYTES, MAX_TERMS, check_subject_metadata,
)
from guavajam.validate_sources import (
    parse_pandoc, validate_front_matter, validate_pandoc_front_matter,
)
from test_validate_sources import MANUSCRIPT, METADATA, StrictSourceTests


class Indented(yaml.SafeDumper):
    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def frontmatter(facts):
    return "---\n" + yaml.dump(facts, Dumper=Indented, allow_unicode=True,
                              sort_keys=False, width=10000) + "---\n\n"


# DataCite 4.7 Subject semantics with local snake_case spellings. The examples
# deliberately include a local scheme and a URN: GuavaJam does not prescribe a
# vocabulary, check membership, dereference identifiers or assert equivalence.
# https://datacite-metadata-schema.readthedocs.io/en/4.7/properties/subject/
KEYWORDS = ["coastal ecology", "C++ & C#", "李 *literal* <term>", "Climate change"]
SUBJECTS = [
    {"subject": "Climate change mitigation",
     "subject_scheme": "Library of Congress Subject Headings",
     "scheme_uri": "https://id.loc.gov/authorities/subjects.html",
     "value_uri": "https://id.loc.gov/authorities/subjects/sh2009009655.html",
     "language": "en"},
    {"subject": "Local *literal* classification", "subject_scheme": "My local scheme",
     "scheme_uri": "urn:example:local-scheme", "classification_code": "001.20-A",
     "language": "zh-Hant-TW"},
    {"subject": "A free-text topic"},
    {"subject": "An independently identified topic", "value_uri": "urn:example:topic"},
]


class SourceSubjectTests(StrictSourceTests):
    def metadata_prefix(self, **fields):
        return frontmatter({**METADATA, **fields})

    def test_keyword_and_subject_declarations_roundtrip_as_literal_source_facts(self):
        fields = {"keywords": KEYWORDS, "subjects": SUBJECTS}
        front = self.metadata_prefix(**fields)
        self.assertEqual([], self.check(front_matter=front))
        facts, body, _, errors = validate_front_matter(self.source_path, front + MANUSCRIPT)
        self.assertEqual([], errors)
        self.assertEqual(fields, {key: facts[key] for key in fields})
        self.assertEqual("\n" + MANUSCRIPT, body)

    @property
    def source_path(self):
        return Path("content/manuscript.md")

    def test_each_collection_can_be_omitted_or_used_independently(self):
        for fields in ({}, {"keywords": ["Local topics"]}, {"subjects": [{"subject": "Local topics"}]}):
            with self.subTest(fields=fields):
                self.assertEqual([], self.check(front_matter=self.metadata_prefix(**fields)))

    def test_values_are_not_slugged_or_merged_by_label(self):
        subjects = [{"subject": "Java", "subject_scheme": "Programming languages"},
                    {"subject": "Java", "subject_scheme": "Geographic names"},
                    {"subject": "Java", "language": "en"},
                    {"subject": "Java", "language": "id"}]
        front = self.metadata_prefix(keywords=["Case Sensitive", "case sensitive"], subjects=subjects)
        self.assertEqual([], self.check(front_matter=front))
        facts, _, _, _ = validate_front_matter(self.source_path, front + MANUSCRIPT)
        self.assertEqual(subjects, facts["subjects"])

    def test_subject_fields_are_strings_with_explicit_operational_bounds(self):
        bad = [
            {"keywords": []}, {"keywords": "topic"}, {"keywords": [""]},
            {"keywords": [" leading"]}, {"keywords": ["two\nlines"]},
            {"keywords": ["e\u0301"]}, {"keywords": ["tab\there"]},
            {"keywords": ["x" * (MAX_TERM_BYTES + 1)]},
            {"keywords": ["é" * (MAX_TERM_BYTES // 2 + 1)]},
            {"keywords": ["topic"] * (MAX_TERMS + 1)},
            {"subjects": []}, {"subjects": "topic"}, {"subjects": ["topic"]},
            {"subjects": [{}]}, {"subjects": [{"subject_scheme": "Scheme"}]},
            {"subjects": [{"subject": "Topic", "category": "Research"}]},
            {"subjects": [{"subject": "Topic", "classification_code": 12}]},
            {"subjects": [{"subject": "Topic", "classification_code": ""}]},
            {"subjects": [{"subject": "Topic", "language": "en_US"}]},
            {"subjects": [{"subject": "Topic", "language": "123"}]},
            {"subjects": [{"subject": "Topic"}] * (MAX_TERMS + 1)},
            {"subjects": [{"subject": "x" * (MAX_TERM_BYTES + 1)}]},
            {"subjects": [{"subject": "Topic", "value_uri": "urn:" + "x" * MAX_SUBJECT_URI_BYTES}]},
        ]
        for fields in bad:
            with self.subTest(fields=fields), self.assertRaises(AssetError):
                check_subject_metadata(fields)
        for fields in ({"keywords": ["é" * (MAX_TERM_BYTES // 2)]},
                       {"keywords": ["topic"] * MAX_TERMS},
                       {"subjects": [{"subject": "Topic"}] * MAX_TERMS},
                       {"subjects": [{"subject": "Topic", "value_uri": "urn:" + "x" * (MAX_SUBJECT_URI_BYTES - 4)}]}):
            with self.subTest(fields=fields):
                check_subject_metadata(fields)

    def test_subject_uri_identifiers_require_absolute_syntax_not_an_authority_allowlist(self):
        for uri in ("urn:example:topic", "https://example.invalid/scheme/term%20one#id", "doi:10.1234/example",
                    "https://[2001:db8::1]:443/term?label=x%20y#id", "https://example.org:8443/term",
                    "https://[v1.example]/term", "custom://user:secret@example.org/topic",
                    "custom:topic/path?query=value#term"):
            with self.subTest(uri=uri):
                self.assertEqual([], self.check(front_matter=self.metadata_prefix(subjects=[{"subject": "Topic", "value_uri": uri}])))
        for uri in ("relative/term", "//example.org/term", "https:", "https:/term", "https://[broken/term",
                    "https://example.org/has space", "urn:example:bad%2", "urn:example:bad%GG",
                    "urn:example:back\\slash", "https://example.org:bad/term", "https://[::1]extra/term",
                    "https://example.org/term[one]", "urn:example:[term]", "https://example.org/term#one#two",
                    "https://user@other@example.org/term", "https://example.org/query?value=[one]"):
            with self.subTest(uri=uri), self.assertRaisesRegex(AssetError, "absolute URI"):
                check_subject_metadata({"subjects": [{"subject": "Topic", "scheme_uri": uri}]})

    def test_new_fields_do_not_permit_aliases_flow_collections_or_arbitrary_nesting(self):
        cases = [
            "keywords: [one, two]\n",
            "keywords:\n  - &term one\n  - *term\n",
            "keywords:\n  - !!str one\n",
            "keywords:\n  - topic:\n      nested: value\n",
            "subjects:\n  - {subject: one}\n",
            "subjects:\n  - subject: one\n    subject: two\n",
            "subjects:\n  - subject: one\n    scheme_uri:\n      value: https://example.org\n",
            "subjects:\n  - subject: one\n    language: en\n    language: de\n",
            "subjects:\n  - subject: one\n    subject_scheme: !custom scheme\n",
            "subjects:\n  - subject: one\n    value_uri: >\n      https://example.org\n",
            "subjects:\n    - subject: wrong indentation\n",
            "keywords:\n - wrong indentation\n",
        ]
        for suffix in cases:
            front = self.metadata_prefix().removesuffix("---\n\n") + suffix + "---\n\n"
            with self.subTest(suffix=suffix):
                self.assertTrue(self.check(front_matter=front))

    def test_ambiguous_keyword_and_subject_scalars_require_quotes(self):
        for suffix in ("keywords:\n  - yes\n", "keywords:\n  - 12\n",
                       "subjects:\n  - subject: Topic\n    classification_code: 0012\n",
                       "subjects:\n  - subject: Topic\n    language: no\n"):
            front = self.metadata_prefix().removesuffix("---\n\n") + suffix + "---\n\n"
            with self.subTest(suffix=suffix):
                self.assertIn("quote ambiguous", " ".join(self.check(front_matter=front)))
        self.assertEqual([], self.check(front_matter=self.metadata_prefix(
            keywords=["yes", "12"], subjects=[{"subject": "Topic", "classification_code": "0012", "language": "no"}])))

    def test_pandoc_collection_shape_must_match_yaml(self):
        source = self.metadata_prefix(keywords=KEYWORDS, subjects=SUBJECTS) + MANUSCRIPT
        facts, _, _, _ = validate_front_matter(self.source_path, source)
        ast = parse_pandoc(source)
        self.assertEqual([], validate_pandoc_front_matter(self.source_path, facts, ast))
        broken = []
        value = copy.deepcopy(ast)
        value["meta"]["keywords"]["c"].pop()
        broken.append(value)
        value = copy.deepcopy(ast)
        value["meta"]["subjects"] = {"t": "MetaString", "c": "not a list"}
        broken.append(value)
        value = copy.deepcopy(ast)
        del value["meta"]["subjects"]["c"][0]["c"]["value_uri"]
        broken.append(value)
        value = copy.deepcopy(ast)
        value["meta"]["subjects"]["c"][0]["c"]["subject"] = {"t": "MetaBool", "c": True}
        broken.append(value)
        for value in broken:
            with self.subTest(value=value["meta"]):
                self.assertTrue(validate_pandoc_front_matter(self.source_path, facts, value))


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(loader.loadTestsFromName(name, SourceSubjectTests)
                              for name in SourceSubjectTests.__dict__ if name.startswith("test_"))
