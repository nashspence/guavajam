"""Selection, byte integrity and literal rendering of reviewed supporting records."""
import copy
import hashlib
import html
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from asset_policy import AssetError
from render_profile import prepare_presentation_ast
from supporting_records import append_supporting_records, load_record, record_path, validate_record

RECORD = {"title": "A <note>", "scope": "Selected notebook pages.",
          "passages": [{"id": "first", "label": "Entry 1", "text": "\n  α <script>\t&\r\n"}]}
MANUSCRIPT = Path("works/example/manuscript.md")


def manuscript(*urls):
    return {"blocks": [{"t": "Para", "c": [
        {"t": "Link", "c": [["", [], []], [{"t": "Str", "c": "source"}], [url, ""]]}
        for url in urls
    ]}]}


class SupportingRecordTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = MANUSCRIPT.parent / "supporting/field-note.json"
        self.absolute = self.root / self.path
        self.absolute.parent.mkdir(parents=True)
        self.absolute.write_text(json.dumps(RECORD, indent=2), encoding="utf-8")

    def test_paths_and_schema(self):
        self.assertTrue(record_path(self.path))
        for path in ("works/example/supporting/not_a_slug.json",
                     "works/example/supporting/private/data.json", "works/example/assets/record.json",
                     "works/example/supporting/r.json#fragment", "works/example/supporting/r.JSON"):
            self.assertFalse(record_path(Path(path)), path)
        self.assertEqual(load_record(self.absolute), RECORD)
        invalid = [
            {**RECORD, "mapping": {}},
            {**RECORD, "title": " "},
            {**RECORD, "scope": "line 1\nline 2"},
            {**RECORD, "passages": []},
            {**RECORD, "passages": RECORD["passages"] * 2},
        ]
        for change in ({"text": {}}, {"text": "\ud800"}, {"label": "a\nb"}, {"id": "a#b"}, {"extra": 1}):
            invalid.append({**RECORD, "passages": [{**RECORD["passages"][0], **change}]})
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(AssetError):
                validate_record(item)
        with patch("supporting_records.MAX_RECORD_BYTES", 8), self.assertRaises(AssetError):
            load_record(self.absolute)
        self.absolute.write_bytes(b'{"title": "a", "title": "b"}')
        with self.assertRaisesRegex(AssetError, "duplicate key"):
            load_record(self.absolute)
        self.absolute.unlink()
        self.absolute.symlink_to("another-file")
        with self.assertRaisesRegex(AssetError, "regular file"):
            load_record(self.absolute)

    def test_first_reference_selects_whole_record_and_digest_commits_reviewed_bytes(self):
        second = self.path.with_name("field.json")
        other = copy.deepcopy(RECORD)
        other["passages"][0]["id"] = "note-first"
        (self.root / second).write_text(json.dumps(other))
        ast = manuscript("supporting/field.json#note-first", "supporting/field-note.json",
                         "supporting/field-note.json#first", "https://example.org/record#part")
        append_supporting_records(ast, self.root, MANUSCRIPT, {self.path, second})
        prepare_presentation_ast(ast)
        links = [node["c"][2][0] for node in ast["blocks"][0]["c"]]
        self.assertEqual(links, ["#passage-5-field-note-first", "#record-field-note",
                                 "#passage-10-field-note-first", "https://example.org/record#part"])
        ids = [block["c"][1][0] for block in ast["blocks"] if block["t"] == "Header"]
        self.assertEqual(ids, ["record-field", links[0][1:], links[1][1:], links[2][1:]])
        literal = RECORD["passages"][0]["text"]
        rendered = [block["c"][1] for block in ast["blocks"] if block["t"] == "RawBlock"]
        self.assertEqual(len(rendered), 2)
        for body in rendered:
            self.assertNotIn("<script>", body)
            self.assertIn(literal, html.unescape(body))
        self.assertIn(hashlib.sha256(self.absolute.read_bytes()).hexdigest(), json.dumps(ast))
        untouched = manuscript("https://example.org/supporting/field-note.json")
        before = copy.deepcopy(untouched)
        append_supporting_records(untouched, self.root, MANUSCRIPT, {self.path})
        self.assertEqual(before, untouched)

    def test_broken_references_fail_before_publication(self):
        for url in ("supporting/field-note.json?raw=1", "supporting/field-note.json#",
                    "supporting/../private.json", "supporting/field-note.json#not-a-passage",
                    "supporting/untracked.json"):
            with self.subTest(url=url), self.assertRaises(AssetError):
                append_supporting_records(manuscript(url), self.root, MANUSCRIPT, {self.path})
        ast = manuscript("supporting/field-note.json")
        ast["blocks"].extend([
            {"t": "RawBlock", "c": ["html", '<a id="record-field-note"></a>']},
            {"t": "Header", "c": [2, ["", [], []], [{"t": "Str", "c": "Collision"}]]},
        ])
        append_supporting_records(ast, self.root, MANUSCRIPT, {self.path})
        with self.assertRaisesRegex(AssetError, "duplicate"):
            prepare_presentation_ast(ast)
