#!/usr/bin/env python3
"""Verify public edition archives and audit source asset integrity."""
import argparse
import hashlib
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from asset_policy import AssetError, MAX_RECORD_BYTES, bounded_record, fetch_asset, load_policy
from backup_package import verify_backup_package
from identifier_schema import TAG, valid_utc
from editorial_notices import edition_notices
from issuance import check_authorization, check_issuance, read_registration
from publish_edition import api, gh, release_inventory, release_records, tag_commit
from public_record import EDITION_RECORDS, WORK_RECORDS, regular_record, saved_manifest
from reader_routes import verify_reader_routes, verify_work_route
from validate_sources import git_sources, parse_document, walk
from verify_edition import verify_edition


@contextmanager
def audit_check(errors, label):
    """Collect independent audit failures. Strict verification callers fail immediately."""
    try:
        yield
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if errors is None:
            raise
        errors.append(f"{label}: {exc}")


def verify_public_release(repo, release, *, root=None, require_receipt=False, errors=None):
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
        if errors is not None:
            print(f"Verified GitHub package against receipt: {tag}")
        with audit_check(errors, f"{tag} GitHub release metadata"):
            inventory = release_inventory(release, tag, manifest, names, draft=False,
                                          notices=edition_notices(root, tag), identifiers=record.get("identifiers"))
            if set(inventory) != set(names):
                raise AssetError("missing release attachments")
        with audit_check(errors, f"{tag} GitHub tag"):
            if tag_commit(repo, tag) != manifest["source_commit"]:
                raise AssetError("published edition source differs from release tag")
    return manifest


def verify_registered_routes(root, repo, manifest, published_at, archive_base):
    tag = manifest['tag']
    match = TAG.fullmatch(tag)
    path = Path('registrations/editions') / match[1] / (match[2] + '.json')
    record = read_registration(root, path)
    verify_reader_routes(manifest, repo, notices=edition_notices(root, tag), identifiers=record.get('identifiers'),
                         archive_base=archive_base, published_at=published_at, pending="published_at" not in record)


def registered_manifest(root, tag, receipt):
    match = TAG.fullmatch(tag)
    if not match or ("published_at" in receipt and not valid_utc(receipt["published_at"])):
        raise AssetError("invalid edition tag or publication timestamp")
    slug, version = match.groups()
    manifest = saved_manifest(root, slug, version, receipt)
    check_authorization(receipt, root / EDITION_RECORDS / slug / version, manifest, require_claim=True)
    return manifest


def verify_published_work(root, repo, manifest, archive_base):
    slug = TAG.fullmatch(manifest['tag'])[1]
    page = root / WORK_RECORDS / slug / 'index.html'
    verify_work_route(manifest, repo, hashlib.sha256(page.read_bytes()).hexdigest(),
                      archive_base=archive_base)


def audit(root, repo, *, archive_base=None):
    failures = []
    urls = set()
    policy = None
    with audit_check(failures, "asset policy"):
        policy = load_policy(root)
        if policy is None:
            raise AssetError("approved asset origin is required for integrity audit")
    paths = git_sources(root)
    expected = {f"works/{path.parent.name}/{path.stem}": path for path in paths
                if path.parts[:2] == ("registrations", "editions") and len(path.parts) == 4 and path.suffix == ".json"}
    for path in paths:
        if policy is None or path.suffix != ".md":
            continue
        with audit_check(failures, str(path)):
            ast, errors = parse_document(root / path, path)
            if errors:
                raise AssetError("\n".join(errors))
            for node in walk(ast["blocks"]):
                if node.get("t") in {'Image','Link'} and policy.manages(node['c'][2][0]):
                    url = node["c"][2][0]
                    policy.parse(url)
                    urls.add(url)
    releases = None
    seen = {}
    with audit_check(failures, "GitHub release inventory"):
        releases = release_records(repo)
        for release in releases:
            with audit_check(failures, "GitHub release"):
                if release["draft"] or release["prerelease"]:
                    continue
                tag = release["tag_name"]
                if tag not in expected:
                    raise AssetError(f"{tag}: missing issuance record for public release")
                if tag in seen:
                    raise AssetError(f"duplicate release for {tag}")
                seen[tag] = release
    checked_works = set()
    for tag, path in sorted(expected.items()):
        release = seen.get(tag)
        manifest = None
        if release is not None:
            with audit_check(failures, f"{tag} GitHub package"):
                manifest = verify_public_release(repo, release, root=root, require_receipt=True, errors=failures)
                published_at = release["published_at"]
        if manifest is None:
            # A broken primary cannot attest its reader. Use the reviewed local provenance and receipt.
            with audit_check(failures, f"{tag} local record"):
                receipt = read_registration(root, path)
                if "abandoned_run_id" in receipt:
                    if release is not None:
                        raise AssetError("public release exists for a confirmed abandoned claim")
                    continue
                if "cancellation_reason" in receipt:
                    continue
                published_at = receipt.get("published_at")
                if "published_at" not in receipt:
                    failures.append(f"Unresolved publication intent (complete issuance or retire the version): {tag}")
                    if receipt.get("issuing") is not True:
                        continue
                elif release is None and releases is not None:
                    failures.append(f"missing registered edition: {tag}")
                manifest = registered_manifest(root, tag, receipt)
        if manifest is None:
            continue
        with audit_check(failures, f"{tag} asset inventory"):
            urls.update(record["source_url"] for record in manifest["assets"] if record["source_kind"] == "https")
        if published_at is None:
            continue
        with audit_check(failures, f"{tag} reader"):
            verify_registered_routes(root, repo, manifest, published_at, archive_base)
            print(f"Verified reader: {tag}")
        work_tag = tag.rsplit('/', 1)[0]
        if work_tag not in checked_works:
            with audit_check(failures, f"{work_tag} work page"):
                verify_published_work(root, repo, manifest, archive_base)
                print(f"Verified work page: {work_tag}")
            checked_works.add(work_tag)
    if policy is not None:
        for url in sorted(urls):
            with audit_check(failures, f"origin asset {url}"):
                fetch_asset(policy, url)
    if failures:
        raise AssetError("\n".join(failures))
    print(f"Integrity verified: {len(urls)} origin assets; {len(seen)} independent GitHub editions")


