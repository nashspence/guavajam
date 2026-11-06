"""Selection, disclosure boundaries and output safety of the local export importer."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from extract_chatgpt import transcript


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.record = {"id": "selected", "title": "A question", "current_node": "tip",
                       "mapping": {"root": {"parent": None, "message": None}}}
        self.node("user", "root", "user", "  Original <script>\nline\n")
        self.node("other", "user", "assistant", "unselected sibling")
        self.node("hidden", "user", "user", "hidden context",
                  metadata={"is_visually_hidden_from_conversation": True})
        self.node("instructions", "hidden", "user", "private custom instructions",
                  metadata={"is_user_system_message": True})
        self.node("tool", "instructions", "tool", "private tool output")
        self.node("call", "tool", "assistant", "tool call arguments", recipient="browser")
        self.node("internal", "call", "assistant", "internal reasoning", channel="analysis")
        self.node("system", "internal", "system", "system context")
        self.node("tip", "system", "assistant", "Visible reply",
                  metadata={"private": "private metadata"})

    def node(self, key, parent, role, text, **fields):
        self.record["mapping"][key] = {"parent": parent, "message": {
            "author": {"role": role}, "content": {"content_type": "text", "parts": [text]}, **fields}}

    def test_selected_branch_and_stable_passages(self):
        record = transcript(self.record, "tip")
        self.assertEqual(set(record), {"title", "scope", "passages"})
        first, second = record["passages"]
        self.assertEqual((first["label"], first["text"]), ("User", "  Original <script>\nline\n"))
        self.assertEqual((second["label"], second["text"]), ("Assistant", "Visible reply"))
        self.assertFalse(any(secret in json.dumps(record) for secret in
                             ("unselected sibling", "hidden context", "custom instructions", "tool output", "arguments",
                              "internal reasoning", "system context", "private metadata")))
        self.assertEqual(first, transcript(self.record, "other")["passages"][0])
        self.assertNotEqual(second["id"], transcript(self.record, "other")["passages"][1]["id"])

    def test_parts_and_unknown_content(self):
        content = self.record["mapping"]["tip"]["message"]["content"]
        content.update(content_type="multimodal_text", parts=["caption", {"asset_pointer": "blob-private"}])
        self.assertEqual(transcript(self.record, "tip")["passages"][-1]["text"],
                         "caption\n\n[Non-text part omitted]")
        content.update(content_type="new-format", parts=["private-structured-data"])
        self.assertEqual(transcript(self.record, "tip")["passages"][-1]["text"],
                         "[Unsupported message content omitted]")

    def test_broken_ancestry(self):
        for parent in ("tip", "missing", []):
            with self.subTest(parent=parent), self.assertRaises(ValueError):
                self.record["mapping"]["user"]["parent"] = parent
                transcript(self.record, "tip")

    def test_checkout_output_including_linked_directories_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "conversations.json"
            source.write_text(json.dumps([self.record]), encoding="utf-8")
            checkout = root / "press"
            scripts = shutil.copytree(SCRIPTS, checkout / "scripts")
            (root / "linked").symlink_to(checkout, target_is_directory=True)
            for parent in (checkout, root / "linked"):
                output = parent / "review.json"
                result = subprocess.run([sys.executable, str(scripts / "extract_chatgpt.py"),
                                         source, "--conversation", "selected", "--output", output],
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("outside the Press checkout", result.stderr)
                self.assertFalse(output.exists())

    def test_sharded_zip_plain_json_and_output_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "export.zip"
            output = root / "record.json"
            payload = json.dumps([self.record])
            json_file = root / "conversations.json"
            json_file.write_text(payload, encoding="utf-8")
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("conversations-000.json", '[{"id":"other","title":"Unrelated"}]')
                archive.writestr("conversations-001.json", payload)
                archive.writestr("private-attachment", "unused")
            scripts = shutil.copytree(SCRIPTS, root / "press/scripts")
            cmd = [sys.executable, str(scripts / "extract_chatgpt.py")]
            args = ["--conversation", "selected", "--output", str(output)]
            subprocess.run([*cmd, source, *args], check=True)
            expected = output.read_bytes()
            self.assertEqual(json.loads(expected), transcript(self.record, "tip"))
            self.assertEqual(json.loads(subprocess.check_output([*cmd, json_file, "--list"])),
                             ["selected", "A question"])
            overwrite = subprocess.run([*cmd, json_file, *args, "--tip", "other"], capture_output=True)
            self.assertNotEqual(overwrite.returncode, 0)
            self.assertEqual(expected, output.read_bytes())
            output.unlink()
            subprocess.run([*cmd, json_file, *args], check=True)
            self.assertEqual(expected, output.read_bytes())
            output.unlink()
            for records in ([self.record] * 2, []):
                json_file.write_text(json.dumps(records), encoding="utf-8")
                result = subprocess.run([*cmd, json_file, *args], capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())
