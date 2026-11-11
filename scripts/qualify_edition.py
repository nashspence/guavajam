#!/usr/bin/env python3
"""Qualify an already verified candidate without rebuilding or changing its bytes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unicodedata

import fitz

from asset_policy import AssetError
from edition_package import package_names
from verify_edition import verify_edition


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "press.candidate-qualification.v1"
TIMEOUT = 240
MAX_REPORT_BYTES = 1024 * 1024
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_PAGES = 200
PAPERS = {"A4": (595.276, 841.89), "Letter": (612, 792)}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def package_snapshot(directory, manifest):
    return {name: digest(directory / name) for name in package_names(manifest)}


def normalized_text(value):
    # MathML may use mathematical alphabet variants and invisible operators.
    return "".join(c for c in unicodedata.normalize("NFKC", value)
                   if not c.isspace() and c not in "\u2061\u2062\u2063\u2064\u200b\u00ad")


def verify_printed_pdf(path, paper, math_tokens):
    """Inspect actual print output, including content outside the page crop box."""
    if paper not in PAPERS or not path.is_file() or not 0 < path.stat().st_size <= MAX_PDF_BYTES:
        raise AssetError(f"{paper}: missing PDF or PDF exceeds the 50 MiB qualification bound")
    expected_width, expected_height = PAPERS[paper]
    with fitz.open(path) as document:
        if not 0 < len(document) <= MAX_PAGES:
            raise AssetError(f"{paper}: print qualification supports 1–{MAX_PAGES} pages; review the candidate size")
        text = []
        for number, page in enumerate(document, 1):
            if abs(page.rect.width - expected_width) > 2 or abs(page.rect.height - expected_height) > 2:
                raise AssetError(f"{paper}: page {number} has an unexpected paper size")
            # Default PDF extraction clips to the page and can silently omit the
            # very glyphs this check must detect. Inspect the infinite rectangle.
            words = page.get_text("words", clip=fitz.INFINITE_RECT())
            for word in words:
                if word[0] < -1 or word[1] < -1 or word[2] > page.rect.width + 1 or word[3] > page.rect.height + 1:
                    raise AssetError(f"{paper}: printed text extends outside page {number}; check wide mathematics or tables")
            text.append(page.get_text(clip=fitz.INFINITE_RECT()))
        packed = normalized_text("".join(text))
        if not packed:
            raise AssetError(f"{paper}: printed edition contains no extractable text")
        missing = [token for token in math_tokens if normalized_text(token) not in packed]
        if missing:
            raise AssetError(f"{paper}: printed mathematics lost visible tokens: {missing[:8]!r}")
        return {"pages": len(document), "byte_count": path.stat().st_size,
                "sha256": digest(path), "math_tokens_checked": len(math_tokens)}


def qualify_edition(directory, output, *, runner=subprocess.run):
    directory = Path(directory).absolute()
    output = Path(output).absolute()
    if directory.resolve() != directory or not directory.is_dir():
        raise AssetError("qualification requires an extracted regular edition directory")
    if output.exists() or output.is_symlink() or output.resolve() != output or output.is_relative_to(directory):
        raise AssetError("qualification output must be a new directory outside the edition package")
    manifest = verify_edition(directory)
    before = package_snapshot(directory, manifest)
    report = {"schema": SCHEMA, "status": "failed", "tag": manifest["tag"],
              "source_commit": manifest["source_commit"], "builder_commit": manifest["builder_commit"],
              "presentation_profile": manifest["presentation_profile"],
              "provenance_sha256": before["provenance.json"], "html_sha256": manifest["html_sha256"],
              "package_sha256": before, "limits": {"browser_seconds": TIMEOUT,
                  "pdf_bytes": MAX_PDF_BYTES, "pdf_pages": MAX_PAGES},
              "browser": None, "print": {}, "failures": []}
    output.mkdir(parents=True)
    request = {"file": str(directory / manifest["canonical_artifact"]),
               "canonical_url": manifest["record_url"] + "/read", "output": str(output),
               "html_sha256": manifest["html_sha256"], "provenance_sha256": before["provenance.json"]}
    try:
        result = runner(["node", str(ROOT / "scripts/qualify_edition.mjs")],
                        input=json.dumps(request), text=True, capture_output=True,
                        timeout=TIMEOUT, cwd=ROOT)
        path = output / "browser.json"
        if not path.is_file() or path.stat().st_size > MAX_REPORT_BYTES:
            detail = (result.stderr or result.stdout or "no browser report")[-2000:].strip()
            raise AssetError("candidate browser qualification did not complete: " + detail)
        browser = json.loads(path.read_text())
        if (browser.get("schema") != "press.candidate-browser.v1"
                or browser.get("html_sha256") != manifest["html_sha256"]
                or browser.get("provenance_sha256") != before["provenance.json"]
                or not isinstance(browser.get("failures"), list)
                or any(not isinstance(failure, str) for failure in browser["failures"])):
            raise AssetError("browser qualification report names different candidate bytes")
        report["browser"] = browser
        report["failures"].extend(browser["failures"])
        if result.returncode and not browser["failures"]:
            report["failures"].append("candidate browser process did not finish successfully")
        if not browser["failures"] and (set(browser.get("screens", {})) !=
                {"desktop", "mobile", "mobile-dark", "mobile-enlarged"}
                or set(browser.get("print", {})) != set(PAPERS)
                or not isinstance(browser.get("structure"), dict)):
            report["failures"].append("candidate browser report omits required qualification cases")
        tokens = browser.get("math_tokens", [])
        if not isinstance(tokens, list) or any(not isinstance(token, str) for token in tokens):
            raise AssetError("invalid browser mathematics inventory")
        for paper in PAPERS:
            try:
                report["print"][paper] = verify_printed_pdf(output / (paper + ".pdf"), paper, tokens)
            except (ValueError, RuntimeError, OSError) as exc:
                report["failures"].append(str(exc))
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        report["failures"].append(str(exc))
    finally:
        # A successful second verification alone is insufficient: changed bytes
        # could carry newly generated checksums. Compare every original file too.
        try:
            after = verify_edition(directory)
            if after != manifest or package_snapshot(directory, after) != before:
                raise AssetError("candidate package changed during qualification")
            report["package_unchanged"] = True
        except (OSError, ValueError, KeyError, TypeError) as exc:
            report["package_unchanged"] = False
            report["failures"].append("candidate package changed or failed re-verification: " + str(exc))
        report["status"] = "failed" if report["failures"] else "passed"
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    if report["failures"]:
        raise AssetError("candidate qualification failed; review " + str(output / "report.json") +
                         ": " + "; ".join(report["failures"][:4]))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="the exact extracted candidate package")
    parser.add_argument("--output", type=Path, required=True, help="new evidence directory outside the package")
    args = parser.parse_args()
    try:
        result = qualify_edition(args.directory, args.output)
        print(f"Qualified {result['tag']} at provenance SHA-256 {result['provenance_sha256']}; report: {args.output / 'report.json'}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
