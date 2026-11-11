"""Exercise the candidate gate on the copied starter through the real verifier."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "tests")]
from qualify_edition import qualify_edition
from test_preview_site import PreviewTests


if __name__ == "__main__":
    fixture = PreviewTests()
    try:
        fixture.setUp()
        fixture.assemble()
        package = fixture.preview / "package"
        before = {p.name: p.read_bytes() for p in package.iterdir()}
        report = qualify_edition(package, fixture.base / "qualification")
        assert report["status"] == "passed"
        assert report["provenance_sha256"] == hashlib.sha256(before["provenance.json"]).hexdigest()
        assert before == {p.name: p.read_bytes() for p in package.iterdir()}
        assert set(report["print"]) == {"A4", "Letter"}
        assert set(report["browser"]["screens"]) == {"desktop", "mobile", "mobile-dark", "mobile-enlarged"}
        print(json.dumps({"candidate": report["tag"], "qualification": report["status"],
                          "provenance_sha256": report["provenance_sha256"], "print": report["print"]}))
    finally:
        fixture.doCleanups()
