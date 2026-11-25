#!/usr/bin/env python3
"""Extract one search document per real Riverhog Contract Closure element.

Reads the source-owned generated candidate. Keeps only its native human-contract
text, excluding audit/navigation controls and duplicate exact-machine JSON.
Headings define experimental passage segmentation, not a new Riverhog contract.
The full exact pointer declarations are retained separately without truncation.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import importlib.metadata
import json
import platform
import statistics
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class NativeText(HTMLParser):
    """Collect visible human-reference text with deterministic heading boundaries."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.div_depth = 0
        self.found = 0
        self.heading_level = 0
        self.heading_text: list[str] = []
        self.title = "Contract facts"
        self.pieces: list[str] = []
        self.passages: list[dict[str, object]] = []

    def finish_passage(self) -> None:
        body = " ".join(" ".join(self.pieces).split())
        if body:
            index = len(self.passages)
            self.passages.append({"anchor": f"native-{index:04d}", "title": self.title,
                                  "position": index, "body": body})
        self.pieces = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "div":
            if self.div_depth:
                self.div_depth += 1
            elif "human-contract" in (values.get("class") or "").split():
                self.div_depth = 1
                self.found += 1
        if self.div_depth and tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.finish_passage()
            self.heading_level = int(tag[1])
            self.heading_text = []

    def handle_endtag(self, tag: str) -> None:
        if self.div_depth and tag == f"h{self.heading_level}":
            self.title = " ".join(" ".join(self.heading_text).split())
            self.heading_level = 0
        if self.div_depth and tag == "div":
            self.div_depth -= 1
            if not self.div_depth:
                self.finish_passage()

    def handle_data(self, data: str) -> None:
        if self.div_depth:
            self.pieces.append(data)
            if self.heading_level:
                self.heading_text.append(data)


def distribution(values: list[int]) -> dict[str, int | float]:
    ordered = sorted(values)
    return {"min": ordered[0], "median": statistics.median(ordered),
            "p95": ordered[int((len(ordered) - 1) * .95)], "max": ordered[-1],
            "sum": sum(ordered)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.repository = args.repository.resolve()
    sys.path.insert(0, str(args.repository / "scripts"))
    from contract_atlas.model import canonical_bytes, pointer_value
    from contract_atlas.records import load_closure
    from contract_atlas.html_rendering import _element_file
    from contract_atlas.generation import source_revision

    manifest = json.loads((args.candidate / "build-manifest.json").read_bytes())
    revision = manifest["source_sha"]
    assert source_revision(args.repository, revision) == revision
    closure_path = args.candidate / "riverhog-v1.json"
    closure = load_closure(closure_path)
    assert digest(closure_path.read_bytes()) == manifest["files"]["riverhog-v1.json"]
    args.output.mkdir(parents=True, exist_ok=True)
    doc_sizes: list[int] = []
    declaration_sizes: list[int] = []
    passage_sizes: list[int] = []
    passage_counts: list[int] = []
    interfaces: collections.Counter[str] = collections.Counter()
    authorities: collections.Counter[str] = collections.Counter()
    documents = args.output / "documents.jsonl"
    declarations = args.output / "declarations.jsonl"
    with documents.open("w", encoding="utf-8") as docs, declarations.open("wb") as exact:
        for element in sorted(closure["elements"], key=lambda x: x["id"]):
            filename = _element_file(element["id"])
            source_path = "riverhog-v1/" + filename
            raw = (args.candidate / source_path).read_bytes()
            assert digest(raw) == manifest["files"][source_path]
            native = NativeText()
            native.feed(raw.decode("utf-8"))
            native.close()
            assert native.found == 1, element["id"]
            assert native.passages, element["id"]
            assert len(set(p["anchor"] for p in native.passages)) == len(native.passages)
            declared = {"id": element["id"], "values": [
                {"pointer": pointer, "value": pointer_value(closure, pointer)}
                for pointer in element["pointers"]]}
            declared_raw = canonical_bytes(declared)
            exact.write(declared_raw + b"\n")
            body = "\n\n".join(str(p["body"]) for p in native.passages)
            document = {"id": element["id"], "title": element["title"],
                        "path": "content/supporting/" + filename.removesuffix(".html") + ".md",
                        "metadata": {"Authority": element["authority"],
                                     "Interface": element["interface"],
                                     "Element ID": element["id"]},
                        "passages": native.passages, "body": body,
                        "related_element_ids": element.get("related_element_ids", []),
                        "source_html_path": source_path, "source_html_sha256": digest(raw),
                        "declarations_sha256": digest(declared_raw),
                        "pointers": element["pointers"]}
            docs.write(json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n")
            doc_sizes.append(len(body.encode()))
            declaration_sizes.append(len(declared_raw))
            passage_counts.append(len(native.passages))
            passage_sizes.extend(len(str(p["body"]).encode()) for p in native.passages)
            interfaces[element["interface"]] += 1
            authorities[element["authority"]] += 1
    dependencies = sorted({d.metadata["Name"]: d.version for d in importlib.metadata.distributions()}.items())
    environment = {"python": platform.python_version(), "platform": platform.platform(),
                   "dependencies": dict(dependencies)}
    (args.output / "environment.json").write_text(json.dumps(environment, indent=2) + "\n")
    stats = {"source_repository": "https://github.com/nashspence/riverhog", "source_commit": revision,
             "source_tree": subprocess.check_output(["git", "-C", str(args.repository), "rev-parse", "HEAD^{tree}"], text=True).strip(),
             "source_worktree": "clean", "closure_sha256": digest(closure_path.read_bytes()),
             "closure_bytes": closure_path.stat().st_size, "candidate_file_count": len(manifest["files"]),
             "candidate_bytes": sum((args.candidate / name).stat().st_size for name in manifest["files"]),
             "documents": len(doc_sizes), "passages": len(passage_sizes),
             "document_body_bytes": distribution(doc_sizes), "passage_body_bytes": distribution(passage_sizes),
             "passages_per_document": distribution(passage_counts), "declaration_bytes": distribution(declaration_sizes),
             "interfaces": dict(sorted(interfaces.items())), "authorities": dict(sorted(authorities.items())),
             "artifacts": {p.name: {"bytes": p.stat().st_size, "sha256": digest(p.read_bytes())}
                           for p in [documents, declarations, args.output / "environment.json"]},
             "method": "Source-owned complete Contract Closure inventory; generated native human-contract text, heading-based experimental passages, no text truncation; audit/navigation and duplicate exact-JSON panels excluded. Full exact declared pointer values retained separately.",
             "limitations": ["No release authoring tree selected; this is current development native reference, not an authored release manual.",
                              "Heading segmentation and supporting filenames are an experimental adapter, not validated GuavaJam source input.",
                              "Does not qualify a Riverhog release or prove all downstream GuavaJam render/search behavior."]}
    (args.output / "manifest.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: v for k, v in stats.items() if k not in {"authorities", "interfaces"}}, indent=2))


if __name__ == "__main__":
    main()
