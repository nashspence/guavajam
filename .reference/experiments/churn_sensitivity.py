#!/usr/bin/env python3
"""Model exact text-payload reuse on the complete extracted Riverhog corpus.

This counts UTF-8 strings and SHA-256-distinct strings. It does not build SQLite,
estimate FTS postings, predict compression, or claim real release history.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
import sys
import time
from pathlib import Path


class Pool:
    def __init__(self) -> None:
        self.lengths: dict[bytes, int] = {}
        self.bytes = 0

    def add_key(self, key: bytes, length: int) -> None:
        if key in self.lengths:
            assert self.lengths[key] == length
        else:
            self.lengths[key] = length
            self.bytes += length

    def add(self, payload: bytes) -> tuple[bytes, int]:
        pair = (hashlib.sha256(payload).digest(), len(payload))
        self.add_key(*pair)
        return pair

    def stats(self) -> dict[str, int]:
        return {"unique_strings": len(self.lengths), "unique_utf8_bytes": self.bytes}


def model(rows: list[dict], scenario: str, passage_policy: str, checkpoints: list[int], *, navigation: bool = False, metadata_text=None) -> dict:
    total_docs = len(rows)
    selected_count = round(total_docs * .05)
    base_parts = [tuple(p["text"].encode() for p in row["navigation"]["anchors"] if p["anchor"] != "metadata") if navigation else tuple(p["body"].encode() for p in row["passages"]) for row in rows]
    separator = b" " if navigation else b"\n\n"
    titles = [row["title"].encode() if navigation else b"" for row in rows]
    current_content = [separator.join(parts) for parts in base_parts]
    current_bodies = [title + b" " + content if title else content for title, content in zip(titles, current_content)]
    current_part_bytes = [sum(map(len, parts)) for parts in base_parts]
    changed_index = [len(parts) - 1 if passage_policy == "last" else max(range(len(parts)), key=lambda i: len(parts[i])) for parts in base_parts]
    metadata = []
    for row in rows:
        h = hashlib.sha256(row["id"].encode()).hexdigest()
        values = {key: value for key, value in row["metadata"].items() if key != "Example version"}
        values["Label"] = ["all", "Café" if int(h[0], 16) % 2 else "Other"]
        text = metadata_text(values) if navigation else " ".join(key + ": " + ("; ".join(value) if isinstance(value, list) else value) for key, value in values.items())
        metadata.append(text.encode())
        if navigation:
            assert text + " Example version v1" == row["navigation"]["anchors"][0]["text"]
    largest = sorted(range(total_docs), key=lambda i: (-len(current_bodies[i]), rows[i]["id"]))[:selected_count]
    rng = random.Random(729)
    native_docs, native_passages = Pool(), Pool()
    for body in current_bodies:
        native_docs.add(body)
    for parts in base_parts:
        for body in parts:
            native_passages.add(body)
    modes = {name: {"documents": Pool(), "metadata": Pool(), "passages": Pool(), "shared": Pool(),
                    "document_occurrence_bytes": 0, "passage_occurrence_bytes": 0}
             for name in ("none", "stable", "edition")}
    for parts in base_parts:
        for body in parts:
            pair = (hashlib.sha256(body).digest(), len(body))
            for data in modes.values():
                data["passages"].add_key(*pair)
                data["shared"].add_key(*pair)
    cumulative_selected: set[int] = set()
    initial_body_bytes = sum(map(len, current_bodies))
    native_doc_occurrence_bytes = 0
    native_passage_occurrence_bytes = 0
    updated_doc_occurrence_bytes = 0
    updated_passage_occurrence_bytes = 0
    result = {"scenario": scenario, "changed_passage": passage_policy,
              "documents_changed_each_update": total_docs if scenario == "all" else selected_count,
              "snapshots": []}
    for edition in range(1, max(checkpoints) + 1):
        if edition == 1:
            selected = []
        elif scenario == "random_5_percent":
            selected = rng.sample(range(total_docs), selected_count)
        elif scenario == "largest_5_percent":
            selected = largest
        else:
            selected = range(total_docs)
        cumulative_selected.update(selected)
        # A changed document replaces its previous modeled marker. It does not
        # accumulate the whole edit history into the current text.
        for index in selected:
            parts = base_parts[index]
            which = changed_index[index]
            changed = parts[which] + f" Modeled contract observation {edition}.".encode()
            current_content[index] = separator.join(parts[:which] + (changed,) + parts[which + 1:])
            current_bodies[index] = titles[index] + b" " + current_content[index] if titles[index] else current_content[index]
            current_part_bytes[index] = sum(map(len, parts)) - len(parts[which]) + len(changed)
            native_docs.add(current_bodies[index])
            pair = native_passages.add(changed)
            updated_doc_occurrence_bytes += len(current_bodies[index])
            updated_passage_occurrence_bytes += len(changed)
            for data in modes.values():
                data["passages"].add_key(*pair)
                data["shared"].add_key(*pair)
        native_doc_occurrence_bytes += sum(map(len, current_bodies))
        native_passage_occurrence_bytes += sum(current_part_bytes)
        for name, data in modes.items():
            for index, body in enumerate(current_bodies):
                suffix = f" Example version{'' if navigation else ':'} v{edition}".encode()
                prefix = (metadata[index] + (suffix if name == "edition" else b"")) if name != "none" else b""
                if navigation:
                    visible_body = titles[index] + b" " + (prefix + b" " if prefix else b"") + current_content[index]
                    if edition == 1 and name == "edition":
                        assert visible_body.decode() == rows[index]["navigation"]["text"]
                else:
                    visible_body = prefix + b"\n\n" + body if prefix else body
                pair = data["documents"].add(visible_body)
                data["shared"].add_key(*pair)
                data["document_occurrence_bytes"] += len(visible_body)
                data["passage_occurrence_bytes"] += current_part_bytes[index] + len(prefix)
                if prefix:
                    pair = data["metadata"].add(prefix)
                    data["passages"].add_key(*pair)
                    data["shared"].add_key(*pair)
        if edition in checkpoints:
            native_union = dict(native_docs.lengths)
            native_union.update(native_passages.lengths)
            snapshot = {"editions": edition, "document_occurrences": total_docs * edition,
                        "native_passage_occurrences": sum(map(len, base_parts)) * edition,
                        "distinct_documents_ever_changed": len(cumulative_selected),
                        "native_document_pool": native_docs.stats(),
                        "native_passage_pool": native_passages.stats(),
                        "native_shared_document_and_passage_pool": {"unique_strings": len(native_union), "unique_utf8_bytes": sum(native_union.values())},
                        "native_document_occurrence_bytes": native_doc_occurrence_bytes,
                        "native_passage_occurrence_bytes": native_passage_occurrence_bytes,
                        "changed_document_payload_bytes_across_updates": updated_doc_occurrence_bytes,
                        "changed_selected_passage_payload_bytes_across_updates": updated_passage_occurrence_bytes,
                        "metadata_modes": {}}
            for name, data in modes.items():
                snapshot["metadata_modes"][name] = {
                    "document_body_pool": data["documents"].stats(),
                    "metadata_passage_pool": data["metadata"].stats(),
                    "all_passage_body_pool": data["passages"].stats(),
                    "shared_document_and_passage_pool": data["shared"].stats(),
                    "document_occurrence_bytes": data["document_occurrence_bytes"],
                    "passage_occurrence_bytes": data["passage_occurrence_bytes"],
                    "visible_passage_occurrences": (sum(map(len, base_parts)) + (total_docs if name != "none" else 0)) * edition,
                    "source_file_changes_excluding_initial_snapshot": total_docs * (edition - 1) if name == "edition" else result["documents_changed_each_update"] * (edition - 1),
                }
            result["snapshots"].append(snapshot)
    result["largest_5_percent_initial_body_bytes"] = sum(len(separator.join(base_parts[i])) + (len(titles[i]) + 1 if titles[i] else 0) for i in largest)
    result["initial_native_body_bytes"] = initial_body_bytes
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--guavajam", type=Path, help="Required for the actual GuavaJam NAV corpus; uses production metadata text extraction.")
    args = parser.parse_args()
    raw = args.corpus.read_bytes()
    rows = [json.loads(line) for line in raw.decode().splitlines()]
    navigation = "navigation" in rows[0]
    metadata_text = None
    if navigation:
        assert args.guavajam is not None
        root = args.guavajam.resolve()
        sys.path[:0] = [str(root / "src"), str(root / "some-implementations/a-guavajam-static-website-generator/src")]
        from guavajam.supporting_metadata import metadata_block
        from a_guavajam_static_website_generator.navigation import source_text
        metadata_text = lambda values: source_text(metadata_block(list(values.items())))
    started = time.perf_counter()
    report = {"kind": "research-text-payload-churn-sensitivity", "python": platform.python_version(),
              "corpus_sha256": hashlib.sha256(raw).hexdigest(), "documents": len(rows),
              "input_kind": "actual-production-navigation" if navigation else "native-heading-extraction",
              "native_passages": sum(len(row["navigation"]["anchors"]) - 1 if navigation else len(row["passages"]) for row in rows),
              "checkpoints": [1, 5, 50], "random_seed": 729,
              "policy": {"selection": "random_5_percent samples round(N*0.05) document identities independently each update; largest_5_percent repeatedly changes the same initial largest round(N*0.05) bodies; all changes every document.",
                         "passage_change": "Both last-passage (matching storage_matrix.py) and largest-passage sensitivity are reported. The selected original passage gets a unique edition-number marker appended; later changes replace the prior marker rather than accumulate history.",
                         "metadata": "none: content only (including document title for NAV input). stable: literal Authority, Interface, Element ID and Label. edition: additionally Example version vN, changing every edition. Visible metadata is one extra passage. NAV input uses exact production metadata_block/source_text and exact title/metadata/content order; heading input retains the earlier colon/semicolon presentation model.",
                         "identity": "Exact UTF-8 body strings deduplicated by SHA-256; payload bytes exclude hashes, keys, references, SQLite pages, FTS postings/docsize, ranking fields, citation data, frontmatter syntax, compression and source-GFM segmentation overhead.",
                         "source_changes": "Count is a consequence of modeled body/metadata changes with other source fields fixed; it is not an exact serialized source-file size or hash measurement.",
                         "limits": "Synthetic editions of actual current native reference; not real Riverhog release history, database-size estimates, or browser qualification. Heading-only passage inventory remains an adapter fixture."},
              "scenarios": []}
    if navigation:
        report["policy"]["limits"] = "Synthetic editions of actual production supporting-GFM/navigation text, not real Riverhog release history, database-size estimates, or browser qualification. Flat literal paragraphs are a text-preserving benchmark adapter, not final table/link presentation."
    for passage_policy in ("last", "largest"):
        for scenario in ("random_5_percent", "largest_5_percent", "all"):
            report["scenarios"].append(model(rows, scenario, passage_policy, report["checkpoints"], navigation=navigation, metadata_text=metadata_text))
            print(scenario, passage_policy, flush=True)
    report["elapsed_seconds"] = time.perf_counter() - started
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(f"Saved {args.output}: {report['elapsed_seconds']:.3f}s", flush=True)


if __name__ == "__main__":
    main()
