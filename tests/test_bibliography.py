"""Reviewed bibliographic facts stay attached to their edition and resource."""
import copy
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from asset_policy import AssetError
from bibliography import MAX_ABSTRACT_BYTES, MAX_AUTHORS
from preview_site import assemble_preview
from public_record import check_records, record_html, work_html, write_records
from test_assets import EditionTests
from test_public_record import PublicRecordTests
from test_editorial_history import EditorialHistoryTests
from test_render_contract import RenderContractTests
from test_validate_sources import MANUSCRIPT, METADATA, StrictSourceTests
from verify_edition import EditionHTML, verify_edition

AUTHORS = [{"name": 'Zoë <Z> & "A"', "orcid": "0000-0002-1825-0097"},
           {"name": "李 *literal*"}, {"name": "Ada", "orcid": "0000-0001-5109-3700"}]
ABSTRACT = 'A literal *claim*: x < y & "z". <script>alert(1)</script> is text.'


class Indented(yaml.SafeDumper):
    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def frontmatter(facts):
    return "---\n" + yaml.dump(facts, Dumper=Indented, allow_unicode=True, sort_keys=False, width=10000) + "---\n\n"


class Metadata(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.meta = {}
        self.scopes = []
        self.properties = []
        self.scripts = 0
        self.feed(page.decode() if isinstance(page, bytes) else page)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and "name" in attrs:
            self.meta.setdefault(attrs["name"], []).append(attrs.get("content"))
        if "itemscope" in attrs:
            self.scopes.append(attrs.get("itemtype"))
        if "itemprop" in attrs:
            self.properties.append((tag, attrs))
        if tag == "script":
            self.scripts += 1


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

    def test_work_citation_selector_requires_a_same_work_publication_receipt(self):
        work = {"schema": "press.work-registration.v1", "work": "sample", "identifiers": {},
                "citation_edition": "v1.0.0"}
        edition = self.edition()
        def check(work, edition, path="registrations/editions/sample/v1.0.0.json"):
            return self.check(other={"registrations/works/sample.json": self.canonical_json(work),
                                     path: self.canonical_json(edition)})
        self.assertEqual([], check(work, edition))
        for version in (None, [], "v99.0.0", "works/other/v1.0.0", "v01.0.0"):
            with self.subTest(version=version):
                self.assertTrue(any("citation_edition" in e for e in check(dict(work, citation_edition=version), edition)))
        pending = dict(edition); pending.pop("published_at")
        self.assertTrue(any("receipted edition" in e for e in check(work, pending)))
        self.assertTrue(any("receipted edition" in e for e in check(work, edition,
            "registrations/editions/other/v1.0.0.json")))

    def test_optional_values_have_narrow_types_and_explicit_bounds(self):
        for field, values in (("abstract", ("", " ", "two\nlines", "x" * (MAX_ABSTRACT_BYTES + 1),
                                             "é" * (MAX_ABSTRACT_BYTES // 2 + 1), ["text"], "text\tend")),
                              ("schema_type", ("Article", "Person", "scholarlyarticle", "", ["CreativeWork"]))):
            for value in values:
                facts = self.facts(); facts[field] = value
                with self.subTest(field=field, value=value):
                    self.assertTrue(self.check_facts(facts))
        self.assertEqual([], self.check_facts(self.facts(abstract="é" * (MAX_ABSTRACT_BYTES // 2))))


class EditionBibliographyTests(RenderContractTests):
    def setUp(self):
        EditionTests.setUp(self)
        self.sample_with_structures()

    def reviewed_facts(self):
        path = self.repo / "works/sample-work/manuscript.md"
        _, header, body = path.read_text().split("---\n", 2)
        facts = yaml.safe_load(header)
        del facts["author"]
        facts.update(authors=copy.deepcopy(AUTHORS), abstract=ABSTRACT, schema_type="ScholarlyArticle")
        path.write_text(frontmatter(facts) + body.removeprefix("\n"))
        self.git("commit", "-qam", "Review ordered authors and optional bibliography")
        self.commit = self.git("rev-parse", "HEAD")
        return facts

    def test_copied_starter_builds_reviewed_ordered_authorship_and_literal_abstract(self):
        self.reviewed_facts()
        # The preview and edition must use the committed facts, including literal markup.
        (self.repo / "works/sample-work/manuscript.md").write_text("Unreviewed replacement")
        manifest = self.build()
        self.assertEqual(AUTHORS, manifest["authors"])
        self.assertNotIn("author", manifest)
        self.assertNotIn("author_orcid", manifest)
        self.assertEqual((ABSTRACT, "ScholarlyArticle"), (manifest["abstract"], manifest["schema_type"]))
        parser = EditionHTML(manifest["presentation_profile"]); parser.feed(self.page().read_text())
        self.assertEqual([a["name"] for a in AUTHORS], parser.authors)
        self.assertEqual('; '.join(a['name'] for a in AUTHORS), ' '.join(''.join(parser.byline).split()))
        self.assertEqual('Edition v1.0.0', ''.join(parser.identity_version))
        self.assertEqual([ABSTRACT], parser.descriptions)
        self.assertIn("Zoë <Z>", "".join(parser.authorship))
        self.assertIn("李 *literal*", " ".join("".join(parser.authorship).split()))
        self.assertEqual(["https://orcid.org/" + a["orcid"] for a in AUTHORS if "orcid" in a], parser.orcid_links)
        self.assertEqual(0, Metadata(self.page().read_bytes()).scripts)
        self.assertNotIn("DCTERMS.issued", self.page().read_text())
        self.assertNotIn("datePublished", self.page().read_text())
        self.assertIn("Author 1 ORCID: 0000-0002-1825-0097", manifest["initial_release_body"])
        self.assertIn("Author 2: 李 \\*literal\\*", manifest["initial_release_body"])
        self.assertEqual(manifest, verify_edition(self.output))
        metadata = assemble_preview(self.repo, self.commit, self.tag, self.base / "preview",
                                   fetcher=lambda *_: (self.preview_asset, "image/svg+xml"))
        record = (self.base / "preview" / metadata["routes"]["/" + self.tag]["file"]).read_bytes()
        self.assertEqual([ABSTRACT], Metadata(record).meta["DC.description"])
        self.assertIn(b"Local preview", record)
        self.assertEqual(self.page().read_bytes(), (self.base / "preview/package" / self.page().name).read_bytes())

    def test_omitted_optional_fields_do_not_appear_in_single_author_starter(self):
        manifest = self.build()
        for field in ("authors", "abstract", "schema_type"):
            self.assertNotIn(field, manifest)
        self.assertEqual("Nash Spence", manifest["author"])
        self.assertNotIn("description", Metadata(self.page().read_bytes()).meta)
        parser = EditionHTML(manifest['presentation_profile']); parser.feed(self.page().read_text())
        self.assertEqual('Nash Spence', ''.join(parser.byline))
        self.assertEqual('Edition v1.0.0', ''.join(parser.identity_version))

    def test_title_identity_is_provenance_bound_and_stays_immediately_after_the_h1(self):
        import re
        self.reviewed_facts(); self.build(); raw = self.page().read_bytes()
        identity = re.search(rb'<div class="press-edition-identity"[^>]*>.*?</div>', raw, re.S).group()
        self.assertRegex(raw, rb'</h1>\s*<div class="press-edition-identity"')
        self.assertLess(raw.index(identity), raw.index(b'<nav id="press-contents"'))
        self.assertEqual(1, raw.count(b'<h1'))
        changed = (
            raw.replace(identity, identity.replace(b'Zo\xc3\xab', b'Someone else')),
            raw.replace(identity, identity.replace(b'Edition v1.0.0', b'Edition v9.0.0')),
            raw.replace(identity, b''),
            raw.replace(identity, identity + identity),
            raw.replace(identity, b'').replace(b'</article>', identity + b'</article>'),
            raw.replace(identity, identity.replace(b'<div ', b'<div hidden ', 1)),
        )
        for page in changed:
            with self.subTest(change=changed.index(page)):
                self._repack(html=page)
                with self.assertRaisesRegex(AssetError, 'identity'):
                    verify_edition(self.output)

    def test_offline_verifier_rejects_changed_author_order_visible_pairing_and_abstract(self):
        self.reviewed_facts(); manifest = self.build(); raw = self.page().read_bytes()
        for html in (raw.replace(b'content="Ada"', b'content="Other"', 1),
                     raw.replace(b'Authors: Zo', b'Authors: No', 1),
                     raw.replace(b'Cite this edition as Zo', b'Cite this edition as No', 1),
                     raw.replace(b'https://orcid.org/0000-0002-1825-0097', b'https://orcid.org/0000-0001-5109-3700', 1),
                     raw.replace(b'A literal *claim*', b'A changed *claim*', 1)):
            with self.subTest(html=html[:20]):
                self._repack(html=html, manifest=copy.deepcopy(manifest))
                with self.assertRaisesRegex(AssetError, "attribution|associations|abstract"):
                    verify_edition(self.output)
        for changed in (dict(manifest, author="Ambiguous"), dict(manifest, schema_type="Person"),
                        dict(manifest, abstract=""), dict(manifest, authors=list(reversed(AUTHORS)))):
            self._repack(html=raw, manifest=changed)
            with self.assertRaises(AssetError):
                verify_edition(self.output)

    def test_shared_bibliography_rules_are_bound_to_committed_builder(self):
        library = Path(__file__).resolve().parents[1] / "scripts/bibliography.py"
        original = Path.read_bytes
        def changed(path):
            raw = original(path)
            return raw + b"\n# uncommitted rule" if path == library else raw
        with patch.object(Path, "read_bytes", changed):
            with self.assertRaisesRegex(AssetError, "builder files must match.*bibliography.py"):
                self.build()


class RecordBibliographyTests(PublicRecordTests):
    def test_edition_metadata_uses_receipt_and_only_assigned_edition_doi(self):
        page = record_html(self.manifest, published_at=self.timestamp, identifiers=None, notices=[])
        values = Metadata(page).meta
        self.assertEqual([self.manifest["title"]], values["DC.title"])
        self.assertEqual(["Nash Spence"], values["DC.creator"])
        self.assertEqual(["Press"], values["DC.publisher"])
        self.assertEqual([self.timestamp], values["DCTERMS.issued"])
        self.assertEqual([self.url], values["DC.identifier"])
        self.assertEqual(["en"], values["DC.language"])
        self.assertNotIn("DC.description", values)
        self.assertFalse(any(k.startswith("citation_") for k in values))
        self.assertEqual([], Metadata(page).scopes)
        self.assertIn(b'http://purl.org/dc/terms/', page)
        page = record_html(self.manifest, published_at=self.timestamp,
                           identifiers={"version_doi": "10.1234/edition"}, notices=[])
        self.assertEqual([self.url, "https://doi.org/10.1234/edition"], Metadata(page).meta["DC.identifier"])

    def test_opt_in_schema_and_abstract_agree_with_visible_ordered_attribution(self):
        manifest = dict(self.manifest, authors=AUTHORS, abstract=ABSTRACT)
        del manifest["author"]
        for kind in ("CreativeWork", "ScholarlyArticle"):
            manifest["schema_type"] = kind
            page = record_html(manifest, published_at=self.timestamp, identifiers=None, notices=[])
            parsed = Metadata(page)
            self.assertEqual([a["name"] for a in AUTHORS], parsed.meta["DC.creator"])
            self.assertEqual([ABSTRACT], parsed.meta["DC.description"])
            self.assertEqual(1, parsed.scopes.count("https://schema.org/" + kind))
            self.assertEqual(3, parsed.scopes.count("https://schema.org/Person"))
            self.assertEqual(1, parsed.scopes.count("https://schema.org/Organization"))
            self.assertEqual(0, parsed.scripts)
            self.assertIn(b'<h2>Abstract</h2>', page)
            properties = [attrs for _, attrs in parsed.properties]
            self.assertIn({"itemprop": "description", "content": ABSTRACT}, properties)
            self.assertIn({"itemprop": "datePublished", "datetime": self.timestamp}, properties)
            self.assertNotIn(b'<script>', page)
            self.assertIn(b'Zo\xc3\xab &lt;Z&gt; &amp; &quot;A&quot;', page)
            self.assertIn(b'Author 1 ORCID</dt><dd>0000-0002-1825-0097', page)
            self.assertIn(b'Author 3 ORCID</dt><dd>0000-0001-5109-3700', page)

    def landing(self, receipts, manifests, notices=None, work=None):
        notices = notices or {v: [] for v in receipts}
        return work_html("https://press.example.org", "essay", receipts, manifests, notices,
                         work or {"identifiers": {"concept_doi": "10.1234/essay"}})

    def test_work_record_requires_reviewed_attribution_and_language(self):
        for field in ('author', 'language'):
            with self.subTest(field=field):
                manifest = dict(self.manifest)
                del manifest[field]
                with self.assertRaises(AssetError):
                    self.landing({'v1.0.0': self.receipt}, {'v1.0.0': manifest})

    def test_initial_work_citation_does_not_change_implicitly_with_editions_or_doi_changes(self):
        later = dict(self.manifest, title="Later title", author="Later author", tag="works/essay/v2.0.0")
        first = dict(self.manifest, authors=AUTHORS, title="Original title", abstract=ABSTRACT, schema_type="ScholarlyArticle")
        del first["author"]
        receipts = {"v2.0.0": dict(self.receipt, record_url="https://press.example.org/works/essay/v2.0.0"),
                    "v1.0.0": dict(self.receipt, published_at="2025-01-02T00:00:00Z")}
        manifests = {"v1.0.0": first, "v2.0.0": later}
        notices = {"v1.0.0": ["- 2026-11-05 Withdrawal: Retained for citation."], "v2.0.0": []}
        history = []
        for doi in ("10.1234/essay", "10.1234/corrected", None):
            if doi != "10.1234/essay":
                history.append({"date": "2026-11-06", "from": "10.1234/essay" if doi else "10.1234/corrected", "to": doi})
            work = {"identifiers": {"concept_doi": doi} if doi else {}}
            if history:
                work["concept_doi_history"] = history
            page = self.landing(receipts, manifests, notices, work)
            values = Metadata(page).meta
            self.assertEqual(["Original title"], values["DC.title"])
            self.assertEqual([a["name"] for a in AUTHORS], values["DC.creator"])
            self.assertEqual(["2025-01-02T00:00:00Z"], values["DCTERMS.issued"])
            self.assertEqual(["https://doi.org/" + doi if doi else "https://press.example.org/works/essay"], values["DC.identifier"])
            self.assertNotIn("DC.description", values)
            self.assertEqual([], Metadata(page).scopes)  # An edition's opt-in does not classify the work.
            citation = page.decode().split("<h2>Cite this work</h2>")[1].split("</p>")[0]
            self.assertIn("Original title", citation)
            self.assertIn("Press, 2025", citation)
            self.assertNotIn("Later author", citation)
            self.assertNotIn("v1.0.0", citation)
            self.assertIn(b"Withdrawn", page)
            self.assertIn(b"Later title", page)

    def test_explicit_reviewed_work_basis_changes_title_and_authors_without_rewriting_editions(self):
        self.generate()
        original_edition = self.page.read_bytes()
        self.issue_version("v2.0.0", title="Changed work identity", author="New author")
        second_page = self.root / "records/editions/essay/v2.0.0/index.html"
        second = second_page.read_bytes()
        landing = self.root / "records/works/essay/index.html"
        self.assertEqual([self.manifest["title"]], Metadata(landing.read_bytes()).meta["DC.title"])
        self.save_work({"concept_doi": "10.1234/essay"}, citation_edition="v2.0.0")
        write_records(self.root, self.tag)
        values = Metadata(landing.read_bytes()).meta
        self.assertEqual(["Changed work identity"], values["DC.title"])
        self.assertEqual(["New author"], values["DC.creator"])
        self.assertEqual([self.timestamp], values["DCTERMS.issued"])
        self.assertEqual(["https://doi.org/10.1234/essay"], values["DC.identifier"])
        citation = landing.read_text().split("<h2>Cite this work</h2>")[1].split("</p>")[0]
        self.assertIn("New author. <cite>Changed work identity</cite>", citation)
        self.assertEqual(original_edition, self.page.read_bytes())
        self.assertEqual(second, second_page.read_bytes())
        self.assertEqual([self.manifest["title"]], Metadata(original_edition).meta["DC.title"])
        self.assertEqual(["Changed work identity"], Metadata(second).meta["DC.title"])
        self.git("add", "records"); check_records(self.root)

    def test_selected_work_basis_keeps_original_publication_date_and_allows_no_doi(self):
        later = dict(self.manifest, title="Reviewed work title", author="Reviewed author")
        receipts = {"v1.0.0": self.receipt, "v2.0.0": dict(self.receipt,
            record_url="https://press.example.org/works/essay/v2.0.0", published_at="2027-01-01T00:00:00Z")}
        first = dict(self.manifest); first.pop("work_identifiers")
        later.pop("work_identifiers")
        manifests = {"v1.0.0": first, "v2.0.0": later}
        for doi in (None, "10.1234/assigned"):
            work = {"citation_edition": "v2.0.0", "identifiers": {"concept_doi": doi} if doi else {}}
            page = self.landing(receipts, manifests, work=work)
            values = Metadata(page).meta
            self.assertEqual(["Reviewed work title"], values["DC.title"])
            self.assertEqual([self.timestamp], values["DCTERMS.issued"])
            self.assertEqual(["https://doi.org/" + doi if doi else "https://press.example.org/works/essay"], values["DC.identifier"])
            citation = page.decode().split("<h2>Cite this work</h2>")[1].split("</p>")[0]
            self.assertIn("Press, 2026", citation)
            self.assertNotIn("2027", citation)

    def test_chronology_beats_version_and_equal_timestamps_use_numeric_version(self):
        receipts = {v: dict(self.receipt, record_url="https://press.example.org/works/essay/" + v,
                            published_at=timestamp) for v, timestamp in
                    (("v1.0.0", self.timestamp), ("v2.0.0", "2024-01-02T00:00:00Z"),
                     ("v10.0.0", "2024-01-02T00:00:00Z"))}
        manifests = {v: dict(self.manifest, title=v) for v in receipts}
        self.assertEqual(["v2.0.0"], Metadata(self.landing(receipts, manifests)).meta["DC.title"])
        receipts["v10.0.0"]["published_at"] = "2023-01-02T00:00:00Z"
        self.assertEqual(["v10.0.0"], Metadata(self.landing(receipts, manifests)).meta["DC.title"])

    def test_pending_claim_does_not_supply_work_bibliographic_basis(self):
        self.generate()
        pending = dict(self.receipt, version="v0.1.0", record_url="https://press.example.org/works/essay/v0.1.0")
        del pending["published_at"]
        path = self.registration.with_name("v0.1.0.json")
        path.write_text(json.dumps(pending, sort_keys=True, indent=2) + "\n"); self.git("add", str(path))
        write_records(self.root, self.tag)
        page = (self.root / "records/works/essay/index.html").read_bytes()
        self.assertEqual([self.timestamp], Metadata(page).meta["DCTERMS.issued"])
        self.assertEqual([self.manifest["title"]], Metadata(page).meta["DC.title"])



class WorkCitationHistoryTests(EditorialHistoryTests):
    def test_existing_history_gate_allows_valid_explicit_work_citation_review(self):
        self.issue()
        base = self.baseline()
        path = self.root / "registrations/works/essay.json"
        path.parent.mkdir()
        work = {"schema": "press.work-registration.v1", "work": "essay", "identifiers": {},
                "citation_edition": "v1.0.0"}
        path.write_text(json.dumps(work, sort_keys=True, indent=2) + "\n")
        self.check(base)
        base = self.baseline()
        work["citation_edition"] = "v9.0.0"
        path.write_text(json.dumps(work, sort_keys=True, indent=2) + "\n")
        with self.assertRaisesRegex(AssetError, "citation_edition.*receipted edition"):
            self.check(base)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(case(name) for case in (SourceBibliographyTests, EditionBibliographyTests,
                                                     RecordBibliographyTests, WorkCitationHistoryTests)
                              for name in case.__dict__ if name.startswith("test_"))
