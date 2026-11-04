#!/usr/bin/env python3
"""Verify public edition archives and audit source asset integrity."""
import argparse
import os
import sys
import tempfile
from pathlib import Path

from asset_policy import AssetError, fetch_asset, load_policy
from identifier_schema import TAG
from editorial_notices import edition_notices
from issuance import check_issuance, read_registration
from publish_edition import api, gh, release_inventory, release_records, tag_commit
from validate_sources import git_sources, parse_document, walk
from verify_edition import verify_edition


def verify_public_release(repo, release, *, root=None, require_receipt=False):
    if root is None:
        root = Path(__file__).resolve().parents[1]
    if release["draft"] or release["prerelease"]:
        raise AssetError("expected a published official edition")
    tag = release["tag_name"]
    match = TAG.fullmatch(tag)
    if match is None:
        raise AssetError("official release has an invalid edition tag")
    filename = match[1] + "-" + match[2] + ".html"
    names = [filename, "provenance.json", "SHA256SUMS"]
    with tempfile.TemporaryDirectory(prefix="press-audit-") as temp:
        for name in names:
            gh("release", "download", tag, "--repo", repo, "--pattern", name, "--dir", temp)
        manifest = verify_edition(Path(temp), tag=tag)
        record = check_issuance(root, Path(temp), manifest, published_at=release["published_at"],
                                require_receipt=require_receipt)
        inventory = release_inventory(release, tag, manifest, names, draft=False,
                                      notices=edition_notices(root, tag), identifiers=record.get("identifiers"))
        if set(inventory) != set(names):
            raise AssetError("missing release attachments")
        if tag_commit(repo, tag) != manifest["source_commit"]:
            raise AssetError("published edition source differs from release tag")
    return manifest


def audit(root, repo):
    policy = load_policy(root)
    urls = set()
    if policy is None:
        raise AssetError("approved asset origin is required for integrity audit")
    paths = git_sources(root)
    expected = {f"works/{path.parent.name}/{path.stem}": path for path in paths
                if path.parts[:2] == ("registrations", "editions") and len(path.parts) == 4 and path.suffix == ".json"}
    for path in paths:
        if path.suffix != ".md":
            continue
        ast, errors = parse_document(root / path, path)
        if errors:
            raise AssetError("\n".join(errors))
        for node in walk(ast["blocks"]):
            if node.get("t") == "Image" and node["c"][2][0].startswith(policy.origin + "/"):
                policy.parse(node["c"][2][0])
                urls.add(node["c"][2][0])
    unresolved = []
    seen = set()
    for release in release_records(repo):
        tag = release["tag_name"]
        if release["draft"] or release["prerelease"]:
            continue
        if tag not in expected:
            raise AssetError(f"{tag}: missing issuance record for public release")
        if tag in seen:
            raise AssetError(f"duplicate release for {tag}")
        seen.add(tag)
        manifest = verify_public_release(repo, release, root=root, require_receipt=True)
        urls.update(record["source_url"] for record in manifest["assets"]
                    if record["source_kind"] == "https")
    for tag in sorted(expected.keys() - seen):
        record = read_registration(root, expected[tag])
        if "published_at" in record:
            raise AssetError(f"missing registered edition: {tag}")
        if "cancellation_reason" not in record:
            unresolved.append(tag)
    if unresolved:
        raise AssetError("Unresolved publication intent (reconcile or explicitly cancel): "
                         + ", ".join(unresolved))
    for url in sorted(urls):
        fetch_asset(policy, url)
    print(f"Integrity verified: {len(urls)} origin assets; {len(seen)} independent GitHub editions")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "nashspence/press"))
    parser.add_argument("--release-id", type=int, help="verify one published GitHub release by ID")
    args = parser.parse_args()
    try:
        if args.release_id is None:
            audit(Path(__file__).resolve().parents[1], args.repo)
        else:
            release = api(args.repo, f"releases/{args.release_id}")
            manifest = verify_public_release(args.repo, release)
            print("Verified published edition:", manifest["tag"])
    except (AssetError, OSError, ValueError, KeyError, TypeError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
