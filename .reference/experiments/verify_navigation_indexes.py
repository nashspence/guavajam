#!/usr/bin/env python3
"""Independently verify actual-NAV search indexes and export browser cases.

Uses the existing research query/facet compilers; no database is modified.
Source fidelity is checked directly against every production-NAV corpus record.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import sqlite3
import time
from pathlib import Path

import metadata_facets
import query_pipeline


def save(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def source_fidelity(database: Path, corpus: Path) -> dict:
    raw = corpus.read_bytes()
    rows = [json.loads(line) for line in raw.decode().splitlines()]
    db = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    count = db.execute("SELECT count(*) FROM documents WHERE edition_id=1").fetchone()[0]
    assert count == len(rows)
    documents = {record['path']: record for record in db.execute('SELECT * FROM documents WHERE edition_id=1')}
    bodies = {record['document_id']: record['body'] for record in db.execute('''SELECT r.document_id,f.body
        FROM search_records r JOIN search_fts f ON f.rowid=r.id WHERE r.edition_id=1''')}
    anchors_by_document = defaultdict(list)
    for record in db.execute('''SELECT p.document_id,p.anchor,p.title,p.kind,p.level,p.position,p.parent,p.scope,f.body
        FROM passages p JOIN passage_fts f ON f.rowid=p.id WHERE p.edition_id=1 ORDER BY p.document_id,p.position'''):
        anchors_by_document[record['document_id']].append(tuple(record)[1:])
    facets_by_document = defaultdict(set)
    for record in db.execute('''SELECT f.document_id,v.name,v.value FROM document_facets f
        JOIN facet_values v ON v.id=f.facet_id JOIN documents d ON d.id=f.document_id WHERE d.edition_id=1'''):
        facets_by_document[record['document_id']].add((record['name'],record['value']))
    digest_mismatches = 0
    total_anchors = 0
    for row in rows:
        document = documents.get(row['path'])
        assert document is not None
        assert (document["title"], document["abstract"], document["language"]) == (row["title"], row["abstract"], "en")
        expected_source_sha = row["source_markdown_sha256"]
        digest_mismatches += document["source_sha256"] != expected_source_sha
        body = bodies[document['id']]
        assert body == row["navigation"]["text"]
        actual = anchors_by_document[document['id']]
        expected = [tuple(anchor[key] for key in ("anchor", "title", "kind", "level", "position", "parent", "scope", "text")) for anchor in row["navigation"]["anchors"]]
        assert [tuple(record) for record in actual] == expected, row["id"]
        total_anchors += len(expected)
        actual_pairs = facets_by_document[document['id']]
        expected_pairs = {(name, value) for name, raw_value in row["metadata"].items()
                          for value in (raw_value if isinstance(raw_value, list) else [raw_value])}
        assert actual_pairs == expected_pairs, row["id"]
    db.close()
    return {"database": str(database), "corpus_sha256": hashlib.sha256(raw).hexdigest(),
            "documents_checked": len(rows), "anchors_checked": total_anchors,
            "document_titles_abstracts_languages_exact": True, "full_document_text_exact": True,
            "all_anchor_text_title_kind_level_position_parent_scope_exact": True,
            "all_metadata_memberships_equal_original_nav_corpus": True,
            "source_sha256_matches_actual_markdown": digest_mismatches == 0,
            "modeled_source_digest_mismatches": digest_mismatches,
            "source_identity_scope": "Every first-edition document is checked against the real supporting-Markdown SHA-256 retained by the production parser fixture.",
            "limitations": (["This older benchmark stores a modeled content digest in source_sha256 instead of exact serialized-source identity."] if digest_mismatches else [])
                + ["This comparison checks initial-edition source-byte identity; later modeled editions are serialized by the benchmark builder, not claimed as real Riverhog release history.",
                   "Edition revisions, work-level fields, publication dates, and citation URLs remain explicitly modeled benchmark context, not signed publication artifacts."]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("browser_selection", type=Path)
    parser.add_argument("--repetitions", type=int, default=1)
    args = parser.parse_args()
    baseline = args.directory / "baseline.sqlite"
    started = time.perf_counter()
    fidelity = source_fidelity(baseline, args.corpus)
    save(args.directory / "source-fidelity.json", fidelity)
    print("Checked all source NAV records and metadata memberships", flush=True)
    query_counts = {}
    for label in ("baseline", "compact", "segmented"):
        path = args.directory / (label + ".sqlite")
        report, cases = query_pipeline.run(path, args.repetitions, None if label == "baseline" else baseline)
        save(args.directory / ("query-" + label + ".json"), report)
        query_counts[label] = len(report["cases"])
        if label == "baseline":
            save(args.directory / "query-cases.json", cases)
        print(label, len(report["cases"]), "query cases passed", flush=True)
    reference = json.loads(args.browser_selection.read_text())
    db = sqlite3.connect(f"file:{baseline.resolve()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    selected = []
    selection_manifest = []
    parameter_names = {"kind", "term", "edition", "document", "supporting", "sort", "page", "keyword", "subject"}
    for previous in reference:
        params = {key: value for key, value in previous.items() if key in parameter_names}
        case = query_pipeline.query_case(**params)
        count = query_pipeline.execute(db, case["baseline_count"])[0]["total"]
        expected = query_pipeline.execute(db, case["baseline_page"])
        optimized = query_pipeline.execute(db, case["optimized_page"])
        assert query_pipeline.canonical_rows(expected) == query_pipeline.canonical_rows(optimized)
        selected.append({"label": previous["label"], **case, "expected_count": count, "expected_rows": expected})
        selection_manifest.append({"label": previous["label"], **params, "old_fixture_count": previous["expected_count"], "actual_nav_count": count})
    db.close()
    save(args.directory / "browser-cases.json", selected)
    save(args.directory / "browser-cases-selection.json", selection_manifest)
    print("Exported", len(selected), "fresh actual-NAV browser cases", flush=True)
    facets = metadata_facets.run(baseline, args.repetitions)
    save(args.directory / "metadata-facets.json", facets)
    print("Facet cases", len(facets["cases"]), "passed", flush=True)
    summary = {"elapsed_seconds": time.perf_counter() - started,
               "query_cases_per_variant": query_counts, "query_variants": list(query_counts),
               "browser_cases": len(selected), "facet_cases": len(facets["cases"]),
               "source_fidelity": fidelity}
    save(args.directory / "verification-summary.json", summary)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
