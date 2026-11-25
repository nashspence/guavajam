#!/usr/bin/env python3
"""Create and parse literal GuavaJam GFM from all real Riverhog native chunks.

This is a text-preserving search fixture, not a final table/link formatter or a
signed publication. The production supporting-source loader and navigation
extractor determine every output text, anchor, title, kind, level and parent.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import platform
import string
import subprocess
import sys
import time
from pathlib import Path


PUNCTUATION = frozenset(string.punctuation)


def literal(text: str) -> str:
    # GFM permits backslash escapes for ASCII punctuation. Escaping every
    # punctuation character keeps source code, URLs, HTML and math literal.
    assert text == " ".join(text.split()), "native chunks must already have normalized whitespace"
    return "".join("\\" + character if character in PUNCTUATION else character for character in text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("guavajam", type=Path)
    parser.add_argument("pandoc", type=Path)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    args.guavajam = args.guavajam.resolve()
    args.pandoc = args.pandoc.resolve()
    args.output = args.output.resolve()
    revision = subprocess.check_output(["git", "-C", str(args.guavajam), "rev-parse", "HEAD"], text=True).strip()
    assert revision == "54fce07b619628a5e92be946be4b189873ddf1a0"
    version = subprocess.check_output([str(args.pandoc), "--version"], text=True).splitlines()[0]
    assert version == "pandoc 3.12.1", version
    os.environ["PATH"] = str(args.pandoc.parent) + os.pathsep + os.environ["PATH"]
    sys.path[:0] = [str(args.guavajam / "src"), str(args.guavajam / "some-implementations/a-guavajam-static-website-generator/src")]
    from guavajam.supporting_gfm import load_gfm_record
    from guavajam.supporting_metadata import document_frontmatter
    from a_guavajam_static_website_generator.navigation import extract

    raw_corpus = args.corpus.read_bytes()
    rows = [json.loads(line) for line in raw_corpus.decode().splitlines()]
    args.output.mkdir(parents=True, exist_ok=True)
    source_root = args.output / "guavajam-source"

    def metadata_for(row: dict) -> dict:
        key = hashlib.sha256(row["id"].encode()).hexdigest()
        return {**row["metadata"], "Label": ["all", "Café" if int(key[0], 16) % 2 else "Other"], "Example version": "v1"}

    def render_source(row: dict, layout: str) -> tuple[str, str, dict]:
        metadata = metadata_for(row)
        abstract = f"{row['metadata']['Interface']} · {row['metadata']['Authority']}"
        source = document_frontmatter(row["title"], abstract=abstract, metadata=list(metadata.items()))
        source += '<a name="metadata"></a>\n\n'
        for passage in row["passages"]:
            anchor, body = passage["anchor"], passage["body"]
            if layout == "headings":
                title = passage["title"]
                source += f'<a name="section-{anchor}"></a>\n\n## {literal(title)}\n\n'
                if body == title:
                    body = ""
                elif body.startswith(title + " "):
                    body = body[len(title) + 1:]
            if body:
                source += f'<a name="{anchor}"></a>\n\n{literal(body)}\n\n'
        return source.rstrip() + "\n", abstract, metadata

    def make_record(row: dict) -> dict:
        source, abstract, metadata = render_source(row, "flat")
        path = source_root / row["path"]
        assert path.is_relative_to(source_root / "content/supporting")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        ast, positions, digest, size = load_gfm_record(path)
        navigation = extract(ast, positions)
        assert len(navigation["anchors"]) == len(row["passages"]) + 1
        assert navigation["anchors"][0]["anchor"] == "metadata"
        assert navigation["anchors"][0]["kind"] == "passage"
        for actual, expected in zip(navigation["anchors"][1:], row["passages"]):
            assert actual["anchor"] == expected["anchor"], row["id"]
            assert actual["text"] == expected["body"], (row["id"], expected["anchor"])
            assert actual["kind"] == "passage" and actual["level"] == 0 and actual["parent"] is None
        return {"id": row["id"], "title": row["title"], "abstract": abstract, "path": row["path"],
                "metadata": metadata, "navigation": navigation,
                "source_markdown_sha256": digest, "source_markdown_bytes": size,
                "native_source_html_sha256": row["source_html_sha256"]}

    started = time.perf_counter()
    records = []
    output_jsonl = args.output / "guavajam-documents.jsonl"
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor, output_jsonl.open("w", encoding="utf-8") as stream:
        for index, record in enumerate(executor.map(make_record, rows), 1):
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            records.append(record)
            if index % 1000 == 0:
                print(f"Parsed and verified {index}/{len(rows)} supporting files", flush=True)
    elapsed = time.perf_counter() - started
    # Compare a minimal document, median-sized document, largest document, and
    # document with most headings. These are real source-native records.
    ordered = sorted(rows, key=lambda row: len(row["body"].encode()))
    sample_indices = [0, len(ordered) // 2, len(ordered) - 1]
    samples = [ordered[index] for index in sample_indices]
    samples.append(max(rows, key=lambda row: len(row["passages"])))
    by_id = {record["id"]: record for record in records}
    comparisons = []
    for index, row in enumerate(samples):
        source, _, _ = render_source(row, "headings")
        sample_path = args.output / f"heading-sample-{index}.md"
        sample_path.write_text(source, encoding="utf-8")
        ast, positions, _, size = load_gfm_record(sample_path)
        nav = extract(ast, positions)
        flat = by_id[row["id"]]["navigation"]
        comparisons.append({"id": row["id"], "native_heading_chunks": len(row["passages"]),
                            "flat_document_text_bytes": len(flat["text"].encode()),
                            "heading_document_text_bytes": len(nav["text"].encode()),
                            "same_document_text": flat["text"] == nav["text"],
                            "flat_passages": len(flat["anchors"]), "heading_passages": len(nav["anchors"]),
                            "flat_passage_text_bytes": sum(len(anchor["text"].encode()) for anchor in flat["anchors"]),
                            "heading_passage_text_bytes": sum(len(anchor["text"].encode()) for anchor in nav["anchors"]),
                            "heading_source_bytes": size})
    flattened = [anchor for record in records for anchor in record["navigation"]["anchors"]]
    manifest = {"kind": "research-valid-supporting-gfm-navigation-corpus", "guavajam_commit": revision,
                "python": platform.python_version(), "pandoc": version, "workers": args.workers,
                "input_corpus_sha256": hashlib.sha256(raw_corpus).hexdigest(),
                "output": {"file": output_jsonl.name, "bytes": output_jsonl.stat().st_size,
                           "sha256": hashlib.sha256(output_jsonl.read_bytes()).hexdigest()},
                "documents": len(records), "passages": len(flattened),
                "source_markdown_bytes": sum(record["source_markdown_bytes"] for record in records),
                "max_source_markdown_bytes": max(record["source_markdown_bytes"] for record in records),
                "navigation_document_text_bytes": sum(len(record["navigation"]["text"].encode()) for record in records),
                "navigation_passage_text_bytes": sum(len(anchor["text"].encode()) for anchor in flattened),
                "max_navigation_passage_text_bytes": max(len(anchor["text"].encode()) for anchor in flattened),
                "metadata_passage_text_bytes": sum(len(record["navigation"]["anchors"][0]["text"].encode()) for record in records),
                "elapsed_supporting_parse_seconds": elapsed,
                "checks": {"all_current_supporting_blob_loaders_passed": True,
                           "all_native_chunk_text_roundtrips_exact": True,
                           "all_record_ids_match_complete_native_corpus": [record["id"] for record in records] == [row["id"] for row in rows],
                           "all_flat_passages_have_production_kind_level_scope": all(anchor["kind"] == "passage" and anchor["level"] == 0 and anchor["scope"] == "document" for anchor in flattened)},
                "layout_comparison_samples": comparisons,
                "limitations": ["Literal flat paragraphs preserve visible native text, not the final table/list/link presentation or original heading structure.",
                                 "The production per-file supporting-source loader and navigation extractor ran. Whole-repository source validation still has its stock 128-document guard and was not claimed here.",
                                 "No signed-publication, complete renderer, citation or browser qualification is implied.",
                                 "Metadata includes explicitly modeled Label and Example version values. Software release semantics remain outside GuavaJam.",
                                 "Alternative heading-plus-body GFM requires extra explicit block anchors and duplicates body text into section and block passage rows under current navigation semantics."]}
    (args.output / "guavajam-navigation-manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(manifest, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