def edition_page(root, tag):
    match = TAG.fullmatch(tag)
    if not match:
        raise AssetError("invalid edition tag")
    path = root / EDITION_RECORDS / match[1] / match[2] / "index.html"
    regular_record(path)
    with path.open("rb") as page:
        return bounded_record(page.read(MAX_RECORD_BYTES + 1))


def editorial_checkpoint(receipt, notices, page):
    """Bind the full receipt, ordered notices and approved edition page bytes."""
    state = {"schema": "press.editorial-checkpoint.v1", "receipt": receipt, "notices": notices,
             "edition_page_sha256": hashlib.sha256(page).hexdigest()}
    raw = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def committed_checkpoint(root, tag):
    from build_edition import git, snapshot

    match = TAG.fullmatch(tag)
    if not match:
        raise AssetError("invalid edition tag")
    with tempfile.TemporaryDirectory(prefix="press-editorial-") as temp:
        committed = Path(temp)
        snapshot(root, git(root, "rev-parse", "HEAD"), committed)
        path = Path("registrations/editions") / match[1] / (match[2] + ".json")
        receipt = read_registration(committed, path)
        if not valid_utc(receipt.get("published_at")):
            raise AssetError("editorial checkpoint requires a committed publication receipt")
        return editorial_checkpoint(receipt, edition_notices(committed, tag), edition_page(committed, tag))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "nashspence/press"))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--reader", type=Path, metavar="DIR", help="audit public routes from a recovered edition package")
    modes.add_argument("--backup-base", metavar="URL", help="verify a recovery host directly before changing permanent routes")
    modes.add_argument("--checkpoint", metavar="TAG", help="print the editorial SHA-256 from the committed checkout at HEAD")
    parser.add_argument("--editorial-sha256", help="current independently retained editorial checkpoint for --reader or --backup-base")
    parser.add_argument("--work-sha256", help="also verify the work page against its independently retained current SHA-256")
    parser.add_argument("--registration", type=Path, help="independently trusted publication receipt for --reader or --backup-base")
    parser.add_argument("--archive-root", type=Path, help="checkout or recovery root with the trusted notices and records/editions page for --reader or --backup-base")
    parser.add_argument("--record-target", help="expected archive base after the permanent record redirect")
    modes.add_argument("--release-id", type=int, help="verify one published GitHub release by ID")
    args = parser.parse_args()
    recovery = args.reader is not None or args.backup_base is not None
    if recovery and not all((args.registration, args.archive_root, args.editorial_sha256)):
        parser.error("recovery verification requires --registration, --archive-root and --editorial-sha256")
    if not recovery and any((args.registration, args.archive_root, args.editorial_sha256, args.work_sha256)):
        parser.error("--registration, --archive-root, --editorial-sha256 and --work-sha256 require --reader or --backup-base")
    if args.backup_base is not None and args.record_target:
        parser.error("--backup-base specifies the direct recovery target by itself")
    root = Path(__file__).resolve().parents[1]
    try:
        if args.checkpoint:
            print(committed_checkpoint(root, args.checkpoint))
        elif recovery:
            receipt = read_registration(Path.cwd(), args.registration)
            if not valid_utc(receipt.get("published_at")):
                raise AssetError("recovery verification requires the trusted receipt's original publication timestamp")
            if not args.archive_root.is_dir():
                raise AssetError("--archive-root must contain the trusted editorial history and edition page")
            tag = f"works/{receipt['work']}/{receipt['version']}"
            notices = edition_notices(args.archive_root, tag)
            page = edition_page(args.archive_root, tag)
            if editorial_checkpoint(receipt, notices, page) != args.editorial_sha256:
                raise AssetError("recovered receipt, notices or edition page differ from the trusted editorial checkpoint")
            if args.backup_base is not None:
                manifest = verify_backup_package(args.backup_base, tag, receipt)
            else:
                manifest = verify_edition(args.reader, tag=tag)
                check_authorization(receipt, args.reader, manifest)
            verify_reader_routes(manifest, args.repo, notices=notices, identifiers=receipt.get("identifiers"),
                                 archive_base=args.record_target, direct_base=args.backup_base,
                                 published_at=receipt["published_at"], checkpoint_page=page)
            if args.work_sha256:
                verify_work_route(manifest, args.repo, args.work_sha256, archive_base=args.record_target,
                                  direct_base=args.backup_base)
                print("Verified work page:", tag.rsplit("/", 1)[0])
            print("Verified backup package and reading routes:" if args.backup_base else "Verified public reading routes:", tag)
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
