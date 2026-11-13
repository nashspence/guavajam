"""Reviewed bibliographic facts stay attached to their edition and resource."""
import copy
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import yaml

from guavajam_core.asset_policy import AssetError
from guavajam_core.bibliography import MAX_ABSTRACT_BYTES, MAX_AUTHORS
from test_validate_sources import MANUSCRIPT, METADATA, StrictSourceTests

AUTHORS = [{"name": 'Zoë <Z> & "A"', "orcid": "0000-0002-1825-0097"},
           {"name": "李 *literal*"}, {"name": "Ada", "orcid": "0000-0001-5109-3700"}]
ABSTRACT = 'A literal *claim*: x < y & "z". <script>alert(1)</script> is text.'


class Indented(yaml.SafeDumper):
    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def frontmatter(facts):
    return "---\n" + yaml.dump(facts, Dumper=Indented, allow_unicode=True, sort_keys=False, width=10000) + "---\n\n"




class SourceBibliographyTests(StrictSourceTests):
    def facts(self, **fields):
        facts = dict(METADATA)
        del facts["author"]
        return dict(facts, authors=copy.deepcopy(AUTHORS), **fields)

    def check_facts(self, facts):
        return self.check(front_matter=frontmatter(facts))

    def test_ordered_literal_names_and_optional_fields_are_accepted(self):
        for kind in ("CreativeWork", "ScholarlyArticle"):
            with self.subTest(kind=kind):
                self.assertEqual([], self.check_facts(self.facts(abstract=ABSTRACT, schema_type=kind)))

    def test_ambiguous_or_missing_authorship_is_rejected(self):
        for extra in ({"author": "Other"}, {"author_orcid": AUTHORS[0]["orcid"]}):
            facts = self.facts(); facts.update(extra)
            self.assertTrue(any("one authorship representation" in e for e in self.check_facts(facts)))
        self.assert_bad("schema keys mismatch", metadata={k:v for k,v in METADATA.items() if k != "author"})

    def test_malformed_author_lists_names_identifiers_and_extra_fields_fail(self):
        for authors in ([], "Alice", ["Alice"], [{"orcid": AUTHORS[0]["orcid"]}],
                        [{"name": ""}], [{"name": " Alice"}], [{"name": "a" * 513}],
                        [{"name": "Alice", "role": "editor"}],
                        [{"name": "Alice", "orcid": "0000-0002-1825-0098"}],
                        [{"name": "Alice", "orcid": [AUTHORS[0]["orcid"]]}],
                        [AUTHORS[0], dict(AUTHORS[0], name="Someone else")],
                        [{"name": "Alice"}] * (MAX_AUTHORS + 1)):
            facts = self.facts(); facts["authors"] = authors
            with self.subTest(authors=authors):
                self.assertTrue(self.check_facts(facts))

    def test_author_yaml_boundary_rejects_flow_aliases_duplicates_and_arbitrary_nesting(self):
        valid = frontmatter(self.facts())
        cases = [valid.replace('authors:\n', 'authors: &people\n'),
                 valid.replace('authors:\n', 'authors: [Alice, Bob]\n'),
                 valid.replace('    orcid: ', '    name: Duplicate\n    orcid: ', 1),
                 valid.replace('  - name:', ' - name:', 1),
                 valid.replace('    orcid: ', '    affiliation:\n      name: Somewhere\n    orcid: ', 1)]
        for source in cases:
            with self.subTest(source=source):
                self.assertTrue(self.check(front_matter=source))


    def test_optional_values_have_narrow_types_and_explicit_bounds(self):
        for field, values in (("abstract", ("", " ", "two\nlines", "x" * (MAX_ABSTRACT_BYTES + 1),
                                             "é" * (MAX_ABSTRACT_BYTES // 2 + 1), ["text"], "text\tend")),
                              ("schema_type", ("Article", "Person", "scholarlyarticle", "", ["CreativeWork"]))):
            for value in values:
                facts = self.facts(); facts[field] = value
                with self.subTest(field=field, value=value):
                    self.assertTrue(self.check_facts(facts))
        self.assertEqual([], self.check_facts(self.facts(abstract="é" * (MAX_ABSTRACT_BYTES // 2))))











def load_tests(loader, tests, pattern):
    return unittest.TestSuite(loader.loadTestsFromName(name,cls) for cls in (SourceBibliographyTests,) for name in cls.__dict__ if name.startswith("test_"))
