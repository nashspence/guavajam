"""Validate current Proffer records; never implies authority or verifies attestations."""

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA = json.loads((Path(__file__).parent / "schema/proffer.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def errors(record):
    failures = sorted(VALIDATOR.iter_errors(record), key=lambda e: str(list(e.absolute_path)))
    if failures:
        return [f"{list(e.absolute_path)}: {e.message}" for e in failures]

    if record["kind"] == "publication" and record["verification"]["method"] == "commit-identity":
        supplied = record["supplied"]
        if supplied["kind"] != "commit" or supplied["id"] != record["handoff"]["commit"]:
            return ["commit-identity requires the supplied and published commit IDs to match"]
    return []


def main(paths):
    if not paths:
        print("usage: python check.py RECORD.json [RECORD.json ...]", file=sys.stderr)
        return 2
    failed = False
    for name in paths:
        try:
            record = json.loads(Path(name).read_text())
            problems = errors(record)
        except (OSError, ValueError) as exc:
            problems = [str(exc)]
        for problem in problems:
            print(f"{name}: {problem}", file=sys.stderr)
        failed |= bool(problems)
    return int(failed)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
