import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from check import SCHEMA, errors

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


class ContractTests(unittest.TestCase):
    def test_schema(self):
        Draft202012Validator.check_schema(SCHEMA)

    def test_valid_fixtures(self):
        files = sorted(FIXTURES.glob("*.json"))
        self.assertTrue(files)
        for path in files:
            with self.subTest(path=path.name):
                self.assertEqual(errors(json.loads(path.read_text())), [])

    def test_invalid_variants(self):
        cases = [
            ("handoff-edited", ("issue",), None),
            ("handoff-edited", ("source", "base", "commit"), "abbreviated"),
            ("handoff-edited", ("source", "work_state"), "standalone"),
            ("handoff-standalone", ("source", "work_state"), "edited"),
            ("handoff-edited", ("checks", 0, "environment"), None),
            ("handoff-standalone", ("checks", 0, "reason"), None),
            ("handoff-edited", ("integrated",), True),
            ("publication-exact", ("handoff", "commit"), "c" * 40),
            ("publication-exact", ("verification", "result"), "unchecked"),
            ("publication-reconstructed", ("supplied", "kind"), "archive"),
            ("disposition-rejected", ("integration",), {"url": "https://example.org/pr/1", "commit": "d" * 40}),
            ("disposition-rejected", ("reason",), None),
            ("disposition-deferred", ("kind",), "legacy-handoff"),
        ]
        for name, path, value in cases:
            with self.subTest(fixture=name, change=path):
                data = copy.deepcopy(fixture(name))
                parent = data
                for key in path[:-1]:
                    parent = parent[key]
                if value is None:
                    parent.pop(path[-1])
                else:
                    parent[path[-1]] = value
                self.assertTrue(errors(data), (name, path))


if __name__ == "__main__":
    unittest.main()
