#!/usr/bin/env python3
"""Verify public edition archives and audit source asset integrity."""
import argparse
import os
import sys
import tempfile
from pathlib import Path

from asset_policy import AssetError, fetch_asset, load_policy
from identifier_schema import TAG, valid_utc
from editorial_notices import edition_notices
from issuance import check_authorization, check_issuance, read_registration
from publish_edition import api, gh, release_inventory, release_records, tag_commit
from reader_routes import verify_reader_routes
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


def verify_registered_routes(root, repo, manifest, published_at, archive_base):
    tag = manifest['tag']
    match = TAG.fullmatch(tag)
    path = Path('registrations/editions') / match[1] / (match[2] + '.json')
    record = read_registration(root, path)
    verify_reader_routes(manifest, repo, notices=edition_notices(root, tag), identifiers=record.get('identifiers'),
                         archive_base=archive_base, published_at=published_at)


def audit(root, repo, *, archive_base=None):
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
        verify_registered_routes(root, repo, manifest, release["published_at"], archive_base)
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
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--reader", type=Path, metavar="DIR", help="audit public routes from a recovered edition package")
    parser.add_argument("--registration", type=Path, help="independently trusted publication receipt for --reader")
    parser.add_argument("--archive-root", type=Path, help="checkout or recovery root with the trusted notices/ directory for --reader")
    parser.add_argument("--record-target", help="expected archive base after the permanent record redirect")
    modes.add_argument("--release-id", type=int, help="verify one published GitHub release by ID")
    args = parser.parse_args()
    if args.reader and (not args.registration or not args.archive_root):
        parser.error("--reader requires --registration and --archive-root")
    if not args.reader and (args.registration or args.archive_root):
        parser.error("--registration and --archive-root require --reader")
    root = Path(__file__).resolve().parents[1]
    try:
        if args.reader:
            manifest = verify_edition(args.reader)
            receipt = read_registration(Path.cwd(), args.registration)
            check_authorization(receipt, args.reader, manifest)
            if not valid_utc(receipt.get("published_at")):
                raise AssetError("--reader requires the trusted receipt's original publication timestamp")
            if not args.archive_root.is_dir():
                raise AssetError("--archive-root must be the existing root of the trusted notice history")
            notices = edition_notices(args.archive_root, manifest["tag"])
            verify_reader_routes(manifest, args.repo, notices=notices, identifiers=receipt.get("identifiers"), archive_base=args.record_target,
                                 published_at=receipt["published_at"])
            print("Verified public record and reading copy:", manifest["tag"])
        elif args.release_id is None:
            audit(root, args.repo, archive_base=args.record_target)
        else:
            release = api(args.repo, f"releases/{args.release_id}")
            manifest = verify_public_release(args.repo, release)
            verify_registered_routes(root, args.repo, manifest, release["published_at"], args.record_target)
            print("Verified published edition:", manifest["tag"])
    except (AssetError, OSError, ValueError, KeyError, TypeError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
