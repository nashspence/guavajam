"""Positive/negative executable tests for Press source format v1."""

import yaml
import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from validate_sources import parse_document, validate_repository, walk  # noqa: E402


MANUSCRIPT = ("# On Structure and Identity\n\n"
              "<a id=\"sec-1\"></a>\n## § 1 — Foundations\n\n"
              "Let $S$ denote symbols, and consider $x \\in S$.\n\n"
              "$$\n\\forall x \\in S, \\quad x=x\n$$\n\n"
              "See [the section](#sec-1).\n")
METADATA = {
    "author": "nashspence", "language": "en", "rights": "all-rights-reserved",
    "schema": "press.work.v1", "slug": "sample", "status": "draft",
    "title": "On Structure and Identity"
}


class StrictSourceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = Path("works/sample/manuscript.md")
        (self.root / self.source.parent).mkdir(parents=True)

    def check(self, source=MANUSCRIPT, metadata=METADATA, other=None, front_matter=None):
        if front_matter is not None:
            prefix = front_matter
        elif metadata is None:
            prefix = ""
        else:
            prefix = "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + "---\n\n"
        (self.root / self.source).write_bytes((prefix + source).encode("utf-8"))
        paths = [self.source]
        for name, content in (other or {}).items():
            path = Path(name)
            dest = self.root / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
            paths.append(path)
        return validate_repository(self.root, paths)

    def assert_bad(self, needle, source=MANUSCRIPT, metadata=METADATA, other=None):
        errors = self.check(source, metadata, other)
        self.assertTrue(any(needle in error for error in errors), "expected " + needle + "; got " + str(errors))

    def test_canonical_example(self):
        self.assertEqual([], self.check())

    def test_starter_manuscript(self):
        repository = Path(__file__).resolve().parents[1]
        source = Path("works/sample-work/manuscript.md")
        (self.root / source.parent).mkdir(parents=True)
        (self.root / source).write_bytes((repository / "templates/work/manuscript.md").read_bytes())
        (self.root / "asset-policy.json").write_bytes((repository / "asset-policy.json").read_bytes())
        import shutil
        shutil.copytree(repository/'templates/work/supporting',self.root/source.parent/'supporting')
        supporting = [p.relative_to(self.root) for p in (self.root/source.parent/'supporting').iterdir()]
        self.assertEqual([], validate_repository(self.root, [source,*supporting]))

        ast, errors = parse_document(self.root / source, source)
        self.assertEqual([], errors)
        nodes = list(walk(ast["blocks"]))
        kinds = {node["t"] for node in nodes}
        self.assertTrue({"Header", "Strong", "Emph", "Strikeout", "Code", "CodeBlock",
                         "BulletList", "OrderedList", "BlockQuote", "Table", "Note",
                         "Image", "Link", "Math"} <= kinds)
        self.assertEqual([1, 2, 3, 3, 3, 4, 2, 2],
                         [node["c"][0] for node in nodes if node["t"] == "Header"])
        # Preserve literal LaTeX in the fixture so mobile rendering failures remain visible.
        self.assertIn("Let $S$ be a set.", (self.root / source).read_text(encoding="utf-8"))
        math_nodes = [node["c"] for node in nodes if node["t"] == "Math"]
        from collections import Counter
        self.assertEqual(Counter({"S": 4, "R": 2, r"x \in S": 1, "x=x": 1,
                                  r"\forall x \in S,\quad x=x": 1}),
                         Counter(content.strip() for _, content in math_nodes))
        self.assertEqual(1, sum(kind["t"] == "DisplayMath" for kind, _ in math_nodes))

        preview = (repository / "storage/preview.svg").read_bytes()
        origin = json.loads((repository / "asset-policy.json").read_text())["origin"]
        expected = f"{origin}/sha256/{hashlib.sha256(preview).hexdigest()}.svg"
        self.assertEqual([expected], [node["c"][2][0] for node in nodes if node["t"] == "Image"])



    def test_plain_document_without_yaml_renders(self):
        self.check()
        notes = Path("notes.md")
        (self.root / notes).write_text("# Notes\n\nOrdinary Markdown.\n", encoding="utf-8")
        self.assertEqual([], validate_repository(self.root, [self.source, notes]))

    def test_ordinary_markdown_explicit_anchor_links(self):
        self.check()
        notes = Path("notes.md")
        (self.root / notes).write_text('# Notes\n\n<a id="stable-section"></a>\n## Section\n\n[Go](#stable-section)\n',
                         encoding="utf-8")
        self.assertEqual([], validate_repository(self.root, [self.source, notes]))

    def test_ordinary_markdown_ignores_anchors_inside_code(self):
        self.check()
        notes = Path("notes.md")
        (self.root / notes).write_text('# Notes\n\n```html\n<a id="not-an-anchor"></a>\n```\n\n'
                         '[Broken](#not-an-anchor)\n', encoding="utf-8")
        errors = validate_repository(self.root, [self.source, notes])
        self.assertTrue(any("unresolved Markdown section link" in error for error in errors), errors)

    def test_notes_and_gfm_table(self):
        content = MANUSCRIPT + "A note[^ref].\n\n[^ref]: Context.\n\n| A | B |\n|---|---|\n| 1 | 2 |\n"
        self.assertEqual([], self.check(content))

    def test_invalid_utf8(self):
        self.check()
        (self.root / self.source).write_bytes(b"\xff# Title\n")
        self.assertIn("UTF-8", " ".join(validate_repository(self.root, [self.source])))

    def test_bom(self):
        front = "\ufeff---\n" + yaml.safe_dump(METADATA, sort_keys=False) + "---\n\n"
        self.assertTrue(any("BOM" in e for e in self.check(front_matter=front)))

    def test_nfc(self):
        self.assert_bad("NFC", MANUSCRIPT.replace("Identity", "Identite\u0301"))

    def test_bidi_override_prohibited(self):
        self.assert_bad("bidirectional override", MANUSCRIPT + "Suspicious \u202e reversal.\n")

    def test_unknown_work_source_file(self):
        self.assert_bad("unsupported file path", other={"works/sample/random.txt": "unvalidated material\n"})

    def test_crlf(self):
        self.assert_bad("CR/CRLF", MANUSCRIPT.replace("\n", "\r\n"))

    def test_final_newline(self):
        self.assert_bad("final newline", MANUSCRIPT.rstrip("\n"))

    def test_trailing_whitespace(self):
        self.assert_bad("trailing whitespace", MANUSCRIPT.replace("## § 1 — Foundations\n", "## § 1 — Foundations \n"))

    def test_no_h1(self):
        self.assert_bad("exactly one ATX H1", MANUSCRIPT.replace("# On Structure", "## On Structure"))

    def test_duplicate_h1(self):
        self.assert_bad("exactly one ATX H1", MANUSCRIPT + "\n# Another title\n")

    def test_skip_heading_level(self):
        self.assert_bad("heading level skipped", MANUSCRIPT.replace("## § 1", "#### § 1"))

    def test_missing_anchor(self):
        self.assert_bad("requires an explicit stable anchor", MANUSCRIPT.replace('<a id="sec-1"></a>\n', ""))

    def test_orphan_anchor(self):
        self.assert_bad("unattached anchor", MANUSCRIPT + '\n<a id="orphan"></a>\n')

    def test_duplicate_anchor(self):
        self.assert_bad("duplicate anchor", MANUSCRIPT.replace('<a id="sec-1"></a>', '<a id="sec-1"></a>\n<a id="sec-1"></a>'))

    def test_reject_arbitrary_html(self):
        self.assert_bad("raw HTML is prohibited", MANUSCRIPT + "<script>alert(1)</script>\n")

    def test_reject_legacy_math(self):
        self.assert_bad("not backslash math delimiters", MANUSCRIPT + r"\(x\)" + "\n")

    def test_equation_labels_require_a_heading_anchor(self):
        for command in (r"\label{eq-a}", r"\tag{A}", r"\tag*{A}", r"\% \label{eq-a}"):
            with self.subTest(command=command):
                self.assert_bad("use a stable heading anchor", MANUSCRIPT + "\n$$\nx " + command + "\n$$\n")
        self.assert_bad("use a stable heading anchor", MANUSCRIPT + r"Inline $x\tag{A}$." + "\n")

    def test_equation_command_examples_and_tex_comments(self):
        source = MANUSCRIPT + r"Use `\label{eq-a}` or `\tag*{A}` in TeX." + "\n"
        source += "\n$$\nx % " + r"\label{ignored}" + "\n+y\n$$\n"
        source += "\n$$\n" + r"\begin{matrix}x\\tag\end{matrix}" + "\n$$\n"
        self.assertEqual([], self.check(source))
        self.assert_bad("use a stable heading anchor", MANUSCRIPT + "\n$$\nx % ignored\n"
                        + r"y\label{eq-a}" + "\n$$\n")

    def test_unbalanced_inline_math(self):
        self.assert_bad("unmatched inline $", MANUSCRIPT + "An $unclosed dollar.\n")

    def test_math_not_parsed(self):
        self.assert_bad("math delimiters did not parse", MANUSCRIPT + "This is $ x $ with spaces.\n")

    def test_currency_must_be_escaped(self):
        self.assert_bad("unmatched inline $", MANUSCRIPT + "Cost is $5.\n")

    def test_escaped_currency(self):
        self.assertEqual([], self.check(MANUSCRIPT + r"Cost is \$5." + "\n"))

    def test_inline_double_dollar(self):
        self.assert_bad("display math $$ must be alone", MANUSCRIPT + "$$E=mc^2$$\n")

    def test_unclosed_display_math(self):
        self.assert_bad("unclosed display math", MANUSCRIPT + "$$\nE=mc^2\n")

    def test_unmatched_code_span(self):
        self.assert_bad("unmatched backtick", MANUSCRIPT + "An `unclosed code span.\n")

    def test_code_fence_masks_headings_and_math(self):
        self.assertEqual([], self.check(MANUSCRIPT + "```markdown\n## Hidden heading\n$$ignored$$\n```\n"))

    def test_unclosed_fence(self):
        self.assert_bad("unclosed fenced code", MANUSCRIPT + "```md\nexample\n")

    def test_footnote_ref_missing_definition(self):
        self.assert_bad("unresolved footnote", MANUSCRIPT + "A missing[^note].\n")

    def test_footnote_unused_definition(self):
        self.assert_bad("unused footnote", MANUSCRIPT + "[^unused]: Unused.\n")

    def test_footnote_duplicate_definition(self):
        self.assert_bad("duplicate footnote", MANUSCRIPT + "A note[^x].\n\n[^x]: One.\n[^x]: Two.\n")

    def test_manuscript_link_to_tracked_file_is_not_portable(self):
        self.assert_bad("standalone manuscript links", MANUSCRIPT + "[rights](../../COPYRIGHT.md)\n",
                        other={"COPYRIGHT.md": "# Rights\n"})

    def test_local_link_broken(self):
        self.assert_bad("broken local link", other={"notes.md": "[missing](missing.md)\n"})

    def test_local_link_untracked(self):
        dest = self.root / "works/sample/untracked.md"
        dest.write_text("# Local\n", encoding="utf-8")
        self.assert_bad("linked file is not tracked", other={"notes.md": "[untracked](works/sample/untracked.md)\n"})

    def test_manuscript_links_use_the_published_section_id(self):
        section = MANUSCRIPT.replace("§ 1 — Foundations", "Introduction")
        self.assert_bad("unresolved Markdown section link", section.replace("(#sec-1)", "(#introduction)"))
        self.assertEqual([], self.check(section.replace("sec-1", "introduction")))
        preview = {"notes.md": "[Introduction](works/sample/manuscript.md#introduction)\n"}
        self.assertEqual([], self.check(section, other=preview))


    def test_local_fragment_missing(self):
        self.assert_bad("unresolved Markdown section link", MANUSCRIPT + "[wrong](#missing)\n")

    def test_reject_insecure_url(self):
        self.assert_bad("unsupported/external URI", MANUSCRIPT + "[bad](javascript:alert(1))\n")

    def test_reject_image_missing_alt(self):
        self.assert_bad("image alt text required", MANUSCRIPT + "![](assets/sample.svg)\n",
                        other={"works/sample/assets/sample.svg": '<svg xmlns="http://www.w3.org/2000/svg"/>\n'})

    def test_reject_external_image(self):
        self.assert_bad("unsupported/external URI", MANUSCRIPT + "![pic](https://example.com/image.png)\n")

    def test_valid_svg_asset(self):
        source = MANUSCRIPT + "![plot](assets/figure.svg)\n"
        other = {"works/sample/assets/figure.svg": '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0L1 1"/></svg>\n'}
        self.assertEqual([], self.check(source, other=other))

    def test_local_raster_with_valid_magic_but_truncated_pixels(self):
        self.assert_bad("invalid or unsafe raster image", MANUSCRIPT + "![Chart](assets/chart.png)\n",
                        other={"works/sample/assets/chart.png": b"\x89PNG\r\n\x1a\n" + b"x" * 50})

    def test_reject_executable_svg(self):
        source = MANUSCRIPT + "![plot](assets/figure.svg)\n"
        other = {"works/sample/assets/figure.svg": '<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>\n'}
        self.assert_bad("SVG element", source, other=other)

    def test_requires_metadata(self):
        self.assert_bad("requires YAML front matter", metadata=None)

    def test_metadata_schema(self):
        self.assert_bad("schema keys mismatch", metadata={**METADATA, "extra": "unexpected"})

    def test_bibliographic_title_for_mathematical_h1(self):
        source = MANUSCRIPT.replace(METADATA["title"], r"The $\alpha$-invariant")
        self.assertEqual([], self.check(source, {**METADATA, "title": "The α-invariant"}))
        self.assert_bad("use words or Unicode", source, {**METADATA, "title": r"The \alpha-invariant"})

    def test_metadata_title_mismatch(self):
        self.assert_bad("title must exactly match", metadata={**METADATA, "title": "Incorrect"})

    def test_metadata_slug_mismatch(self):
        self.assert_bad("slug must match", metadata={**METADATA, "slug": "other"})

    def test_metadata_invalid_rights(self):
        self.assert_bad("rights must be one of", metadata={**METADATA, "rights": "MIT"})

    def test_metadata_published_status_disallowed(self):
        self.assert_bad("status must remain draft", metadata={**METADATA, "status": "published"})

    def test_does_not_validate_heading_inside_code(self):
        content = MANUSCRIPT + '````\n<a id="fake"></a>\n## Not a section\n````\n'
        self.assertEqual([], self.check(content))

    def test_yaml_duplicate_key(self):
        front = "---\n" + yaml.safe_dump(METADATA, sort_keys=False) + "title: Duplicate\n---\n\n"
        self.assertTrue(any("duplicate YAML key" in e for e in self.check(front_matter=front)))

    def test_yaml_custom_tag(self):
        front = "---\n" + yaml.safe_dump(METADATA, sort_keys=False).replace(
            "title: On Structure and Identity", "title: !my-tag On Structure and Identity") + "---\n\n"
        self.assertTrue(any("prohibited" in e for e in self.check(front_matter=front)))

    def test_yaml_alias_and_anchor(self):
        front = "---\n" + yaml.safe_dump(METADATA, sort_keys=False).replace(
            "author: nashspence", "author: &alias nashspence").replace(
            "title: On Structure and Identity", "title: *alias") + "---\n\n"
        self.assertTrue(any("prohibited" in e for e in self.check(front_matter=front)))

    def test_yaml_merge_key(self):
        front = "---\n" + yaml.safe_dump(METADATA, sort_keys=False) + "<<: *not-defined\n---\n\n"
        self.assertTrue(any("prohibited" in e or "invalid YAML" in e for e in self.check(front_matter=front)))

    def test_yaml_list_type(self):
        front = "---\n" + yaml.safe_dump(METADATA, sort_keys=False).replace(
            "author: nashspence", "author: [Nash, Spence]") + "---\n\n"
        self.assertTrue(any("string scalar" in e for e in self.check(front_matter=front)))

    def test_yaml_boolean_must_be_quoted(self):
        front = "---\n" + yaml.safe_dump(METADATA, sort_keys=False).replace(
            "author: nashspence", "author: yes") + "---\n\n"
        self.assertTrue(any("quote ambiguous" in e for e in self.check(front_matter=front)))

    def test_yaml_numbers_must_be_quoted(self):
        front = "---\n" + yaml.safe_dump(METADATA, sort_keys=False).replace(
            "author: nashspence", "author: 2026") + "---\n\n"
        self.assertTrue(any("quote ambiguous" in e for e in self.check(front_matter=front)))

    def test_yaml_unknown_key(self):
        self.assert_bad("schema keys mismatch", metadata={**METADATA, "date": "2026-10-29"})

    def test_yaml_missing_key(self):
        self.assert_bad("schema keys mismatch", metadata={k:v for k,v in METADATA.items() if k!="author"})

    def test_yaml_multiline_scalar(self):
        front = "---\n" + yaml.safe_dump(METADATA, sort_keys=False).replace(
            "title: On Structure and Identity", "title: |\n  On Structure and Identity") + "---\n\n"
        self.assertTrue(any("multiline" in e or "flat single-line" in e for e in self.check(front_matter=front)))

    def test_yaml_invalid_syntax(self):
        front = "---\n" + yaml.safe_dump(METADATA, sort_keys=False).replace(
            "author: nashspence", "author: [") + "---\n\n"
        self.assertTrue(any("invalid YAML" in e for e in self.check(front_matter=front)))

    def test_unclosed_front_matter(self):
        self.assertTrue(any("missing closing" in e for e in self.check(front_matter="---\nschema: press.work.v1\n")))

    def test_front_matter_requires_blank_separator(self):
        front = "---\n" + yaml.safe_dump(METADATA, sort_keys=False) + "---\n"
        self.assertTrue(any("blank line" in e for e in self.check(front_matter=front)))

    def test_front_matter_rejects_extra_blank_separator(self):
        front = "---\n" + yaml.safe_dump(METADATA, sort_keys=False) + "---\n\n\n"
        self.assertTrue(any("exactly one blank line" in e for e in self.check(front_matter=front)))

    def test_yaml_quotes_colon_and_comment(self):
        title = "On Structure: Identity"
        front = "---\n" + yaml.safe_dump({**METADATA, "title": title}, sort_keys=False)
        front += "# This is an author comment.\n---\n\n"
        body = MANUSCRIPT.replace("On Structure and Identity", title)
        self.assertEqual([], self.check(body, front_matter=front))

    def test_yaml_metadata_with_markdown_interpretation_disallowed(self):
        front = "---\n" + yaml.safe_dump(METADATA, sort_keys=False).replace(
            "author: nashspence", 'author: "*nashspence*"') + "---\n\n"
        self.assertTrue(any("Pandoc metadata differs" in e for e in self.check(front_matter=front)))

    def test_old_json_metadata_not_allowed(self):
        self.assert_bad("unsupported file path", other={"works/sample/metadata.json": "{}\n"})

    def test_work_directory_requires_manuscript(self):
        asset = Path("works/sample/assets/figure.svg")
        (self.root / asset.parent).mkdir()
        (self.root / asset).write_text('<svg xmlns="http://www.w3.org/2000/svg"/>\n', encoding="utf-8")
        errors = validate_repository(self.root, [asset])
        self.assertTrue(any("requires manuscript.md" in e for e in errors))

    def test_work_directories_contain_only_publication_sources(self):
        for path in ('works/README.md','works/sample/README.md','works/sample/supporting/README.md'):
            self.assert_bad('unsupported file path',other={path:'# Notes\n'})


    def test_identifiers_mapping_and_orcid(self):
        metadata = {**METADATA,
            "author_orcid": "0000-0002-1825-0097",
            "identifiers": {
                "concept_doi": "10.5281/zenodo.1234567",
                "arxiv": "2501.01234",
                "openalex": "W2741809807",
                "wikidata": "Q123456",
            }}
        self.assertEqual([], self.check(metadata=metadata))

    def test_partial_optional_identifiers(self):
        self.assertEqual([], self.check(metadata={**METADATA, "identifiers": {"openalex": "W2741809807"}}))

    def test_empty_identifiers_rejected(self):
        self.assert_bad("nonempty mapping", metadata={**METADATA, "identifiers": {}})

    def test_unknown_identifier_rejected(self):
        self.assert_bad("unknown identifier keys", metadata={**METADATA, "identifiers": {"notreal": "123"}})

    def test_identifier_bad_doi(self):
        self.assert_bad("bare DOI", metadata={**METADATA, "identifiers": {"concept_doi": "https://doi.org/10.1234/foo"}})

    def test_identifier_bad_openalex(self):
        self.assert_bad("OpenAlex W-ID", metadata={**METADATA, "identifiers": {"openalex": "A123"}})

    def test_identifier_bad_wikidata(self):
        self.assert_bad("Wikidata Q-ID", metadata={**METADATA, "identifiers": {"wikidata": "Q0"}})

    def test_identifier_bad_arxiv_version(self):
        self.assert_bad("unversioned arXiv", metadata={**METADATA, "identifiers": {"arxiv": "2501.01234v2"}})

    def test_bad_orcid_checksum(self):
        self.assert_bad("invalid ORCID", metadata={**METADATA, "author_orcid": "0000-0002-1825-0098"})

    def test_duplicate_identifier_yaml_key(self):
        prefix = "---\n" + yaml.safe_dump({**METADATA, "identifiers": {"arxiv": "2501.01234"}}, sort_keys=False)
        prefix += "  arxiv: 2501.01235\n---\n\n"
        self.assertTrue(any("duplicate YAML identifier key" in e for e in self.check(front_matter=prefix)))

    def test_nested_identifier_sequence_rejected(self):
        front = "---\n" + yaml.safe_dump(METADATA, sort_keys=False)
        front += "identifiers:\n  openalex: [W123]\n---\n\n"
        self.assertTrue(any("prohibited" in e or "string scalar" in e for e in self.check(front_matter=front)))

    def test_nested_non_identifier_map_rejected(self):
        front = "---\n" + yaml.safe_dump(METADATA, sort_keys=False)
        front += "author_orcid:\n  orcid: 0000-0002-1825-0097\n---\n\n"
        self.assertTrue(any("two-space identifiers" in e or "string scalar" in e for e in self.check(front_matter=front)))

    def test_wrong_indent_nested_identifier(self):
        front = "---\n" + yaml.safe_dump(METADATA, sort_keys=False)
        front += "identifiers:\n    openalex: W123\n---\n\n"
        self.assertTrue(any("two-space identifiers" in e for e in self.check(front_matter=front)))

    def test_v2_schema_is_rejected(self):
        self.assert_bad("schema must be press.work.v1", metadata={**METADATA, "schema": "press.work.v2"})



def load_tests(loader, tests, pattern):
    return unittest.TestSuite(loader.loadTestsFromName(name,cls) for cls in (StrictSourceTests,) for name in cls.__dict__ if name.startswith("test_"))
