"""Static in-edition navigation must reuse reviewed author headings and stay offline."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from asset_policy import AssetError
from render_profile import prepare_presentation_ast, canonical_template
from validate_sources import parse_document
import test_assets as edition_fixtures
from verify_edition import verify_edition


def heading(level, ident, label, classes=()):
    return {"t": "Header", "c": [level, [ident, list(classes), []], [{"t": "Str", "c": label}]]}


def anchor(ident):
    return {"t": "RawBlock", "c": ["html", f'<a id="{ident}"></a>']}


def sample(*, count=3, records=0, nested=True):
    blocks = [heading(1, "title", "The shape of an argument")]
    for n in range(1, count + 1):
        blocks.extend([anchor("sec-" + str(n)), heading(2, "", f"Section {n} — Reviewed heading")])
        if nested:
            blocks.extend([anchor("theorem-" + str(n)), heading(3, "", f"Theorem {n} — Detail")])
    for n in range(records):
        blocks.extend([anchor("record-" + str(n)), heading(2, "", f"Supporting record {n}", ("press-record-heading",))])
        blocks.extend([anchor("passage-" + str(n)), heading(3, "", f"Passage {n}", ("press-passage-heading",))])
    return {"meta": {}, "blocks": blocks}


class NavigationTests(unittest.TestCase):
    def _contents(self, ast):
        return [b for b in ast["blocks"] if b["t"] == "BulletList"]

    def test_major_sections_only_and_one_supporting_records_destination(self):
        ast = sample(count=3, records=12)
        prepare_presentation_ast(ast, "https://press.example.org/works/essay/v1.0.0")
        self.assertEqual(ast["meta"]["press_has_contents"], {"t": "MetaBool", "c": True})
        self.assertEqual(1, len(self._contents(ast)))
        entries = self._contents(ast)[0]["c"]
        links = [item[0]["c"][0]["c"] for item in entries]
        self.assertEqual([link[2][0] for link in links],
                         ["#sec-1", "#sec-2", "#sec-3", "#record-0"])
        self.assertTrue(all("¶" not in link[1][0]["c"] for link in links))
        self.assertEqual(links[-1][1][0]["c"], "Supporting records")
        self.assertEqual(links[-1][0][2], [["lang", "en"]])
        # Canonical section permalinks remain HTTPS, distinct from offline TOC links.
        section = next(b for b in ast["blocks"] if b.get("t") == "Header" and b["c"][1][0] == "sec-1")
        self.assertEqual(section["c"][2][-1]["c"][2][0],
                         "https://press.example.org/works/essay/v1.0.0/read#sec-1")

    def test_small_editions_have_no_contents_or_header_link(self):
        for count in (0, 1, 2):
            with self.subTest(sections=count):
                ast = sample(count=count)
                prepare_presentation_ast(ast)
                self.assertEqual([], self._contents(ast))
                self.assertNotIn("press_has_contents", ast["meta"])

    def test_two_sections_plus_evidence_merits_contents(self):
        ast = sample(count=2, records=1)
        prepare_presentation_ast(ast)
        self.assertEqual(3, len(self._contents(ast)[0]["c"]))

    def test_large_appendix_remains_a_single_entry(self):
        ast = sample(count=1, records=18)
        prepare_presentation_ast(ast)
        self.assertEqual(["#sec-1", "#record-0"], [
            item[0]["c"][0]["c"][2][0] for item in self._contents(ast)[0]["c"]])

    def test_short_appendices_do_not_force_contents(self):
        for count, records in ((0, 3), (1, 1), (1, 2)):
            with self.subTest(sections=count, records=records):
                ast = sample(count=count, records=records)
                prepare_presentation_ast(ast)
                self.assertEqual([], self._contents(ast))
                self.assertNotIn("press_has_contents", ast["meta"])

    def test_reserved_ids_rejected_even_for_short_documents(self):
        for reserved in ("press-main", "press-contents", "press-contents-label"):
            ast = {"blocks": [heading(1, "title", "Sample"), anchor(reserved), heading(2, "", "Chapter")]}
            with self.subTest(reserved=reserved), self.assertRaisesRegex(AssetError, "reserved for reader navigation"):
                prepare_presentation_ast(ast)

    def test_generated_navigation_does_not_duplicate_mathml_or_author_headings(self):
        ast = sample(count=3, nested=False)
        h2 = next(b for b in ast["blocks"] if b.get("t") == "Header" and b["c"][0] == 2)
        h2["c"][2] = [
            {"t": "Str", "c": "Analysis of"}, {"t": "Space"},
            {"t": "Math", "c": [{"t": "InlineMath"}, r"\alpha^2"]},
        ]
        prepare_presentation_ast(ast)
        self.assertEqual(1, sum(b.get("t") == "Math" for b in h2["c"][2]))
        # TOC labels stay textual, not a duplicate math rendering or extra section.
        item = self._contents(ast)[0]["c"][0][0]["c"][0]["c"]
        self.assertEqual(item[1][0]["t"], "Str")
        self.assertIn(r"\alpha", item[1][0]["c"])
        self.assertEqual(3, sum(b.get("t") == "Header" and b["c"][0] == 2 for b in ast["blocks"]))

    def test_actual_pandoc_renders_labeled_nav_and_local_fragments(self):
        source = ROOT / "templates/work/manuscript.md"
        ast, errors = parse_document(source, Path("templates/work/manuscript.md"))
        self.assertEqual([], errors)
        ast = prepare_presentation_ast(ast)
        ast["meta"].update({
            "pagetitle": {"t": "MetaString", "c": "Sample Work"},
            "lang": {"t": "MetaString", "c": "en"},
            "edition": {"t": "MetaString", "c": "v1.0.0"},
            "record_url": {"t": "MetaString", "c": "https://press.example.org/works/sample/v1.0.0"},
            "source_commit": {"t": "MetaString", "c": "a" * 40},
        })
        template, _, _ = canonical_template(ROOT)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "template.html"
            path.write_text(template)
            result = subprocess.run([
                "pandoc", "--fail-if-warnings", "--from=json", "--to=html5", "--standalone",
                "--mathml", "--template=" + str(path),
            ], input=json.dumps(ast), capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        raw = result.stdout
        self.assertIn('<a class="press-skip" href="#press-main"', raw)
        self.assertIn('<nav id="press-contents" class="press-contents" aria-labelledby="press-contents-label">', raw)
        self.assertIn('class="press-contents-link" href="#press-contents"', raw)
        nav = re.search(r'<nav id="press-contents".*?</nav>', raw, re.S)[0]
        self.assertEqual(["#sec-1", "#sec-2", "#sec-3"],
                         re.findall(r'href="(#[^"]+)"', nav))
        self.assertNotIn('href="#theorem-1"', re.search(r'<nav id="press-contents".*?</nav>', raw, re.S)[0])
        self.assertEqual(9, raw.count("<math"))  # TOC does not duplicate manuscript MathML.


class NavigationBuildTests(unittest.TestCase):
    def test_real_editions_select_sections_and_group_gfm_evidence(self):
        for count, records, expected in ((1, 0, False), (2, 0, False), (3, 0, True),
                                         (5, 0, True), (24, 0, True), (2, 1, True),
                                         (1, 2, False), (1, 3, True)):
            with self.subTest(sections=count, records=records):
                fixture = edition_fixtures.EditionTests()
                try:
                    fixture.setUp()
                    source = fixture.repo / 'works/sample/manuscript.md'
                    front = source.read_text().split('---\n', 2)[1]
                    body = '# On Structure and Identity\n\n'
                    for n in range(1, count + 1):
                        body += (f'<a id="sec-{n}"></a>\n## Section {n} — Analysis of $x=x$\n\n'
                                 'Reviewed section text.\n\n'
                                 f'<a id="detail-{n}"></a>\n### Lemma {n} — Detail\n\n'
                                 'The detail is citable without entering the contents.\n\n')
                    directory = source.parent / 'supporting'
                    directory.mkdir()
                    for n in range(records):
                        name = f'note-{n}'
                        suffix = '.md'
                        original = ROOT / ('templates/work/supporting/note' + suffix)
                        (directory / (name + suffix)).write_bytes(original.read_bytes())
                        body += f'See the [record {n}](supporting/{name}{suffix}).\n\n'
                    source.write_text('---\n' + front + '---\n\n' + body.rstrip() + '\n')
                    fixture.git('add', 'works'); fixture.git('commit', '-qm', 'Review navigation case')
                    fixture.commit = fixture.git('rev-parse', 'HEAD')
                    manifest = fixture.build()
                    self.assertEqual(manifest, verify_edition(fixture.output))
                    raw = (fixture.output / manifest['canonical_artifact']).read_text()
                    toc = re.search(r'<nav id="press-contents".*?</nav>', raw, re.S)
                    self.assertEqual(expected, toc is not None)
                    self.assertEqual(expected, 'class="press-contents-link"' in raw)
                    if toc:
                        targets = [f'#sec-{n}' for n in range(1, count + 1)]
                        if records:
                            targets.append('#record-md-6-note-0')
                        self.assertEqual(targets, re.findall(r'href="(#[^"]+)"', toc.group()))
                        self.assertEqual(int(records > 0), toc.group().count('Supporting records'))
                        self.assertNotIn('<math', toc.group())
                finally:
                    fixture.doCleanups()


if __name__ == "__main__":
    unittest.main()
