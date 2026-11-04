#!/usr/bin/env python3
"""Trusted scheduled audit of origin assets and independent GitHub edition copies."""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from asset_policy import AssetError, fetch_asset, load_policy
from publish_edition import gh, release_records, tag_commit
from validate_sources import git_sources, parse_document, walk
from verify_edition import verify_edition


def audit(root, repo):
    policy = load_policy(root)
    urls = set()
    if policy is None:
        raise AssetError("approved asset origin is required for integrity audit")
    for path in git_sources(root):
        if path.suffix != ".md":
            continue
        ast, errors = parse_document(root / path, path)
        if errors:
            raise AssetError("\n".join(errors))
        for node in walk(ast["blocks"]):
            if node.get("t") == "Image" and node["c"][2][0].startswith(policy.origin + "/"):
                policy.parse(node["c"][2][0])
                urls.add(node["c"][2][0])
    count = 0
    for release in release_records(repo):
        if release["draft"] or release["prerelease"]:
            continue
        tag = release["tag_name"]
        from build_edition import TAG
        match = TAG.fullmatch(tag)
        if match is None:
            raise AssetError("official release has an invalid edition tag")
        filename = match[1] + "-" + match[2] + ".html"
        with tempfile.TemporaryDirectory(prefix="press-audit-") as temp:
            for name in [filename, "provenance.json", "SHA256SUMS"]:
                gh("release", "download", tag, "--repo", repo, "--pattern", name, "--dir", temp)
            manifest = verify_edition(Path(temp), tag=tag)
            if tag_commit(repo, tag) != manifest["source_commit"]:
                raise AssetError("published edition source differs from release tag")
            urls.update(record["source_url"] for record in manifest["assets"]
                        if record["source_kind"] == "https")
        count += 1
    for url in sorted(urls):
        fetch_asset(policy, url)
    print(f"Integrity verified: {len(urls)} origin assets; {count} independent GitHub editions")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "nashspence/press"))
    args = parser.parse_args()
    try:
        audit(Path(__file__).resolve().parents[1], args.repo)
    except (AssetError, OSError, ValueError, KeyError, TypeError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
