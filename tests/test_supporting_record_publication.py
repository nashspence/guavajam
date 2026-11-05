"""Publish selected records from the reviewed Git snapshot into the standalone edition."""
import hashlib
import html
import json
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

import test_assets as fixtures
from validate_sources import git_sources, validate_repository
from verify_edition import EditionHTML, verify_edition


ROOT = Path(__file__).resolve().parents[1]


class SupportingRecordPublicationTests(fixtures.EditionTests):
    def test_reviewed_records_and_passage_links_travel_with_the_edition(self):
        manuscript = self.repo / "works/sample/manuscript.md"
        directory = manuscript.parent / "supporting"
        directory.mkdir()
        record = directory / "draft.json"
        literal = "  Test <script>bad()</script>\n\n![](https://example.org/tracker) *verbatim* α"

        def reply(text):
            return {"parent": "root", "message": {
                "author": {"role": "assistant"},
                "content": {"content_type": "text", "parts": [text]}}}

        archive = self.base / "private-export.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("conversations-001.json", json.dumps([
                {"id": "chosen", "title": "Discussion", "current_node": "current",
                 "mapping": {"root": {"parent": None, "message": None}, "current": reply(literal),
                             "sibling": reply("Discarded answer")}},
                {"id": "unrelated", "title": "Unrelated account chat"}]))
        subprocess.run([sys.executable, ROOT / "scripts/extract_chatgpt.py", archive,
                        "--conversation", "chosen", "--output", record], check=True)
        source = json.loads(record.read_bytes())
        passage = source["passages"][0]["id"]
        digest = hashlib.sha256(record.read_bytes()).hexdigest()

        # Exercise the actual starter a non-ChatGPT author would copy.
        note = (ROOT / "templates/work/supporting/note.json").read_bytes()
        (directory / "note.json").write_bytes(note)
        note_data = json.loads(note)
        (directory / "other.json").write_text(json.dumps({**note_data, "title": "Uncited note"}))
        with manuscript.open("a", encoding="utf-8") as stream:
            stream.write(f"\nSee the [exchange](supporting/draft.json#{passage}), "
                         "[whole transcript](supporting/draft.json), and [note](supporting/note.json).\n")
        self.git("add", "works")
        self.assertEqual([], validate_repository(self.repo, git_sources(self.repo)))
        self.git("commit", "-qm", "Review the synthesis and supporting records")
        self.commit = self.git("rev-parse", "HEAD")
        record.write_text("unreviewed replacement")
        archive.unlink()

        manifest = self.build()
        self.assertEqual(manifest, verify_edition(self.output, commit=self.commit, tag=self.tag))
        self.assertEqual({manifest["canonical_artifact"], "provenance.json", "SHA256SUMS"},
                         {path.name for path in self.output.iterdir()})
        page = (self.output / manifest["canonical_artifact"]).read_text(encoding="utf-8")
        parsed = EditionHTML(manifest["presentation_profile"])
        parsed.feed(page)
        for fragment in ("record-note", "record-draft", f"passage-5-draft-{passage}"):
            self.assertIn(fragment, parsed.fragment_links)
            self.assertIn((fragment, manifest["record_url"] + "/read#" + fragment), parsed.permalinks)
        self.assertIn(digest, page)
        self.assertIn(literal, html.unescape(page))
        self.assertIn(note_data["passages"][-1]["text"], html.unescape(page))
        self.assertNotIn("<script>bad()</script>", page)
        for omitted in ("Uncited note", "Discarded answer", "Unrelated account chat"):
            self.assertNotIn(omitted, page)


def load_tests(loader, tests, pattern):
    # The base class supplies the real Git repository fixture and offline asset fetcher.
    return unittest.TestSuite(SupportingRecordPublicationTests(name)
                              for name in SupportingRecordPublicationTests.__dict__ if name.startswith("test_"))
